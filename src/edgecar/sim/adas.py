"""Simulated ADAS (adaptive cruise + lane keeping) driven by real speed profiles.

The datasets' vehicles record no ADAS channel, so engagement is simulated on the *real* trips:

* the system engages after ``ENGAGE_HOLD_S`` seconds above ``ENGAGE_KMH``;
* it disengages for causes visible in the real data — the driver braking hard, or speed falling below the
  ACC minimum — or for causes sampled per kilometre: lane markings lost (more likely in sub-zero weather),
  camera/radar blockage (only below ``SENSOR_BLOCK_C``), or a takeover request;
* takeover reaction time and outcome are drawn from the 500 measured takeovers in TD2D (CARLA, 50 drivers).

Generation is seeded per trip, so a rebuild produces identical events.
"""

from __future__ import annotations

import glob
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from edgecar.store import Store

ENGAGE_KMH = 60.0
ENGAGE_HOLD_S = 20
MIN_SPEED_KMH = 30.0  # ACC drops out below this
DRIVER_BRAKE_MPS2 = -2.0  # deceleration that implies the driver pressed the brake
REENGAGE_AFTER_S = 45
LANE_LOSS_PER_KM = 0.012
LANE_LOSS_COLD_FACTOR = 3.0  # slush / salt on markings below freezing
SENSOR_BLOCK_C = -2.0
SENSOR_BLOCK_PER_KM = 0.02
TAKEOVER_REQUEST_PER_KM = 0.006

PROVENANCE = "sim:adas"


def load_td2d(td2d_dir: Path) -> pd.DataFrame:
    """Measured takeovers: reaction time (ms) and outcome per case."""
    rows = []
    for f in glob.glob(str(Path(td2d_dir) / "TD2D" / "D*" / "*" / "takeoverScenarioInformations.csv")):
        d = pd.read_csv(f)
        d["condition"] = Path(f).parent.name
        rows.append(d[["criticalEventType", "reactionTime", "takeoverResult", "condition"]])
    if not rows:
        raise FileNotFoundError(f"no TD2D takeover files under {td2d_dir}")
    return pd.concat(rows, ignore_index=True)


def _rng(trip_id: str) -> np.random.Generator:
    return np.random.default_rng(int(hashlib.sha256(trip_id.encode()).hexdigest()[:12], 16))


def _event(ts, trip_id, vehicle_id, code, severity, title, detail) -> dict:
    return {
        "event_id": f"{trip_id}:adas:{pd.Timestamp(ts):%H%M%S}:{code}",
        "ts": ts,
        "vehicle_id": vehicle_id,
        "trip_id": trip_id,
        "category": "adas",
        "code": code,
        "severity": severity,
        "source": "ADAS (SOME/IP)",
        "title": title,
        "detail": json.dumps(detail),
        "provenance": PROVENANCE,
    }


