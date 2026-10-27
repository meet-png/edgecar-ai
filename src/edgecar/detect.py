"""Event detectors that run over stored signals: they turn real telemetry into discrete, searchable events.

On the vehicle these run incrementally as data arrives; at build time they run once over the full history.
"""

from __future__ import annotations

import json

import pandas as pd

from edgecar.store import Store
from edgecar.store.db import HARD_BRAKE_MPS2

CHARGE_MIN_SOC_GAIN = 3.0  # % gained between two trips counts as a charging session
BATT_TEMP_HIGH_C = 40.0  # sustained pack temperature worth recording


def _events(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(
        rows,
        columns=[
            "event_id",
            "ts",
            "vehicle_id",
            "trip_id",
            "category",
            "code",
            "severity",
            "source",
            "title",
            "detail",
            "provenance",
        ],
    )
    df["detail"] = df["detail"].map(json.dumps)
    return df


def detect_charging(store: Store) -> pd.DataFrame:
    t = store.sql("""
        SELECT t.*, v.battery_kwh_usable,
               lag(soc_end) OVER (PARTITION BY t.vehicle_id ORDER BY start_ts) AS prev_soc_end,
               lag(end_ts)  OVER (PARTITION BY t.vehicle_id ORDER BY start_ts) AS prev_end
        FROM trips t JOIN vehicles v USING (vehicle_id)
        WHERE soc_start IS NOT NULL""")
    t = t[(t["soc_start"] - t["prev_soc_end"]) >= CHARGE_MIN_SOC_GAIN]
    rows = []
    for r in t.itertuples():
        gain = r.soc_start - r.prev_soc_end
        kwh = gain / 100 * r.battery_kwh_usable if pd.notna(r.battery_kwh_usable) else None
        rows.append(
            {
                "event_id": f"{r.vehicle_id}:chg:{r.prev_end:%Y%m%d%H%M%S}",
                "ts": r.prev_end,
                "vehicle_id": r.vehicle_id,
                "trip_id": None,
                "category": "charging",
                "code": "CHARGE_SESSION",
                "severity": "info",
                "source": "BMS",
                "title": f"Charged from {r.prev_soc_end:.0f}% to {r.soc_start:.0f}%",
                "detail": {
                    "soc_from": round(r.prev_soc_end, 1),
                    "soc_to": round(r.soc_start, 1),
                    "energy_kwh": None if kwh is None else round(kwh, 2),
                    "parked_hours": round((r.start_ts - r.prev_end).total_seconds() / 3600, 1),
                },
                "provenance": r.provenance,
            }
        )
    return _events(rows)


def detect_hard_brakes(store: Store) -> pd.DataFrame:
    f = store.sql(f"""
        WITH s AS (
            SELECT ts, vehicle_id, trip_id, provenance, speed_kmh,
                   coalesce(accel_mps2, (speed_kmh - lag(speed_kmh) OVER w) / 3.6) AS a
            FROM frames WINDOW w AS (PARTITION BY trip_id ORDER BY ts)
        )
        SELECT * FROM (
            SELECT *, lag(a) OVER (PARTITION BY trip_id ORDER BY ts) AS prev_a FROM s
        ) WHERE a <= {HARD_BRAKE_MPS2} AND coalesce(prev_a, 0) > {HARD_BRAKE_MPS2}""")
    rows = [
        {
            "event_id": f"{r.trip_id}:brake:{r.ts:%H%M%S}",
            "ts": r.ts,
            "vehicle_id": r.vehicle_id,
            "trip_id": r.trip_id,
            "category": "driving",
            "code": "HARD_BRAKE",
            "severity": "info",
            "source": "ESC",
            "title": f"Hard braking at {r.speed_kmh:.0f} km/h ({r.a:.1f} m/s²)",
            "detail": {"speed_kmh": round(r.speed_kmh, 1), "decel_mps2": round(r.a, 2)},
            "provenance": r.provenance,
        }
        for r in f.itertuples()
    ]
    return _events(rows)


def detect_battery_temperature(store: Store) -> pd.DataFrame:
    """First crossing of ``BATT_TEMP_HIGH_C`` per trip."""
    f = store.sql(f"""
        SELECT trip_id, any_value(vehicle_id) AS vehicle_id, any_value(provenance) AS provenance,
               min(ts) AS ts, max(coalesce(batt_temp_max_c, batt_temp_c)) AS peak
        FROM frames WHERE coalesce(batt_temp_max_c, batt_temp_c) >= {BATT_TEMP_HIGH_C}
        GROUP BY trip_id""")
    rows = [
        {
            "event_id": f"{r.trip_id}:batthot",
            "ts": r.ts,
            "vehicle_id": r.vehicle_id,
            "trip_id": r.trip_id,
            "category": "thermal",
            "code": "BATT_TEMP_HIGH",
            "severity": "warning",
            "source": "BMS",
            "title": f"Battery temperature above {BATT_TEMP_HIGH_C:.0f} °C (peak {r.peak:.1f} °C)",
            "detail": {"peak_c": round(r.peak, 1)},
            "provenance": r.provenance,
        }
        for r in f.itertuples()
    ]
    return _events(rows)


def detect_all(store: Store) -> int:
    n = 0
    for detector in (detect_charging, detect_hard_brakes, detect_battery_temperature):
        ev = detector(store)
        if not ev.empty:
            n += store.append_events(ev)
    return n
