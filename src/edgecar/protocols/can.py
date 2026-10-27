"""CAN: canonical signals ↔ real CAN frames, using a vehicle's DBC database.

The Nissan Leaf mapping below uses the community-reverse-engineered Leaf DBC files
(github.com/dalathegreat/leaf_can_bus_messages, GPL-3.0 — fetched at setup, never vendored).

Calibration note: the DBC marks the heater scaling as uncertain ("300W/bit?"). Every Leaf heater reading in the
Vehicle Energy Dataset is a multiple of 250 W (only 73 % are multiples of 300 W), while current, voltage, outside
temperature and A/C power match the DBC resolutions exactly (0.5 A, 0.5 V, 0.5 °C, 50 W). The dataset was evidently
decoded from these same signals, so the heater is mapped at 250 W/bit and the round trip is lossless.

On the vehicle, ingestion reads frames from a SocketCAN interface opened in **listen-only** mode (no transmit path
exists in the AI zone); here frames are written to and read from candump-format logs (``(ts) channel ID#DATA``).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import cantools
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SignalMap:
    canonical: str
    bus: str  # logical bus name, also used as the candump channel
    message: str  # DBC message name
    signal: str  # DBC signal name
    scale: float = 1.0  # canonical = dbc_physical × scale
    invert: bool = False  # canonical = −(…) — Leaf reports discharge current as negative


LEAF_SIGNALS: tuple[SignalMap, ...] = (
    SignalMap("speed_kmh", "carcan", "x355_CLUSTER_BasicInfo_2", "VehicleSpeedToVCM"),
    SignalMap("batt_current_a", "evcan", "x1DB", "LB_Current", invert=True),
    SignalMap("batt_voltage_v", "evcan", "x1DB", "LB_Total_Voltage"),
    SignalMap("soc_pct", "evcan", "x55B", "LB_SOC", scale=0.1),  # 0…1000 → 0.0…100.0 %
    SignalMap("ambient_temp_c", "evcan", "x54C", "OutsideAmbientTemperature"),
    SignalMap("hvac_heater_kw", "evcan", "x54F", "HeaterPowerConsumption", scale=0.25),  # 250 W/bit (see above)
    SignalMap("hvac_ac_kw", "evcan", "x54F", "ACPowerConsumption", scale=0.05),  # 50 W/bit
)

LEAF_DBC_FILES = {"evcan": "EV-can_AZE0.dbc", "carcan": "CAR-can_AZE0.dbc"}

TRIP_GAP_S = 120  # bus silence longer than this ends a trip (ignition off)


@dataclass
class Frame:
    ts: float  # POSIX seconds
    channel: str
    arbitration_id: int
    data: bytes


class CanCodec:
    def __init__(
        self, dbc_dir: Path, dbc_files: dict[str, str] = LEAF_DBC_FILES, signals: tuple[SignalMap, ...] = LEAF_SIGNALS
    ) -> None:
        self.db = {
            bus: cantools.database.load_file(str(Path(dbc_dir) / f), strict=False) for bus, f in dbc_files.items()
        }
        self.signals = signals
        self.by_message: dict[tuple[str, str], list[SignalMap]] = defaultdict(list)
        for s in signals:
            self.by_message[(s.bus, s.message)].append(s)
        self.by_id = {(bus, self.db[bus].get_message_by_name(msg).frame_id): (bus, msg) for bus, msg in self.by_message}

    # ------------------------------------------------------------------------------------------------- encode

    def encode(self, frames: pd.DataFrame) -> Iterator[Frame]:
        """Yield one CAN frame per (message, second) for which at least one mapped signal has a value."""
        ts = (frames["ts"].astype("datetime64[ns]").astype("int64") / 1e9).to_numpy()
        for (bus, msg_name), maps in self.by_message.items():
            msg = self.db[bus].get_message_by_name(msg_name)
            cols = [m.canonical for m in maps if m.canonical in frames]
            if not cols:
                continue
            values = frames[cols].to_numpy(dtype=float)
            present = ~np.isnan(values).all(axis=1)
            base = {s.name: 0 for s in msg.signals}
            for i in np.flatnonzero(present):
                payload = dict(base)
                for j, m in enumerate(m for m in maps if m.canonical in frames):
                    v = values[i, j]
                    if np.isnan(v):
                        continue
                    phys = (-v if m.invert else v) / m.scale
                    sig = msg.get_signal_by_name(m.signal)
                    # quantise to the signal's resolution so encoding never fails on float noise
                    payload[m.signal] = round((phys - sig.offset) / sig.scale) * sig.scale + sig.offset
                yield Frame(float(ts[i]), bus, msg.frame_id, msg.encode(payload, strict=False))

    # ------------------------------------------------------------------------------------------------- decode

    def decode(self, frames: Iterable[Frame]) -> pd.DataFrame:
        """Decode frames into canonical signals, one row per second."""
        rows: dict[float, dict] = defaultdict(dict)
        for f in frames:
            key = self.by_id.get((f.channel, f.arbitration_id))
            if key is None:
                continue  # a frame this application does not use: ignored, never answered
            bus, msg_name = key
            decoded = self.db[bus].get_message_by_name(msg_name).decode(f.data, decode_choices=False)
            row = rows[round(f.ts, 3)]
            for m in self.by_message[key]:
                v = float(decoded[m.signal]) * m.scale
                row[m.canonical] = -v if m.invert else v
        df = pd.DataFrame.from_dict(rows, orient="index").sort_index()
        df.index = pd.to_datetime(df.index, unit="s")
        df.index.name = "ts"
        df = df.reset_index()
        if {"batt_voltage_v", "batt_current_a"} <= set(df.columns):
            df["batt_power_kw"] = df["batt_voltage_v"] * df["batt_current_a"] / 1000.0
        return df


def segment_trips(df: pd.DataFrame, vehicle_id: str, gap_s: int = TRIP_GAP_S) -> pd.DataFrame:
    """Assign trip ids from bus activity: a gap longer than ``gap_s`` means the vehicle was switched off."""
    df = df.sort_values("ts").reset_index(drop=True)
    new_trip = df["ts"].diff().dt.total_seconds().fillna(np.inf) > gap_s
    starts = df["ts"].where(new_trip).ffill()
    df.insert(1, "vehicle_id", vehicle_id)
    df.insert(2, "trip_id", vehicle_id + ":" + starts.dt.strftime("%Y%m%d-%H%M%S"))
    return df


# ---------------------------------------------------------------------------------------------- candump logs


def write_log(frames: Iterable[Frame], path: Path) -> int:
    """Write frames in candump ``-L`` format: ``(1509526283.123456) evcan 1DB#00112233445566FF``."""
    n = 0
    with open(path, "w", encoding="ascii", newline="\n") as fh:
        for f in sorted(frames, key=lambda f: (f.ts, f.channel, f.arbitration_id)):
            fh.write(f"({f.ts:.6f}) {f.channel} {f.arbitration_id:03X}#{f.data.hex().upper()}\n")
            n += 1
    return n


def read_log(path: Path) -> Iterator[Frame]:
    with open(path, encoding="ascii") as fh:
        for line in fh:
            ts, channel, frame = line.split()
            can_id, data = frame.split("#")
            yield Frame(float(ts.strip("()")), channel, int(can_id, 16), bytes.fromhex(data))
