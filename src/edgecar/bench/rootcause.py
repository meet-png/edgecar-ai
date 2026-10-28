"""Root-cause benchmark: random fault scenarios with distractors — does the assistant name the real cause?

    python -m edgecar.bench.rootcause --seed 1000 --n 200 --out eval/results/rootcause.json

Each seed builds one scenario (``edgecar.sim.faults``) on a real BMW i3 trip, sends it through the wire encoders
and protocol adapters into its own store, and asks "What happened before the battery overheating warning?".

* **root cause correct** — the event the answer names as the likely root cause is the injected fault (±2 s);
* **code correct** — when the fault has a diagnostic code, the answer names that code;
* **events recovered** — every ground-truth and distractor event came back out of the decoders (±2 s);
* **earliest-fault rule** — the same check for the naive rule "the first fault in the 15-minute window";
* **map-only rule** — the first fault of the trip from a subsystem that can overheat the battery (no causal order).

Seeds below 1000 were used while developing; report seeds from 1000 up.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from edgecar import diagnosis
from edgecar.assistant import Assistant
from edgecar.fleet import VEHICLES
from edgecar.ingest import adapters
from edgecar.protocols import vhal
from edgecar.protocols.can import I3_FD_DBC_FILES, I3_FD_SIGNALS, REFERENCE_DBC_DIR, CanCodec, segment_trips
from edgecar.protocols.slog2 import parse_line
from edgecar.sim import faults
from edgecar.store import Store

QUESTION = "What happened before the battery overheating warning?"
PROVENANCE = "sim:rootcause-benchmark"


def real_i3_trips(store: Store, min_minutes: float = 25.0) -> dict[str, pd.DataFrame]:
    ids = store.sql(
        "SELECT trip_id FROM trips WHERE vehicle_id = 'i3-tum' AND provenance LIKE 'real%' AND duration_min >= ?",
        [min_minutes],
    )["trip_id"]
    return {t: store.sql("SELECT * FROM frames WHERE trip_id = ? ORDER BY ts", [t]) for t in ids}


def scenario_store(sc: faults.Scenario, codec: CanCodec) -> Store:
    """The scenario's traffic, decoded by the same adapters as the demo store, in a store of its own."""
    tr = sc.traffic
    store = Store(":memory:", threads=2)
    v = VEHICLES["i3-tum"]
    store.upsert_vehicle(asdict(v) | {"provenance": str(v.provenance)})
    ts_ns = tr.frames["ts"].astype("datetime64[ns]").astype("int64").to_numpy()
    props = [
        vhal.PropValue(int(t), vhal.PERF_VEHICLE_SPEED, float(x) / 3.6)
        for t, x in zip(ts_ns, tr.frames["speed_kmh"], strict=True)
        if pd.notna(x)
    ]
    decoded = codec.decode(codec.encode(tr.frames)).merge(adapters.vhal_frames(props), on="ts", how="outer")
    decoded = segment_trips(decoded.sort_values("ts").reset_index(drop=True), "i3-tum")
    store.append_frames(decoded.assign(trip_id=tr.frames["trip_id"].iloc[0]), PROVENANCE)
    store.rebuild_trips()
    year = tr.frames["ts"].iloc[0].year
    events = pd.concat(
        [
            adapters.slog2_events([r for x in tr.slog2_lines if (r := parse_line(x, year))], "i3-tum", PROVENANCE),
            adapters.uds_events(tr.diag_frames, "i3-tum", PROVENANCE),
            adapters.someip_events(tr.someip_records, "i3-tum", PROVENANCE),
            adapters.vhal_events(tr.vhal_values, "i3-tum", PROVENANCE),
            adapters.canfd_thermal_events(decoded, "i3-tum", PROVENANCE),
        ],
        ignore_index=True,
    )
    store.append_events(adapters.assign_trips(events, store))
    return store


def recovered(store: Store, expected: list[tuple[pd.Timestamp, str]]) -> int:
    ev = store.sql("SELECT ts, code FROM events")
    return sum(
        bool(((ev["code"] == code) & ((ev["ts"] - t).abs() <= pd.Timedelta(seconds=2))).any()) for t, code in expected
    )


def earliest_fault_rule(store: Store, lookback_min: float = 15.0) -> pd.Timestamp | None:
    """The naive rule: the first warning-or-worse diagnostic code or system error in the window before the warning."""
    warn = store.sql("SELECT max(ts) AS t FROM events WHERE code = 'BATT_OVERTEMP_WARNING'")["t"][0]
    first = store.sql(
        "SELECT min(ts) AS t FROM events WHERE category IN ('dtc', 'system') AND severity IN ('warning', 'critical') "
        "AND ts >= ? AND ts <= ?",
        [warn - pd.Timedelta(minutes=lookback_min), warn],
    )["t"][0]
    return None if pd.isna(first) else first


