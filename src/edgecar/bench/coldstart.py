"""Cold start ("ignition"): how soon after power-up can the assistant answer?

    python -m edgecar.bench.coldstart --model models/qwen2.5-1.5b-instruct-q4_k_m.gguf --threads 2

Measured from process start, in fresh processes:

* **rules path** — interpreter start, store open, first answered question (no model involved);
* **model path** — llama-server start until the model is loaded, then until the shared prompt prefix is in the KV
  cache: once by computing it (first boot) and once by restoring the cache saved by that first boot.

``--drop-caches`` (Linux, root) empties the OS page cache before each start, so the model is read from storage.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from edgecar.nlu.slm import PROFILES, SlmParser, SlmServer, prompt_style

FIRST_QUESTION = "How far did I drive last week?"


def drop_caches() -> None:
    subprocess.run(["sync"], check=True)
    Path("/proc/sys/vm/drop_caches").write_text("3\n")


def rules_first_answer(store: Path, vehicle: str) -> tuple[float, str]:
    """Wall time from launching a fresh interpreter to its first answer (fast path, no model)."""
    child = (
        "from edgecar.store import Store; from edgecar.assistant import Assistant; "
        f"a = Assistant(Store({str(store)!r}, read_only=True), {vehicle!r}); "
        f"print(a.ask({FIRST_QUESTION!r}).text, flush=True)"
    )
    t0 = time.perf_counter()
    out = subprocess.run([sys.executable, "-c", child], capture_output=True, text=True, check=True, encoding="utf-8")
    return time.perf_counter() - t0, out.stdout.strip()


def model_ready(server: Path, model: Path, threads: int, profile: str, cache_dir: Path, drop: bool) -> dict:
    if drop:
        drop_caches()
    t0 = time.perf_counter()
    with SlmServer(server, model, threads=threads, cache_dir=cache_dir, **PROFILES[profile]) as srv:
        loaded = time.perf_counter() - t0
        slm = SlmParser(srv.url, style=prompt_style(model))
        how = slm.ready(model)
        ready = time.perf_counter() - t0
        first = slm.parse("why did my range drop")  # the first model answer after start-up
    return {
        "prefix": how,
        "model_loaded_s": round(loaded, 2),
        "ready_s": round(ready, 2),
        "first_parse_ms": round(first.total_ms, 1),
        "first_parse_prompt_tokens": first.prompt_tokens,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", type=Path, default=Path("data/edgecar.duckdb"))
    ap.add_argument("--vehicle", default="leaf-455")
    ap.add_argument("--model", type=Path, default=Path("models/qwen2.5-1.5b-instruct-q4_k_m.gguf"))
    ap.add_argument("--server", type=Path, default=Path("tools/llama.cpp/llama-server.exe"))
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--profile", choices=sorted(PROFILES), default="edge")
    ap.add_argument("--drop-caches", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()

    rules_s, text = rules_first_answer(a.store, a.vehicle)
    cache_dir = Path(tempfile.mkdtemp(prefix="edgecar-kv-"))
    try:
        runs = [model_ready(a.server, a.model, a.threads, a.profile, cache_dir, a.drop_caches) for _ in range(2)]
        saved = sorted(cache_dir.glob("*.kv"))
        kv_mb = round(saved[0].stat().st_size / 1e6, 1) if saved else None
    finally:
        shutil.rmtree(cache_dir, ignore_errors=True)
    report = {
        "machine": platform.machine(),
        "threads": a.threads,
        "drop_caches": a.drop_caches,
        "rules_first_answer_s": round(rules_s, 2),
        "rules_first_answer": text[:120],
        "model_first_boot": runs[0],
        "model_next_boot": runs[1],
        "saved_prefix_kv_mb": kv_mb,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