def simulate_trip(f: pd.DataFrame, takeovers: pd.DataFrame) -> list[dict]:
    trip_id, vehicle_id = f["trip_id"].iloc[0], f["vehicle_id"].iloc[0]
    rng = _rng(trip_id)
    speed = f["speed_kmh"].to_numpy()
    accel = f["accel_mps2"].to_numpy() if f["accel_mps2"].notna().any() else np.r_[0, np.diff(speed) / 3.6]
    ambient = f["ambient_temp_c"].to_numpy()
    ts = f["ts"].to_numpy()
    events: list[dict] = []
    engaged, above_for, cooldown = False, 0, 0
    for i in range(len(f)):
        v = speed[i]
        if np.isnan(v):
            engaged, above_for = False, 0
            continue
        if not engaged:
            cooldown = max(0, cooldown - 1)
            above_for = above_for + 1 if v >= ENGAGE_KMH else 0
            if above_for >= ENGAGE_HOLD_S and cooldown == 0:
                engaged = True
                events.append(
                    _event(
                        ts[i],
                        trip_id,
                        vehicle_id,
                        "ADAS_ENGAGED",
                        "info",
                        f"Adaptive cruise + lane keeping engaged at {v:.0f} km/h",
                        {"speed_kmh": round(float(v), 1)},
                    )
                )
            continue
        km = v / 3600.0
        cold = not np.isnan(ambient[i]) and ambient[i] < 0
        cause = None
        if accel[i] <= DRIVER_BRAKE_MPS2:
            cause = (
                "ADAS_DISENGAGE_DRIVER_BRAKE",
                "info",
                f"Disengaged: driver braked ({accel[i]:.1f} m/s²)",
                {"cause": "driver_override_brake", "decel_mps2": round(float(accel[i]), 2)},
            )
        elif v < MIN_SPEED_KMH:
            cause = (
                "ADAS_DISENGAGE_LOW_SPEED",
                "info",
                f"Disengaged: speed fell to {v:.0f} km/h",
                {"cause": "below_min_speed"},
            )
        elif not np.isnan(ambient[i]) and ambient[i] < SENSOR_BLOCK_C and rng.random() < SENSOR_BLOCK_PER_KM * km:
            cause = (
                "ADAS_DISENGAGE_SENSOR_BLOCKED",
                "warning",
                "Disengaged: front radar/camera blocked (ice or snow)",
                {"cause": "sensor_blocked", "ambient_c": round(float(ambient[i]), 1)},
            )
        elif rng.random() < LANE_LOSS_PER_KM * km * (LANE_LOSS_COLD_FACTOR if cold else 1.0):
            cause = (
                "ADAS_DISENGAGE_LANE_LOST",
                "warning",
                "Disengaged: lane markings not detected",
                {"cause": "lane_markings_lost"},
            )
        elif rng.random() < TAKEOVER_REQUEST_PER_KM * km:
            case = takeovers.iloc[rng.integers(len(takeovers))]
            rt = float(case["reactionTime"]) / 1000.0
            ok = case["takeoverResult"] == "Success"
            events.append(
                _event(
                    ts[i],
                    trip_id,
                    vehicle_id,
                    "ADAS_TAKEOVER_REQUEST",
                    "warning",
                    "Takeover requested: system limit reached",
                    {
                        "cause": f"system_limit:{case['criticalEventType']}",
                        "reaction_time_s": round(rt, 3),
                        "td2d_condition": case["condition"],
                    },
                )
            )
            t_resp = pd.Timestamp(ts[i]) + pd.Timedelta(seconds=rt)
            if ok:
                cause = (
                    "ADAS_DISENGAGE_TAKEOVER",
                    "info",
                    f"Driver took over after {rt:.2f} s",
                    {"cause": "takeover_completed", "reaction_time_s": round(rt, 3)},
                )
            else:
                cause = (
                    "ADAS_TAKEOVER_LATE",
                    "critical",
                    f"Late takeover: driver responded after {rt:.2f} s",
                    {"cause": "takeover_late", "reaction_time_s": round(rt, 3)},
                )
            events.append(_event(t_resp, trip_id, vehicle_id, *cause))
            engaged, above_for, cooldown = False, 0, REENGAGE_AFTER_S
            continue
        if cause:
            events.append(_event(ts[i], trip_id, vehicle_id, *cause))
            engaged, above_for, cooldown = False, 0, REENGAGE_AFTER_S
    return events


def simulate(store: Store, td2d_dir: Path) -> pd.DataFrame:
    takeovers = load_td2d(td2d_dir)
    trips = store.sql("SELECT trip_id FROM trips WHERE max_speed_kmh >= ? ORDER BY trip_id", [ENGAGE_KMH])
    out: list[dict] = []
    for trip_id in trips["trip_id"]:
        f = store.sql(
            "SELECT ts, vehicle_id, trip_id, speed_kmh, accel_mps2, ambient_temp_c FROM frames "
            "WHERE trip_id = ? ORDER BY ts",
            [trip_id],
        )
        out.extend(simulate_trip(f, takeovers))
    return pd.DataFrame(out)
