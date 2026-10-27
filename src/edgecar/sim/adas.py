"""Simulated ADAS (adaptive cruise + lane keeping) on real trips, calibrated on real ADAS usage.

The datasets' vehicles record no ADAS channel, so engagement is simulated on the *real* speed profiles, with timing
drawn from measured human–ADAS handovers:

* **Episode length** (engaged → disengaged) and **re-engagement delay** are sampled from the empirical
  distributions of BATON (OpenLKA, CC BY-NC 4.0): 1,800 activations and 1,793 takeovers over 565 real drives,
  162 h, 150 drivers, 99 vehicle models — median engaged episode 53 s, median re-engagement delay 15 s,
  20.2 takeovers per ADAS-active hour.
* An episode ends early for causes **read from the real data**: braking harder than ACC commands itself (the driver
  pressed the brake), or standing still longer than the stop-and-go hold time. Without these triggers the sampled
  episodes alone give ≈ 22 disengagements per engaged hour, close to BATON's measured 20.2; the triggers add only
  the clear cases.
* BATON carries no cause labels, so episodes that run their sampled length end with a cause drawn from stated
  priors (``CAUSE_PRIORS``) — driver steering override most often; lane-marking loss (more likely below 0 °C);
  sensor blockage (only below −2 °C); or a system takeover request whose reaction time and outcome are drawn from
  the 500 measured takeovers in TD2D (CARLA, 50 drivers).

The simulator emits **wire traffic only**: each state change becomes a SOME/IP notification of the ADAS service
(0x1A01/0x8001). Events are created by the ingestion adapter that decodes them (``edgecar.ingest.adapters``).
Generation is seeded per trip, so a rebuild produces identical traffic.
"""

from __future__ import annotations

import glob
import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from edgecar.ingest.adapters import ADAS_CAUSES, CRITICAL_SITUATIONS, NO_AMBIENT
from edgecar.protocols import someip
from edgecar.store import Store

ENGAGE_MIN_KMH = 30.0  # the driver can engage above this speed
STANDSTILL_HOLD_S = 30  # stop-and-go ACC holds the car at a standstill, then hands control back
DRIVER_BRAKE_MPS2 = -3.5  # beyond what ACC commands itself (~ -3 m/s²): the driver braked
SENSOR_BLOCK_C = -2.0

# Assumed shares of episode-ending causes when no cause is visible in the data (BATON has no cause labels).
CAUSE_PRIORS = {"driver_steer": 0.70, "lane_markings_lost": 0.15, "tor": 0.10, "sensor_blocked": 0.05}

PROVENANCE = "sim:adas (BATON timing, TD2D takeovers)"
DISENGAGED, ENGAGED, TAKEOVER_REQUEST = 0, 1, 2


@dataclass(frozen=True)
class Calibration:
    episode_s: np.ndarray  # engaged-episode durations
    gap_s: np.ndarray  # delays before re-engaging
    takeovers: pd.DataFrame  # TD2D: reaction time (ms) and outcome per case


@dataclass(frozen=True)
class AdasChange:
    """One ADAS state change — exactly the fields carried in the SOME/IP notification."""

    ts: pd.Timestamp
    state: int
    cause: str
    speed_kmh: float
    value: int  # cause-specific: deceleration [cm/s²], reaction time [ms], or critical-situation index
    ambient_c: int  # NO_AMBIENT when unknown

    def to_someip(self, session: int) -> bytes:
        payload = someip.ADAS_PAYLOAD.pack(
            self.state, ADAS_CAUSES.index(self.cause), round(self.speed_kmh * 100), self.value, self.ambient_c
        )
        return someip.encode(
            someip.Message(someip.ADAS_SERVICE, someip.ADAS_STATE_EVENT, payload, session_id=session & 0xFFFF or 1)
        )


def load_calibration(baton_dir: Path, td2d_dir: Path) -> Calibration:
    act = pd.read_csv(Path(baton_dir) / "activation_events_or.csv").assign(kind="on")
    tko = pd.read_csv(Path(baton_dir) / "takeover_events_or.csv").assign(kind="off")
    ev = pd.concat([act, tko]).sort_values(["route_id", "event_time_sec"])
    episodes, gaps = [], []
    for _, g in ev.groupby("route_id"):
        kind, t = g["kind"].to_numpy(), g["event_time_sec"].to_numpy()
        for i in range(len(g) - 1):
            if kind[i] == "on" and kind[i + 1] == "off":
                episodes.append(t[i + 1] - t[i])
            elif kind[i] == "off" and kind[i + 1] == "on":
                gaps.append(t[i + 1] - t[i])
    rows = []
    for f in glob.glob(str(Path(td2d_dir) / "TD2D" / "D*" / "*" / "takeoverScenarioInformations.csv")):
        d = pd.read_csv(f)
        d["condition"] = Path(f).parent.name
        rows.append(d[["criticalEventType", "reactionTime", "takeoverResult", "condition"]])
    return Calibration(np.array(episodes), np.array(gaps), pd.concat(rows, ignore_index=True))


