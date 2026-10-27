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
from edgecar.store import Store

VED_VEHICLES = {"leaf-455": 455, "leaf-010": 10, "phev-457": 457}


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
    for vehicle_id, veh in VED_VEHICLES.items():
        frames = ved.to_canonical(raw[raw["VehId"] == veh], vehicle_id)
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
