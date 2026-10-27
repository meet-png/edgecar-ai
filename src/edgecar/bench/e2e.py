"""End-to-end benchmark: every question through understanding → analytics → answer, with the model loaded.

    python -m edgecar.bench.e2e --model models/qwen2.5-1.5b-instruct-q4_k_m.gguf --threads 2

Reports latency percentiles per path and the resident memory of both processes (assistant + llama-server).
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
from pathlib import Path

import psutil

from edgecar.assistant import Assistant
from edgecar.nlu.slm import PROFILES, SlmParser, SlmServer
from edgecar.store import Store


def pctl(xs: list[float], p: int) -> float:
    return round(statistics.quantiles(xs, n=100)[p - 1], 1) if len(xs) >= 2 else round(xs[0], 1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", type=Path, default=Path("data/edgecar.duckdb"))
    ap.add_argument("--vehicle", default="leaf-455")
    ap.add_argument(
        "--questions", type=Path, nargs="+", default=[Path("eval/questions.jsonl"), Path("eval/test.jsonl")]
    )
    ap.add_argument("--model", type=Path, default=Path("models/qwen2.5-1.5b-instruct-q4_k_m.gguf"))
    ap.add_argument("--server", type=Path, default=Path("tools/llama.cpp/llama-server.exe"))
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--profile", choices=sorted(PROFILES), default="edge")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()

    questions = [
        json.loads(line)["q"]
        for f in a.questions
        for line in f.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    me = psutil.Process()
    t_boot = time.perf_counter()
    with SlmServer(a.server, a.model, threads=a.threads, **PROFILES[a.profile]) as srv:
        server = psutil.Process(srv.pid)
        slm = SlmParser(srv.url)
        warm = slm.warm_up()
        boot_s = time.perf_counter() - t_boot
        store = Store(a.store, read_only=True, threads=a.threads)
        assistant = Assistant(store, a.vehicle, slm)
        rows, peak_server, peak_me = [], 0, 0
        for q in questions:
            ans = assistant.ask(q)
            rows.append(
                {
                    "q": q,
                    "path": ans.path,
                    "total_ms": ans.timings_ms["total"],
                    "engine_ms": ans.timings_ms.get("engine", 0.0),
                }
            )
            peak_server = max(peak_server, server.memory_info().rss)
            peak_me = max(peak_me, me.memory_info().rss)
        server_private = server.memory_info().private if hasattr(server.memory_info(), "private") else None

    report = {
        "machine": f"{platform.processor() or platform.machine()} ({psutil.cpu_count(logical=False)} cores)",
        "threads": a.threads,
        "profile": a.profile,
        "model": a.model.name,
        "model_file_mb": round(a.model.stat().st_size / 1e6),
        "boot_s": round(boot_s, 1),
        "warmup_prompt_tokens": warm.prompt_tokens,
        "questions": len(rows),
        "peak_rss_mb": {
            "llama_server": round(peak_server / 1e6),
            "assistant": round(peak_me / 1e6),
            "total": round((peak_server + peak_me) / 1e6),
        },
        "llama_server_private_mb": round(server_private / 1e6) if server_private else None,
    }
    for path in ("fast", "slm", "unsupported"):
        ms = [r["total_ms"] for r in rows if r["path"] == path]
        if ms:
            report[f"latency_ms_{path}"] = {
                "n": len(ms),
                "p50": pctl(ms, 50),
                "p95": pctl(ms, 95),
                "max": round(max(ms), 1),
            }
    all_ms = [r["total_ms"] for r in rows]
    report["latency_ms_all"] = {
        "p50": pctl(all_ms, 50),
        "p95": pctl(all_ms, 95),
        "max": round(max(all_ms), 1),
        "under_2s_pct": round(100 * sum(m < 2000 for m in all_ms) / len(all_ms), 1),
    }
    eng = [r["engine_ms"] for r in rows if r["engine_ms"]]
    report["engine_ms"] = {"p50": pctl(eng, 50), "p95": pctl(eng, 95), "max": round(max(eng), 1)}
    print(json.dumps(report, indent=2))
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(report | {"rows": rows}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
