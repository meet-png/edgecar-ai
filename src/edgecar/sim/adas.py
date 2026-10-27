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

Generation is seeded per trip, so a rebuild produces identical events.
"""

from __future__ import annotations

import glob
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from edgecar.store import Store

ENGAGE_MIN_KMH = 30.0  # the driver can engage above this speed
STANDSTILL_HOLD_S = 30  # stop-and-go ACC holds the car at a standstill, then hands control back
DRIVER_BRAKE_MPS2 = -3.5  # beyond what ACC commands itself (~ -3 m/s²): the driver braked
SENSOR_BLOCK_C = -2.0

# Assumed shares of episode-ending causes when no cause is visible in the data (BATON has no cause labels).
CAUSE_PRIORS = {"steer": 0.70, "lane": 0.15, "tor": 0.10, "sensor": 0.05}

PROVENANCE = "sim:adas (BATON timing, TD2D takeovers)"


@dataclass(frozen=True)
class Calibration:
    episode_s: np.ndarray  # engaged-episode durations
    gap_s: np.ndarray  # delays before re-engaging
    takeovers: pd.DataFrame  # TD2D: reaction time (ms) and outcome per case


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


def _event(ts, trip_id, vehicle_id, code, severity, title, detail) -> dict:
    return {
        "event_id": f"{trip_id}:adas:{pd.Timestamp(ts):%H%M%S}:{code}",
        "ts": pd.Timestamp(ts),
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


def simulate_trip(f: pd.DataFrame, cal: Calibration) -> list[dict]:
    trip_id, vehicle_id = f["trip_id"].iloc[0], f["vehicle_id"].iloc[0]
    rng = _rng(trip_id)
    speed = f["speed_kmh"].to_numpy()
    accel = f["accel_mps2"].to_numpy() if f["accel_mps2"].notna().any() else np.r_[0, np.diff(speed) / 3.6]
    ambient = f["ambient_temp_c"].to_numpy()
    ts = f["ts"].to_numpy()
    events: list[dict] = []
    engaged = False
    wait = float(rng.choice(cal.gap_s))  # seconds of eligible driving before the driver engages
    remaining = 0.0
    stopped_for = 0
    for i in range(len(f)):
        v = speed[i]
        if np.isnan(v):
            continue
        if not engaged:
            if v >= ENGAGE_MIN_KMH:
                wait -= 1
                if wait <= 0:
                    engaged, remaining = True, float(rng.choice(cal.episode_s))
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
        remaining -= 1
        stopped_for = stopped_for + 1 if v < 1.0 else 0
        amb = ambient[i]
        cause = None
        if accel[i] <= DRIVER_BRAKE_MPS2:
            cause = (
                "ADAS_DISENGAGE_DRIVER_BRAKE",
                "info",
                f"Disengaged: driver braked ({accel[i]:.1f} m/s²)",
                {"cause": "driver_brake", "decel_mps2": round(float(accel[i]), 2), "speed_kmh": round(v, 1)},
            )
        elif stopped_for >= STANDSTILL_HOLD_S:
            cause = (
                "ADAS_DISENGAGE_STANDSTILL",
                "info",
                f"Disengaged: standing still for more than {STANDSTILL_HOLD_S} s",
                {"cause": "standstill_hold_exceeded"},
            )
        elif remaining <= 0:
            priors = dict(CAUSE_PRIORS)
            cold = not np.isnan(amb) and amb < 0
            if cold:
                priors["lane"] *= 2.0  # slush and salt on lane markings
            if np.isnan(amb) or amb >= SENSOR_BLOCK_C:
                priors.pop("sensor")
            keys = list(priors)
            p = np.array([priors[k] for k in keys])
            pick = keys[rng.choice(len(keys), p=p / p.sum())]
            if pick == "steer":
                cause = (
                    "ADAS_DISENGAGE_DRIVER_STEER",
                    "info",
                    "Disengaged: driver took over steering",
                    {"cause": "driver_steer", "speed_kmh": round(v, 1)},
                )
            elif pick == "lane":
                cause = (
                    "ADAS_DISENGAGE_LANE_LOST",
                    "warning",
                    "Disengaged: lane markings not detected",
                    {"cause": "lane_markings_lost", "ambient_c": None if np.isnan(amb) else round(amb, 1)},
                )
            elif pick == "sensor":
                cause = (
                    "ADAS_DISENGAGE_SENSOR_BLOCKED",
                    "warning",
                    "Disengaged: front radar/camera blocked (ice or snow)",
                    {"cause": "sensor_blocked", "ambient_c": round(amb, 1)},
                )
            else:
                case = cal.takeovers.iloc[rng.integers(len(cal.takeovers))]
                rt = float(case["reactionTime"]) / 1000.0
                events.append(
                    _event(
                        ts[i],
                        trip_id,
                        vehicle_id,
                        "ADAS_TAKEOVER_REQUEST",
                        "warning",
                        "Takeover requested: system limit reached",
                        {"cause": f"system_limit:{case['criticalEventType']}", "speed_kmh": round(v, 1)},
                    )
                )
                late = case["takeoverResult"] != "Success"
                cause = (
                    "ADAS_DISENGAGE_TAKEOVER_LATE" if late else "ADAS_DISENGAGE_TAKEOVER",
                    "critical" if late else "info",
                    f"{'Late takeover' if late else 'Driver took over'}: responded after {rt:.2f} s",
                    {
                        "cause": "takeover_late" if late else "takeover_completed",
                        "reaction_time_s": round(rt, 3),
                        "td2d_condition": case["condition"],
                    },
                )
                events.append(_event(pd.Timestamp(ts[i]) + pd.Timedelta(seconds=rt), trip_id, vehicle_id, *cause))
                cause = None
                engaged, wait = False, float(rng.choice(cal.gap_s))
        if cause:
            events.append(_event(ts[i], trip_id, vehicle_id, *cause))
            engaged, wait = False, float(rng.choice(cal.gap_s))
    return events


def simulate(store: Store, cal: Calibration, vehicle_ids: list[str] | None = None) -> pd.DataFrame:
    sql = "SELECT trip_id FROM trips WHERE max_speed_kmh >= ?"
    params: list = [ENGAGE_MIN_KMH]
    if vehicle_ids:
        sql += f" AND vehicle_id IN ({', '.join('?' for _ in vehicle_ids)})"
        params += vehicle_ids
    out: list[dict] = []
    for trip_id in store.sql(sql + " ORDER BY trip_id", params)["trip_id"]:
        f = store.sql(
            "SELECT ts, vehicle_id, trip_id, speed_kmh, accel_mps2, ambient_temp_c FROM frames "
            "WHERE trip_id = ? ORDER BY ts",
            [trip_id],
        )
        out.extend(simulate_trip(f, cal))
    return pd.DataFrame(out)
