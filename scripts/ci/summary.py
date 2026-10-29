"""Turn the ARM benchmark outputs into one summary (Markdown on stdout, JSON next to the inputs).

python scripts/ci/summary.py eval/results/arm/<model> >> "$GITHUB_STEP_SUMMARY"
"""

import json
import re
import sys
from pathlib import Path

d = Path(sys.argv[1])


def load(name: str):
    p = d / name
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except json.JSONDecodeError:
        return {}


cpu_txt = (d / "cpu.txt").read_text() if (d / "cpu.txt").exists() else ""
cpu = re.search(r"Model name:\s*(.+)", cpu_txt)
e2e, cold, held, cg = load("e2e.json"), load("coldstart.json"), load("heldout.json"), load("cgroup.json")
refusal, brief = load("refusal.json"), load("brief.json")
live = (d / "live.txt").read_text() if (d / "live.txt").exists() else ""
commit = re.search(r"p50 ([\d.]+) ms · p95 ([\d.]+) ms", live)

bench = {}
for repack in (0, 1):
    for row in load(f"llama-bench-repack{repack}.json") or []:
        kind = f"pp{row['n_prompt']}" if row.get("n_prompt") else f"tg{row['n_gen']}"
        bench[f"repack{repack}_{kind}_tok_s"] = round(row["avg_ts"], 1)

summary = {
    "model": e2e.get("model") or held.get("model"),
    "cpu": cpu.group(1).strip() if cpu else None,
    "cores": cg.get("cpus"),
    "memory_limit_mb": cg.get("memory_max_mb"),
    "memory_peak_mb": cg.get("memory_peak_mb"),
    "sampled_peak_mb": cg.get("sampled_peak_mb"),
    "system_mem_total_mb": cg.get("system_mem_total_mb"),
    "system_used_peak_mb": cg.get("system_used_peak_mb"),
    "oom_kills": cg.get("memory_events", {}).get("oom_kill"),
    "limit_hits": cg.get("memory_events", {}).get("max"),
    "questions": e2e.get("questions"),
    "latency_ms": {k: e2e.get(f"latency_ms_{k}") for k in ("fast", "slm", "unsupported", "all")},
    "engine_ms": e2e.get("engine_ms"),
    "heldout_accuracy": held.get("accuracy"),
    "heldout_n": held.get("n"),
    "rules_first_answer_s": cold.get("rules_first_answer_s"),
    "model_first_boot_s": cold.get("model_first_boot", {}).get("ready_s"),
    "model_next_boot_s": cold.get("model_next_boot", {}).get("ready_s"),
    "live_commit_ms": {"p50": float(commit.group(1)), "p95": float(commit.group(2))} if commit else None,
    "llama_bench_2_threads": bench,
    "refusal_declined": f"{round(refusal['accuracy'] * refusal['n'])}/{refusal['n']}" if refusal else None,
    "brief": [{k: r[k] for k in ("vehicle", "question", "route", "ms")} for r in brief] if brief else None,
}
(d / "summary.json").write_text(json.dumps(summary, indent=2))


def lat(k: str) -> str:
    v = summary["latency_ms"].get(k) or {}
    return f"p50 {v.get('p50', '–')} ms · p95 {v.get('p95', '–')} ms · max {v.get('max', '–')} ms (n={v.get('n', '–')})"


rows = [
    ("CPU", f"{summary['cpu']} — cores {summary['cores']}"),
    ("Memory limit (whole AI zone, no swap)", f"{summary['memory_limit_mb']} MiB"),
    ("Peak memory: kernel high-water mark", f"{summary['memory_peak_mb']} MiB"),
    ("Peak memory: anonymous / page cache (sampled)", json.dumps(summary["sampled_peak_mb"])),
    (
        "Whole machine incl. OS: peak used / total",
        f"{summary['system_used_peak_mb']} MiB / {summary['system_mem_total_mb']} MiB",
    ),
    ("OOM kills · times the limit was hit", f"{summary['oom_kills']} · {summary['limit_hits']}"),
    ("Rules path (deterministic)", lat("fast")),
    (f"Model path ({summary['model']}, grammar-constrained)", lat("slm")),
    ("All questions", lat("all") + f" · {(summary['latency_ms'].get('all') or {}).get('under_2s_pct', '–')} % < 2 s"),
    ("Held-out accuracy", f"{summary['heldout_accuracy']:.1%} of {summary['heldout_n']}" if held else "–"),
    ("Cold start: first answer (rules)", f"{summary['rules_first_answer_s']} s"),
    (
        "Cold start: model ready (computed / restored prefix)",
        f"{summary['model_first_boot_s']} s / {summary['model_next_boot_s']} s",
    ),
    ("Live ingest commit latency per 1 s batch", json.dumps(summary["live_commit_ms"])),
    ("llama-bench, 2 threads (tokens/s)", json.dumps(bench)),
]
print(f"## EdgeCar AI — ARM64 edge benchmark · {summary['model']}\n")
print("| | |\n|---|---|")
for k, v in rows:
    print(f"| {k} | {v} |")