def map_only_rule(store: Store) -> pd.Timestamp | None:
    """The fault-propagation map without causal ordering: the first fault of the trip from a subsystem that can
    overheat this car's battery."""
    warn = store.sql("SELECT * FROM events WHERE code = 'BATT_OVERTEMP_WARNING' ORDER BY ts DESC LIMIT 1").iloc[0]
    causes = diagnosis.possible_causes(warn["code"], VEHICLES["i3-tum"].battery_cooling)
    ev = store.sql(
        "SELECT ts, code FROM events WHERE category IN ('dtc', 'system') AND severity IN ('warning', 'critical') "
        "AND ts <= ? ORDER BY ts",
        [warn["ts"]],
    )
    ev = ev[ev["code"].map(lambda c: diagnosis.subsystem(c) in causes)]
    return None if ev.empty else ev["ts"].iloc[0]


def _at(t, fault_time: pd.Timestamp) -> bool:
    """Is ``t`` the injected fault (decoders add up to ~1 s: QNX log write, UDS poll)?"""
    return t is not None and abs((pd.Timestamp(t) - fault_time).total_seconds()) <= 2


def run(seeds: range, source: Store) -> dict:
    trips = real_i3_trips(source)
    codec = CanCodec(REFERENCE_DBC_DIR, I3_FD_DBC_FILES, I3_FD_SIGNALS)
    rows = []
    for seed in seeds:
        sc = faults.random_scenario(seed, trips)
        tr = sc.traffic
        store = scenario_store(sc, codec)
        facts = Assistant(store, "i3-tum").ask(QUESTION).result.facts
        named = facts.get("root_cause_time")
        naive, map_only = earliest_fault_rule(store), map_only_rule(store)
        warn = max(t for t, code in tr.truth if code == "BATT_OVERTEMP_WARNING")
        rows.append(
            {
                "seed": seed,
                "base_trip": sc.base_trip,
                "fault": asdict(sc.fault),
                "fault_to_warning_min": round((warn - tr.fault_time).total_seconds() / 60, 1),
                "distractors": [code for _, code in sc.distractors],
                "distractors_before_fault": sum(t < tr.fault_time for t, _ in sc.distractors),
                "root_cause_correct": _at(named, tr.fault_time),
                "code_correct": tr.fault_dtc is None
                or tr.fault_dtc in (facts.get("root_cause_dtc"), facts.get("root_cause_code")),
                "named": facts.get("root_cause_dtc") or facts.get("root_cause_code"),
                "earliest_fault_rule_correct": _at(naive, tr.fault_time),
                "map_only_rule_correct": _at(map_only, tr.fault_time),
                "events_expected": len(tr.truth) + len(sc.distractors),
                "events_recovered": recovered(store, tr.truth + sc.distractors),
            }
        )
        store.close()

    def share(sub: list[dict], key: str) -> str:
        return f"{sum(r[key] for r in sub)}/{len(sub)}"

    by_kind = defaultdict(list)
    for r in rows:
        by_kind[r["fault"]["kind"]].append(r)
    summary = {
        "scenarios": len(rows),
        "root_cause_correct": share(rows, "root_cause_correct"),
        "code_correct": share(rows, "code_correct"),
        "earliest_fault_rule_correct": share(rows, "earliest_fault_rule_correct"),
        "map_only_rule_correct": share(rows, "map_only_rule_correct"),
        "with_a_same_subsystem_decoy": sum(
            any(c in ("QNX_THERMAL_MGR", "QNX_BMS_THERMAL") for c in r["distractors"]) for r in rows
        ),
        "events_recovered": f"{sum(r['events_recovered'] for r in rows)}/{sum(r['events_expected'] for r in rows)}",
        "with_a_distractor_before_the_fault": sum(r["distractors_before_fault"] > 0 for r in rows),
        "fault_more_than_15_min_before_warning": sum(r["fault_to_warning_min"] > 15 for r in rows),
        "by_kind": {
            k: {
                "n": len(v),
                "root_cause": share(v, "root_cause_correct"),
                "rule": share(v, "earliest_fault_rule_correct"),
            }
            for k, v in sorted(by_kind.items())
        },
    }
    return {"summary": summary, "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", type=Path, default=Path("data/edgecar.duckdb"), help="source of the real i3 trips")
    ap.add_argument("--seed", type=int, default=1000)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    source = Store(a.store, read_only=True)
    res = run(range(a.seed, a.seed + a.n), source)
    print(json.dumps(res["summary"], indent=2))
    for r in res["rows"]:
        if not r["root_cause_correct"]:
            print(f"  ✗ seed {r['seed']} {r['fault']['kind']}: named {r['named']} · distractors {r['distractors']}")
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