def _rng(trip_id: str) -> np.random.Generator:
    return np.random.default_rng(int(hashlib.sha256(trip_id.encode()).hexdigest()[:12], 16))


def simulate_trip(f: pd.DataFrame, cal: Calibration) -> list[AdasChange]:
    rng = _rng(f["trip_id"].iloc[0])
    speed = f["speed_kmh"].to_numpy()
    accel = f["accel_mps2"].to_numpy() if f["accel_mps2"].notna().any() else np.r_[0, np.diff(speed) / 3.6]
    ambient = f["ambient_temp_c"].to_numpy()
    ts = pd.to_datetime(f["ts"]).to_numpy()
    out: list[AdasChange] = []
    engaged, stopped_for, remaining = False, 0, 0.0
    wait = float(rng.choice(cal.gap_s))  # seconds of eligible driving before the driver engages

    def change(i: int, state: int, cause: str, value: int = 0, at=None) -> None:
        amb = ambient[i]
        out.append(
            AdasChange(
                pd.Timestamp(at if at is not None else ts[i]),
                state,
                cause,
                float(speed[i]),
                value,
                NO_AMBIENT if np.isnan(amb) else int(round(amb)),
            )
        )

    for i in range(len(f)):
        v = speed[i]
        if np.isnan(v):
            continue
        if not engaged:
            if v >= ENGAGE_MIN_KMH:
                wait -= 1
                if wait <= 0:
                    engaged, stopped_for, remaining = True, 0, float(rng.choice(cal.episode_s))
                    change(i, ENGAGED, "none")
            continue
        remaining -= 1
        stopped_for = stopped_for + 1 if v < 1.0 else 0
        cause, value = None, 0
        if accel[i] <= DRIVER_BRAKE_MPS2:
            cause, value = "driver_brake", min(65535, round(-accel[i] * 100))
        elif stopped_for >= STANDSTILL_HOLD_S:
            cause = "standstill_hold"
        elif remaining <= 0:
            priors = dict(CAUSE_PRIORS)
            amb = ambient[i]
            if not np.isnan(amb) and amb < 0:
                priors["lane_markings_lost"] *= 2.0  # slush and salt on lane markings
            if np.isnan(amb) or amb >= SENSOR_BLOCK_C:
                priors.pop("sensor_blocked")
            keys = list(priors)
            p = np.array([priors[k] for k in keys])
            cause = keys[rng.choice(len(keys), p=p / p.sum())]
            if cause == "tor":
                case = cal.takeovers.iloc[rng.integers(len(cal.takeovers))]
                change(i, TAKEOVER_REQUEST, "system_limit", CRITICAL_SITUATIONS.index(case["criticalEventType"]))
                rt_ms = int(case["reactionTime"])
                late = case["takeoverResult"] != "Success"
                change(
                    i,
                    DISENGAGED,
                    "takeover_late" if late else "takeover_completed",
                    rt_ms,
                    at=pd.Timestamp(ts[i]) + pd.Timedelta(milliseconds=rt_ms),
                )
                engaged, wait = False, float(rng.choice(cal.gap_s))
                continue
        if cause:
            change(i, DISENGAGED, cause, value)
            engaged, wait = False, float(rng.choice(cal.gap_s))
    return out


def simulate(store: Store, cal: Calibration, vehicle_id: str) -> list[AdasChange]:
    trips = store.sql(
        "SELECT trip_id FROM trips WHERE vehicle_id = ? AND max_speed_kmh >= ? ORDER BY start_ts",
        [vehicle_id, ENGAGE_MIN_KMH],
    )
    out: list[AdasChange] = []
    for trip_id in trips["trip_id"]:
        f = store.sql(
            "SELECT ts, trip_id, speed_kmh, accel_mps2, ambient_temp_c FROM frames WHERE trip_id = ? ORDER BY ts",
            [trip_id],
        )
        out.extend(simulate_trip(f, cal))
    return out


def to_pcap(changes: list[AdasChange], path: Path) -> int:
    """Write the changes as the SOME/IP notifications the ADAS ECU would send (Wireshark-readable)."""
    records = [(c.ts.value / 1e9, c.to_someip(session=i + 1)) for i, c in enumerate(changes)]
    return someip.write_pcap(records, path)
