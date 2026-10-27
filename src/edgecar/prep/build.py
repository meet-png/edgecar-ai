"""Build the on-device store from the fetched public datasets.

python -m edgecar.prep.build --data data --out data/edgecar.duckdb
"""

from __future__ import annotations

import argparse
import time
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from edgecar.detect import detect_all
from edgecar.fleet import VEHICLES
from edgecar.prep import tum, ved
from edgecar.protocols.can import CanCodec, read_log, segment_trips, write_log
from edgecar.store import Store

VED_VEHICLES = {"leaf-455": 455, "leaf-010": 10, "phev-457": 457}
CAN_REPLAY = {"leaf-455", "leaf-010"}  # vehicles with a real DBC: their data reaches the store as decoded CAN frames


def replay_over_can(codec: CanCodec, frames: pd.DataFrame, vehicle_id: str, can_dir: Path) -> pd.DataFrame:
    """Encode real signals into Leaf CAN frames (candump log), decode them back, and segment trips from bus activity.

    GNSS position is not carried on CAN; on the vehicle it arrives from the head unit, so it is joined by timestamp.
    """
    can_dir.mkdir(parents=True, exist_ok=True)
    log = can_dir / f"{vehicle_id}.log"
    t0 = time.perf_counter()
    n_frames = write_log(codec.encode(frames), log)
    decoded = segment_trips(codec.decode(read_log(log)), vehicle_id)
    decoded = decoded.merge(frames[["ts", "lat_deg", "lon_deg"]].dropna(), on="ts", how="left")
    # round-trip check against the source values
    both = decoded.merge(frames, on="ts", suffixes=("", "_src"))
    errs = {m.canonical: float((both[m.canonical] - both[f"{m.canonical}_src"]).abs().max()) for m in codec.signals}
    worst = ", ".join(f"{k} {v:.3g}" for k, v in errs.items())
    print(
        f"{vehicle_id:10s} CAN replay: {n_frames:,} frames → {log.name} ({log.stat().st_size / 1e6:.1f} MB) "
        f"in {time.perf_counter() - t0:.1f} s; trips from bus activity: {decoded['trip_id'].nunique()}; "
        f"max |decoded − source|: {worst}"
    )
    return decoded


def build(data_dir: Path, out: Path) -> None:
    t0 = time.perf_counter()
    if out.exists():
        out.unlink()
    store = Store(out, threads=4, memory_limit="2GB")
    for v in VEHICLES.values():
        store.upsert_vehicle(asdict(v) | {"provenance": str(v.provenance)})

    cache = data_dir / "interim" / "ved_selected.csv.gz"
    if cache.exists():
        raw = pd.read_csv(cache)
    else:
        raw = ved.read_raw(data_dir / "raw" / "ved", VED_VEHICLES.values())
        cache.parent.mkdir(parents=True, exist_ok=True)
        raw.to_csv(cache, index=False, compression="gzip")
    leaf_dbc = data_dir / "raw" / "leaf_dbc"
    codec = CanCodec(leaf_dbc) if leaf_dbc.exists() else None
    for vehicle_id, veh in VED_VEHICLES.items():
        frames = ved.to_canonical(raw[raw["VehId"] == veh], vehicle_id)
        if codec is not None and vehicle_id in CAN_REPLAY:
            frames = replay_over_can(codec, frames, vehicle_id, data_dir / "interim" / "can")
        n = store.append_frames(frames, str(VEHICLES[vehicle_id].provenance))
        print(f"{vehicle_id:10s} {n:>9,} frames")

    frames = tum.to_canonical(data_dir / "raw" / "tum_bmw_i3")
    n = store.append_frames(frames, str(VEHICLES["i3-tum"].provenance))
    print(f"{'i3-tum':10s} {n:>9,} frames")

    print(f"trips: {store.rebuild_trips():,}   minute roll-ups: {store.rebuild_rollups():,}")
    print(f"events detected from real signals: {detect_all(store):,}")
    store.con.execute("CHECKPOINT")
    store.close()
    print(f"store: {out} ({out.stat().st_size / 1e6:.1f} MB) in {time.perf_counter() - t0:.1f} s")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", type=Path, default=Path("data"))
    p.add_argument("--out", type=Path, default=Path("data/edgecar.duckdb"))
    a = p.parse_args()
    build(a.data, a.out)


if __name__ == "__main__":
    main()
