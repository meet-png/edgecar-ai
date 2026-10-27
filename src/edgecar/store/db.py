"""On-device storage: one embedded DuckDB file with tiered retention.

Tiers
-----
``frames``      1 Hz canonical signals (warm tier, weeks).
``frames_1min`` per-minute mean/min/max roll-ups (cold tier, months).
``trips``       one summary row per trip (kept for the vehicle's life).
``events``      discrete events from every source (kept for the vehicle's life).

Analytics read ``trips`` and ``events`` first and touch ``frames`` only for short windows, which keeps typical
queries in the low milliseconds on two ARM cores.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from edgecar.schema import SIGNALS

_SIGNAL_DDL = ",\n    ".join(f"{name} DOUBLE" for name in SIGNALS)

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS vehicles (
    vehicle_id VARCHAR PRIMARY KEY,
    name VARCHAR,
    powertrain VARCHAR,
    battery_kwh_usable DOUBLE,
    location VARCHAR,
    timezone VARCHAR,
    provenance VARCHAR,
    source_ref VARCHAR
);

CREATE TABLE IF NOT EXISTS frames (
    ts TIMESTAMP NOT NULL,
    vehicle_id VARCHAR NOT NULL,
    trip_id VARCHAR NOT NULL,
    {_SIGNAL_DDL},
    provenance VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id VARCHAR PRIMARY KEY,
    ts TIMESTAMP NOT NULL,
    vehicle_id VARCHAR NOT NULL,
    trip_id VARCHAR,
    category VARCHAR NOT NULL,
    code VARCHAR NOT NULL,
    severity VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    title VARCHAR NOT NULL,
    detail JSON,
    provenance VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS trips (
    trip_id VARCHAR PRIMARY KEY,
    vehicle_id VARCHAR NOT NULL,
    start_ts TIMESTAMP,
    end_ts TIMESTAMP,
    duration_min DOUBLE,
    distance_km DOUBLE,
    energy_kwh DOUBLE,
    kwh_per_100km DOUBLE,
    traction_kwh DOUBLE,
    heater_kwh DOUBLE,
    ac_kwh DOUBLE,
    regen_kwh DOUBLE,
    soc_start DOUBLE,
    soc_end DOUBLE,
    avg_speed_kmh DOUBLE,
    moving_speed_kmh DOUBLE,
    max_speed_kmh DOUBLE,
    mean_v2 DOUBLE,
    share_city DOUBLE,
    share_highway DOUBLE,
    stops_per_km DOUBLE,
    ambient_c DOUBLE,
    batt_temp_max_c DOUBLE,
    hard_brakes INTEGER,
    fuel_l DOUBLE,
    l_per_100km DOUBLE,
    road_type VARCHAR,
    provenance VARCHAR
);
"""

# Speed bands used everywhere "city" and "highway" are mentioned (km/h).
CITY_MAX_KMH = 50.0
HIGHWAY_MIN_KMH = 80.0
HARD_BRAKE_MPS2 = -3.0


