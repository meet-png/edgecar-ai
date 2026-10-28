"""Compare candidate small language models on the same task, budget and hardware settings.

    python -m edgecar.bench.models --threads 2

For each GGUF model: model-only parsing accuracy on all 192 labelled questions (the model's own ability), hybrid
accuracy on the 70-question held-out set (what ships), model-path latency, and resident memory of the server.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

import psutil

from edgecar.bench.nlu_eval import run
from edgecar.nlu.slm import PROFILES, SlmParser, SlmServer, prompt_style

CANDIDATES = [
    "qwen2.5-0.5b-instruct-q4_k_m.gguf",
    "Qwen3-0.6B-Q8_0.gguf",
    "Llama-3.2-1B-Instruct-Q4_K_M.gguf",
    "gemma-3-1b-it-Q4_K_M.gguf",
    "qwen2.5-1.5b-instruct-q4_k_m.gguf",
    "smollm2-1.7b-instruct-q4_k_m.gguf",
]


def load(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", type=Path, default=Path("models"))
    ap.add_argument("--server", type=Path, default=Path("tools/llama.cpp/llama-server.exe"))
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--out", type=Path, default=Path("eval/results/models.json"))
    a = ap.parse_args()

    dev, test = load(Path("eval/questions.jsonl")), load(Path("eval/test.jsonl"))
    rows = []
    for name in CANDIDATES:
        path = a.models / name
        if not path.exists():
            print(f"skip {name} (not downloaded)")
            continue
        with SlmServer(a.server, path, threads=a.threads, **PROFILES["edge"]) as srv:
            proc = psutil.Process(srv.pid)
            slm = SlmParser(srv.url, style=prompt_style(path))
            warm = slm.warm_up()
            only = run(dev + test, "slm", slm)
            hybrid = run(test, "hybrid", slm)
            mem = proc.memory_info()
            row = {
                "model": name,
                "file_mb": round(path.stat().st_size / 1e6),
                "slm_only_accuracy_192": round(only["accuracy"], 3),
                "hybrid_heldout_accuracy_70": round(hybrid["accuracy"], 3),
                "slm_p50_ms": round(statistics.median(r["ms"] for r in only["rows"]), 0),
                "slm_p95_ms": round(statistics.quantiles([r["ms"] for r in only["rows"]], n=20)[18], 0),
                "rss_mb": round(mem.rss / 1e6),
                "private_mb": round(mem.private / 1e6) if hasattr(mem, "private") else None,
                "prefix_tokens": warm.prompt_tokens,
            }
        rows.append(row)
        print(json.dumps(row))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print("\n| Model | File | Model-only (192) | Hybrid held-out (70) | p50 / p95 | RSS (private) |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        print(
            f"| {r['model']} | {r['file_mb']} MB | {r['slm_only_accuracy_192']:.1%} | "
            f"{r['hybrid_heldout_accuracy_70']:.1%} | {r['slm_p50_ms']:.0f} / {r['slm_p95_ms']:.0f} ms | "
            f"{r['rss_mb']} MB ({r['private_mb']} MB) |"
        )


if __name__ == "__main__":
    main()
