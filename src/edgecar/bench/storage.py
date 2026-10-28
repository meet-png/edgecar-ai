"""Storage and retention: what one real car-year costs on flash, and how fast it can be searched.

    python -m edgecar.bench.storage --vehicle leaf-455 --out eval/results/storage.json

Each storage tier of the vehicle is copied into its own DuckDB file to measure its size on disk:

* **hot** — 1 Hz signals (``frames``), kept for ``--hot-weeks``;
* **warm** — per-minute roll-ups (``frames_1min``), kept for ``--warm-months``;
* **cold** — trips and events, kept for the life of the vehicle.

Query latency is the median of repeated runs of typical questions over the last 8 weeks and over the whole year.
"""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path

import duckdb

from edgecar.analytics import Engine
from edgecar.query import Query, TimeWindow
from edgecar.store import Store

QUERIES = {
    "average consumption": dict(intent="summary", metric="energy_consumption"),
    "consumption trend": dict(intent="trend", metric="energy_consumption"),
    "city vs highway": dict(intent="compare", metric="energy_consumption", compare_by="road_type"),
    "largest efficiency drop": dict(intent="extreme", metric="energy_efficiency", direction="drop"),
    "why range changed": dict(intent="explain_change", metric="range"),
    "list all events": dict(intent="list_events", event="any"),
    "anything unusual": dict(intent="anomalies"),
}


def tier_bytes(store: Path, vehicle: str, tables: list[str]) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "tier.duckdb"
        con = duckdb.connect(str(out))
        con.execute(f"ATTACH '{store.as_posix()}' AS src (READ_ONLY)")
        for t in tables:
            con.execute(f"CREATE TABLE {t} AS SELECT * FROM src.{t} WHERE vehicle_id = ?", [vehicle])
        con.execute("DETACH src")
        con.execute("CHECKPOINT")
        con.close()
        return out.stat().st_size


def latency_ms(engine: Engine, q: Query, runs: int = 15) -> float:
    engine.run(q)  # warm caches, as on a running vehicle
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        engine.run(q)
        times.append((time.perf_counter() - t0) * 1000)
    return round(statistics.median(times), 1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", type=Path, default=Path("data/edgecar.duckdb"))
    ap.add_argument("--vehicle", default="leaf-455")
    ap.add_argument("--hot-weeks", type=int, default=8)
    ap.add_argument("--warm-months", type=int, default=12)
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()

    store = Store(a.store, read_only=True, threads=a.threads)
    span = store.sql(
        "SELECT min(ts) AS t0, max(ts) AS t1, count(*) AS rows, count(DISTINCT trip_id) AS trips, "
        "count(DISTINCT date_trunc('day', ts)) AS days FROM frames WHERE vehicle_id = ?",
        [a.vehicle],
    ).iloc[0]
    weeks = (span["t1"] - span["t0"]).days / 7
    sizes = {
        "hot_1hz": tier_bytes(a.store, a.vehicle, ["frames"]),
        "warm_1min": tier_bytes(a.store, a.vehicle, ["frames_1min"]),
        "cold_trips_events": tier_bytes(a.store, a.vehicle, ["trips", "events"]),
    }
    mb = {k: round(v / 1e6, 2) for k, v in sizes.items()}
    hot_per_week = sizes["hot_1hz"] / weeks
    retained = hot_per_week * a.hot_weeks + sizes["warm_1min"] * min(1.0, a.warm_months * 30.4 / (weeks * 7))
    retained += sizes["cold_trips_events"]

    engine = Engine(store, a.vehicle)
    latency = {
        name: {
            f"last_{a.hot_weeks}_weeks": latency_ms(
                engine, Query(**spec, window=TimeWindow(kind="last_days", days=7 * a.hot_weeks))
            ),
            "whole_year": latency_ms(engine, Query(**spec, window=TimeWindow(kind="last_days", days=365))),
        }
        for name, spec in QUERIES.items()
    }
    report = {
        "vehicle": a.vehicle,
        "span": f"{span['t0']:%Y-%m-%d} → {span['t1']:%Y-%m-%d} ({weeks:.0f} weeks, {span['days']} days driven, "
        f"{span['trips']} trips, {span['rows']:,} 1 Hz rows = {span['rows'] / 3600:.0f} h of driving)",
        "tier_mb": mb,
        "whole_year_all_tiers_mb": round(sum(sizes.values()) / 1e6, 2),
        "hot_mb_per_week": round(hot_per_week / 1e6, 3),
        "retention": f"1 Hz for {a.hot_weeks} weeks, 1-min roll-ups for {a.warm_months} months, trips + events forever",
        "retained_mb": round(retained / 1e6, 2),
        "query_ms_median": latency,
        "threads": a.threads,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