class Store:
    def __init__(
        self, path: str | Path, *, threads: int = 2, memory_limit: str = "256MB", read_only: bool = False
    ) -> None:
        self.path = Path(path)
        self.con = duckdb.connect(str(self.path), read_only=read_only)
        self.con.execute(f"SET threads = {int(threads)}")
        self.con.execute(f"SET memory_limit = '{memory_limit}'")
        if not read_only:
            self.con.execute(_SCHEMA)

    def close(self) -> None:
        self.con.close()

    # ---------------------------------------------------------------- writes

    def upsert_vehicle(self, row: dict) -> None:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        self.con.execute(f"INSERT OR REPLACE INTO vehicles ({cols}) VALUES ({marks})", list(row.values()))

    def append_frames(self, frames: pd.DataFrame, provenance: str) -> int:
        df = frames.copy()
        for name in SIGNALS:
            if name not in df:
                df[name] = float("nan")
        df["provenance"] = provenance
        cols = ["ts", "vehicle_id", "trip_id", *SIGNALS.keys(), "provenance"]
        # pandas NaN must become SQL NULL, otherwise a single gap poisons every aggregate.
        select = ", ".join(f"CASE WHEN isnan({c}) THEN NULL ELSE {c} END" if c in SIGNALS else c for c in cols)
        self.con.register("_incoming", df[cols])
        self.con.execute(f"INSERT INTO frames ({', '.join(cols)}) SELECT {select} FROM _incoming")
        self.con.unregister("_incoming")
        return len(df)

    def append_events(self, events: pd.DataFrame) -> int:
        self.con.register("_incoming", events)
        cols = ", ".join(events.columns)
        self.con.execute(f"INSERT OR REPLACE INTO events ({cols}) SELECT {cols} FROM _incoming")
        self.con.unregister("_incoming")
        return len(events)

    # ----------------------------------------------------------- derivations

    def rebuild_trips(self) -> int:
        """Recompute the per-trip summary table from 1 Hz frames."""
        self.con.execute("DELETE FROM trips")
        self.con.execute(f"""
            INSERT INTO trips
            WITH f AS (
                SELECT *,
                       coalesce(accel_mps2,
                                (speed_kmh - lag(speed_kmh) OVER w) / 3.6) AS a,
                       CASE WHEN speed_kmh < 1 AND lag(speed_kmh) OVER w >= 1 THEN 1 ELSE 0 END AS stop_start
                FROM frames
                WINDOW w AS (PARTITION BY trip_id ORDER BY ts)
            ),
            f2 AS (
                -- a hard brake is counted once, at the second deceleration first crosses the threshold
                SELECT *, CASE WHEN a <= {HARD_BRAKE_MPS2}
                                AND coalesce(lag(a) OVER (PARTITION BY trip_id ORDER BY ts), 0) > {HARD_BRAKE_MPS2}
                               THEN 1 ELSE 0 END AS brake_onset
                FROM f
            )
            SELECT
                trip_id,
                any_value(vehicle_id),
                min(ts), max(ts),
                date_diff('second', min(ts), max(ts)) / 60.0,
                sum(speed_kmh) / 3600.0                                        AS distance_km,
                sum(batt_power_kw) / 3600.0                                    AS energy_kwh,
                100.0 * sum(batt_power_kw) / nullif(sum(speed_kmh), 0)         AS kwh_per_100km,
                (sum(batt_power_kw) - coalesce(sum(hvac_heater_kw), 0) - coalesce(sum(hvac_ac_kw), 0)) / 3600.0,
                coalesce(sum(hvac_heater_kw), 0) / 3600.0,
                coalesce(sum(hvac_ac_kw), 0) / 3600.0,
                -sum(least(batt_power_kw, 0)) / 3600.0,
                arg_min(soc_pct, ts) FILTER (WHERE soc_pct IS NOT NULL),
                arg_max(soc_pct, ts) FILTER (WHERE soc_pct IS NOT NULL),
                avg(speed_kmh),
                avg(speed_kmh) FILTER (WHERE speed_kmh >= 1),
                max(speed_kmh),
                avg(speed_kmh * speed_kmh) FILTER (WHERE speed_kmh >= 1),
                sum(speed_kmh) FILTER (WHERE speed_kmh < {CITY_MAX_KMH}) / nullif(sum(speed_kmh), 0),
                sum(speed_kmh) FILTER (WHERE speed_kmh >= {HIGHWAY_MIN_KMH}) / nullif(sum(speed_kmh), 0),
                sum(stop_start) / nullif(sum(speed_kmh) / 3600.0, 0),
                avg(ambient_temp_c),
                max(coalesce(batt_temp_max_c, batt_temp_c)),
                sum(brake_onset),
                sum(fuel_rate_lph) / 3600.0,
                100.0 * sum(fuel_rate_lph) / nullif(sum(speed_kmh), 0),
                NULL,
                any_value(provenance)
            FROM f2
            GROUP BY trip_id
        """)
        self.con.execute("""
            UPDATE trips SET road_type = CASE
                WHEN share_highway >= 0.5 THEN 'highway'
                WHEN share_city >= 0.6 THEN 'city'
                ELSE 'mixed' END
        """)
        return self.con.execute("SELECT count(*) FROM trips").fetchone()[0]

    def rebuild_rollups(self) -> int:
        """Cold tier: per-minute mean/min/max of every signal."""
        aggs = ",\n".join(f"avg({n}) AS {n}, min({n}) AS {n}_min, max({n}) AS {n}_max" for n in SIGNALS)
        self.con.execute("DROP TABLE IF EXISTS frames_1min")
        self.con.execute(f"""
            CREATE TABLE frames_1min AS
            SELECT date_trunc('minute', ts) AS ts, vehicle_id, trip_id, {aggs}
            FROM frames GROUP BY ALL ORDER BY vehicle_id, ts
        """)
        return self.con.execute("SELECT count(*) FROM frames_1min").fetchone()[0]

    def apply_retention(self, now: pd.Timestamp, warm_days: int) -> int:
        """Drop 1 Hz frames older than ``warm_days``; trips, events and minute roll-ups are kept."""
        cutoff = now - pd.Timedelta(days=warm_days)
        n = self.con.execute("SELECT count(*) FROM frames WHERE ts < ?", [cutoff]).fetchone()[0]
        self.con.execute("DELETE FROM frames WHERE ts < ?", [cutoff])
        return n

    # ------------------------------------------------------------------ reads

    def sql(self, query: str, params: list | None = None) -> pd.DataFrame:
        return self.con.execute(query, params or []).df()
