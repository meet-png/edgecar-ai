"""Randomised battery over-temperature scenarios with distractor events, for testing root-cause analysis.

Each scenario takes a random real BMW i3 trip, a random fault (kind, onset, severity — see ``thermal.Fault``) and
adds 2–6 *distractors* at random times, often before the real fault: faults from unrelated subsystems (lost CAN
communication, 12 V low, an infotainment watchdog reset, the drive electronics' coolant pump) and decoys from the
same subsystems as real causes (transient sensor glitches in the thermal manager or the BMS). A rule like "the
earliest fault in the window is the cause" falls for both; the fault-propagation map alone falls for the decoys.
Everything is emitted as wire traffic and decoded by the same adapters as the demo store.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from edgecar.protocols import uds
from edgecar.sim import adas, thermal

# (kind, …): faults that did not cause the over-temperature
DISTRACTORS = (
    ("dtc", "GW", "U0155"),  # lost communication with the instrument cluster
    ("dtc", "GW", "U0121"),  # lost communication with the ABS module
    ("dtc", "VCU", "P0562"),  # 12 V system voltage low
    ("dtc", "VCU", "P0C73"),  # drive-electronics coolant pump: a different cooling circuit on this car
    ("qnx", "media_svc", 233, "audio", "audio DSP watchdog expired, restarting DSP"),
    ("qnx", "nav_gnss", 154, "gnss", "fix lost: 0 satellites in view"),
    ("qnx", "hmi_svc", 301, "render", "frame time above 50 ms for 5 s"),
    ("adas", "lane_markings_lost"),
    # decoys from the *same* subsystems as real causes: transient glitches that recover on their own
    ("qnx", "thermal_mgr", 412, "refrig", "p_high sensor implausible (31.8 bar), recovered after 2 s"),
    ("qnx", "bms_thermal", 377, "cellgrp", "cell voltage sensor timeout on group 3, recovered"),
)


@dataclass
class Scenario:
    seed: int
    base_trip: str
    fault: thermal.Fault
    traffic: thermal.ScenarioTraffic
    distractors: list[tuple[pd.Timestamp, str]]


def random_fault(rng: np.random.Generator) -> thermal.Fault:
    kind = str(rng.choice(thermal.KINDS))
    r = rng.uniform(0.30, 0.60) if kind == "cell_degradation" else rng.uniform(0.10, 0.25)
    return thermal.Fault(kind, int(rng.integers(120, 1500)), round(float(r), 3), int(rng.integers(1, 13)))


def add_distractors(tr: thermal.ScenarioTraffic, rng: np.random.Generator) -> list[tuple[pd.Timestamp, str]]:
    """Unrelated faults in the 30 minutes before the warning, never within 30 s of the real fault."""
    t_warn = max(t for t, code in tr.truth if code == "BATT_OVERTEMP_WARNING")
    t0 = max(tr.frames["ts"].iloc[0], t_warn - pd.Timedelta(minutes=30))
    span = (t_warn - t0).total_seconds()
    out: list[tuple[pd.Timestamp, str]] = []
    for i in rng.choice(len(DISTRACTORS), size=int(rng.integers(2, 7)), replace=False):
        d = DISTRACTORS[int(i)]
        while True:
            at = t0 + pd.Timedelta(seconds=float(rng.uniform(0, span - 5)))
            if abs((at - tr.fault_time).total_seconds()) > 30:
                break
        if d[0] == "dtc":
            tr.diag_frames += thermal._uds_poll(at, d[1], [uds.DtcRecord(d[2], 0x00, thermal.STATUS_CONFIRMED)])
            out.append((at, d[2]))
        elif d[0] == "qnx":
            tr.slog2_lines.append(thermal._log(at, d[1], d[2], d[3], 3, d[4]))
            out.append((at, f"QNX_{d[1].upper()}"))
        else:
            speed = float(np.interp(at.value, tr.frames["ts"].astype("int64"), tr.frames["speed_kmh"].fillna(0)))
            change = adas.AdasChange(at, adas.DISENGAGED, d[1], speed, 0, adas.NO_AMBIENT)
            tr.someip_records.append((at.value / 1e9, change.to_someip(len(out) + 1)))
            out.append((at, "ADAS_DISENGAGE_LANE_LOST"))
    tr.diag_frames.sort(key=lambda f: f.ts)
    tr.someip_records.sort()
    return out


def random_scenario(seed: int, trips: dict[str, pd.DataFrame]) -> Scenario:
    """A scenario that actually reaches the warning: faults that never get there on a trip are re-drawn."""
    rng = np.random.default_rng(seed)
    names = sorted(trips)
    for _ in range(200):
        trip = names[int(rng.integers(len(names)))]
        fault = random_fault(rng)
        try:
            tr = thermal.battery_overheat(
                trips[trip], fault, trip_id=f"i3-tum:R{seed:04d}", start=trips[trip]["ts"].min()
            )
        except ValueError:
            continue
        return Scenario(seed, trip, fault, tr, add_distractors(tr, rng))
    raise RuntimeError(f"seed {seed}: no trip/fault combination reached the warning")
