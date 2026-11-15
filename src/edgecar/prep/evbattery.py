"""EVBattery (Tsinghua; CC BY 4.0) → one CSV row per charging snippet, for validating the battery-health estimate.

    python -m edgecar.prep.evbattery --part 3       # needs torch (the .venv-train environment) — offline prep only

Source: figshare.com/articles/dataset/23301881 — charging records of 464 EVs; each file is one 128-sample snippet
(10 s apart) of a charging session, with per-session capacity labels written by the manufacturer's engineers for
some sessions. Columns of a snippet: mean cell voltage, current (negative while charging), SoC, max / min cell
voltage, max / min temperature, seconds.

The files are ``torch.save`` pickles. Unpickling runs code, so they are opened with torch's restricted loader
(``weights_only=True``), which builds tensors and plain containers only; the two numpy constructors the files need
are allowed explicitly, nothing else.
"""

from __future__ import annotations

import argparse
import csv
import tarfile
from multiprocessing import Pool
from pathlib import Path

import numpy as np

URL = "https://ndownloader.figshare.com/files/{}"
PARTS = {1: "41076449", 2: "41076506", 3: "41076458"}  # battery_dataset{n}.tar.gz
RAW = Path("data/raw/evbattery")
OUT = Path("data/interim/evbattery")
MAX_GAP_S = 30.0


def load(path: Path):
    import torch
    from numpy._core.multiarray import _reconstruct, scalar

    safe = [
        (_reconstruct, "numpy.core.multiarray._reconstruct"),
        (scalar, "numpy.core.multiarray.scalar"),
        np.ndarray,
        np.dtype,
        type(np.dtype("float64")),
        type(np.dtype("float32")),
        type(np.dtype("int64")),
    ]
    with torch.serialization.safe_globals(safe):
        return torch.load(path, weights_only=True)


def summarise(path: Path) -> tuple:
    """Charge put in (Ah, trapezoid, skipping gaps) and SoC gained over one snippet, with its labels."""
    arr, meta = load(path)
    t, i, soc = arr[:, 7], arr[:, 1], arr[:, 2]
    dt = np.diff(t)
    ok = (dt > 0) & (dt <= MAX_GAP_S)
    ah = float(np.sum(np.where(ok, -0.5 * (i[1:] + i[:-1]) * dt, 0.0)) / 3600.0)
    return (
        path.name,
        int(meta["car"]),
        str(meta["charge_segment"]),
        float(meta["capacity"] or 0.0),
        float(meta["mileage"]),
        round(ah, 5),
        round(float(soc[-1] - soc[0]), 3),
        float(soc[0]),
        float(arr[:, 5].mean()),
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--part", type=int, choices=sorted(PARTS), default=3)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    archive = RAW / f"battery_dataset{a.part}.tar.gz"
    if not archive.exists():
        raise SystemExit(f"download {URL.format(PARTS[a.part])} to {archive} first")
    root = RAW / "x"
    data = root / f"battery_dataset{a.part}" / "data"
    if not data.exists():
        with tarfile.open(archive) as tf:
            tf.extractall(root, filter="data")  # the "data" filter refuses absolute paths, links out, devices
    files = sorted(data.glob("*.pkl"))
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"snippets{a.part}.csv"
    with Pool(a.workers) as pool, out.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "car", "segment", "capacity", "mileage", "ah", "dsoc", "soc0", "tmax"])
        for row in pool.imap_unordered(summarise, files, chunksize=200):
            w.writerow(row)
    print(f"{len(files)} snippets → {out}")


if __name__ == "__main__":
    main()
