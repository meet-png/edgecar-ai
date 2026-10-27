"""Fault scenario: coolant-pump failure + a degrading cell group → battery overheating warning.

A scenario is a *derived trip*: a real trip's signals replayed on a new date with a failure applied on top. It is
stored as its own trip with ``sim:scenario`` provenance and is excluded from energy statistics, so it never
distorts the real data.

Physics. After the pump fails, the hottest cell group heats by Joule losses on the trip's **real** current draw and
loses heat only passively::

    dT/dt = I² · R_fault / (m·c)  −  (T − T_base) / τ

Calibration: on the real trip alone (no fault) the hottest sensor never exceeds 33 °C, and a pump failure with a
healthy cell group (R ≈ 0.02–0.04 Ω) would only reach 35–38 °C — not enough to warn. The scenario therefore models
a cell group whose resistance has risen to 0.15 Ω (an ageing or internally damaged group, a known precursor of
thermal events). Diagnostic codes are SAE J2012 definitions: P0C73 (motor-electronics coolant pump "A" control),
P0A7E (hybrid/EV battery pack "A" over temperature).

Every event in the chain arrives through the protocol that would carry it in a real vehicle, so the timeline doubles
as a multi-protocol ingestion fixture and as ground truth for "what happened before the warning?".
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from edgecar.store import Store

BASE_TRIP = "i3-tum:A07"  # real summer trip: 44 km, 33.5 °C outside, 1 Jul 2019
SCENARIO_TRIP = "i3-tum:S01"
START = pd.Timestamp("2019-07-24 13:05:00")
FAIL_AFTER_S = 360
R_FAULT_OHM = 0.15
MC_J_PER_K = 20_000.0  # thermal mass of the affected cell group
PASSIVE_TAU_S = 1_800.0
PROVENANCE = "sim:scenario/battery_overheat (base i3-tum:A07)"


def _event(ts, code, category, severity, source, title, detail) -> dict:
    return {
        "event_id": f"{SCENARIO_TRIP}:{code}:{pd.Timestamp(ts):%H%M%S}",
        "ts": pd.Timestamp(ts),
        "vehicle_id": "i3-tum",
        "trip_id": SCENARIO_TRIP,
        "category": category,
        "code": code,
        "severity": severity,
        "source": source,
        "title": title,
        "detail": json.dumps(detail),
        "provenance": PROVENANCE,
    }


def battery_overheat(store: Store) -> tuple[pd.DataFrame, pd.DataFrame]:
    base = store.sql("SELECT * FROM frames WHERE trip_id = ? ORDER BY ts", [BASE_TRIP])
    if base.empty:
        raise LookupError(f"base trip {BASE_TRIP} not in store")
    f = base.drop(columns=["provenance"]).copy()
    f["ts"] = f["ts"] + (START - f["ts"].iloc[0])
    f["trip_id"] = SCENARIO_TRIP

    i2 = f["batt_current_a"].fillna(0).to_numpy() ** 2
    t_base = base["batt_temp_max_c"].ffill().to_numpy()
    temp = np.empty(len(f))
    temp[0] = t_base[0]
    for k in range(1, len(f)):
        heat = i2[k] * R_FAULT_OHM / MC_J_PER_K if k >= FAIL_AFTER_S else 0.0
        temp[k] = temp[k - 1] + heat - (temp[k - 1] - t_base[k]) / PASSIVE_TAU_S
    f["batt_temp_max_c"] = temp
    # one hot cell group drags the pack average up a little
    f["batt_temp_c"] = base["batt_temp_c"].to_numpy() + 0.25 * (temp - t_base)

    ts = f["ts"]
    t_fail = ts.iloc[FAIL_AFTER_S]
    ev = [
        _event(
            t_fail + pd.Timedelta(seconds=0.4),
            "QNX_THERMAL_MGR",
            "system",
            "warning",
            "QNX slog2",
            "thermal_mgr: coolant pump A PWM feedback lost — coolant flow 0.0 l/min",
            {"slog2": "thermal_mgr[412]  pump_a: pwm_fb=0 duty=62% flow=0.0 l/min -> FAULT"},
        ),
        _event(
            t_fail + pd.Timedelta(seconds=1.0),
            "P0C73",
            "dtc",
            "warning",
            "UDS 0x19 (VCU)",
            "DTC P0C73 — Motor electronics coolant pump 'A' control",
            {
                "dtc": "P0C73",
                "status": "0x2F testFailed|pending|confirmed",
                "freeze_frame": {
                    "speed_kmh": round(float(f["speed_kmh"].iloc[FAIL_AFTER_S]), 1),
                    "batt_temp_max_c": round(float(temp[FAIL_AFTER_S]), 1),
                },
            },
        ),
    ]
    rate = pd.Series(temp).diff(60) * 1.0  # °C per minute over a 60 s window
    first = {}
    for name, cond in {
        "rise": (rate >= 0.8) & (np.arange(len(f)) > FAIL_AFTER_S),
        "derate": temp >= 50.0,
        "warn": temp >= 55.0,
    }.items():
        idx = np.flatnonzero(np.asarray(cond))
        if idx.size:
            first[name] = int(idx[0])
    if "rise" in first:
        k = first["rise"]
        ev.append(
            _event(
                ts.iloc[k],
                "BATT_TEMP_RISE_RATE",
                "thermal",
                "warning",
                "CAN (BMS)",
                f"Battery temperature rising {rate.iloc[k]:.1f} °C/min with no coolant flow",
                {"rate_c_per_min": round(float(rate.iloc[k]), 2), "batt_temp_max_c": round(temp[k], 1)},
            )
        )
    if "derate" in first:
        k = first["derate"]
        ev.append(
            _event(
                ts.iloc[k],
                "BATT_POWER_DERATE",
                "thermal",
                "warning",
                "SOME/IP (ThermalMgmt)",
                "Drive power limited to protect the battery",
                {"batt_temp_max_c": round(temp[k], 1), "power_limit_kw": 40},
            )
        )
    if "warn" in first:
        k = first["warn"]
        ev += [
            _event(
                ts.iloc[k],
                "BATT_OVERTEMP_WARNING",
                "thermal",
                "critical",
                "VHAL (cluster warning)",
                "Battery overheating warning shown to the driver",
                {"batt_temp_max_c": round(temp[k], 1), "minutes_after_pump_fault": round((k - FAIL_AFTER_S) / 60, 1)},
            ),
            _event(
                ts.iloc[k] + pd.Timedelta(seconds=1),
                "P0A7E",
                "dtc",
                "critical",
                "UDS 0x19 (BMS)",
                "DTC P0A7E — Hybrid/EV battery pack 'A' over temperature",
                {"dtc": "P0A7E", "status": "0x2F testFailed|pending|confirmed"},
            ),
        ]
    return f, pd.DataFrame(ev)
