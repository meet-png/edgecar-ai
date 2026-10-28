"""Score question → command parsing against a labelled set.

    python -m edgecar.bench.nlu_eval --mode hybrid --model models/qwen2.5-0.5b-instruct-q4_k_m.gguf

Modes: ``fast`` (rules only), ``slm`` (model only, every question), ``hybrid`` (rules, then model — production).
A prediction is correct when its canonical command equals one of the question's accepted commands.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path

from edgecar.nlu import command
from edgecar.nlu.pipeline import understand
from edgecar.nlu.slm import PROFILES, SlmParser, SlmServer, prompt_style


def normalise(line: str, gold: str) -> str:
    """Canonical form; an unrequested ``lookback`` on events_before is ignored (the engine default applies)."""
    try:
        q = command.parse(line)
    except ValueError:
        return f"INVALID<{line}>"
    if q is not None and q.intent == "events_before" and "lookback=" not in gold:
        q = q.model_copy(update={"lookback_min": None})
    return command.render(q)


def pct(xs: list[float], p: float) -> float:
    return statistics.quantiles(xs, n=100)[p - 1] if len(xs) >= 2 else (xs[0] if xs else float("nan"))


def run(questions: list[dict], mode: str, slm: SlmParser | None) -> dict:
    rows = []
    for item in questions:
        t0 = time.perf_counter()
        u = understand(item["q"], slm if mode != "fast" else None, use_fast=mode != "slm")
        ms = (time.perf_counter() - t0) * 1000
        golds = [command.render(command.parse(g)) for g in item["gold"]]
        # declined by deterministic checks counts as the rules path; declined by or after the model as the model path
        path = {"fast": "fast", "refused": "fast", "unavailable": "fast", "slm": "slm", "unsupported": "slm"}.get(
            u.path, "none"
        )
        pred = normalise(command.render(u.query), golds[0])
        rows.append(
            {
                "id": item["id"],
                "q": item["q"],
                "gold": golds,
                "pred": pred,
                "path": path,
                "model_line": u.model_line,
                "ok": pred in golds,
                "ms": ms,
            }
        )
    by_cat = defaultdict(list)
    for r in rows:
        by_cat["-".join(r["id"].split("-")[:-1])].append(r["ok"])
    out = {
        "mode": mode,
        "n": len(rows),
        "accuracy": sum(r["ok"] for r in rows) / len(rows),
        "by_category": {k: round(sum(v) / len(v), 3) for k, v in sorted(by_cat.items())},
        "paths": {p: sum(r["path"] == p for r in rows) for p in ("fast", "slm", "none")},
        "rows": rows,
    }
    for p in ("fast", "slm"):
        sub = [r for r in rows if r["path"] == p]
        if sub:
            out[f"{p}_accuracy"] = sum(r["ok"] for r in sub) / len(sub)
            ms = [r["ms"] for r in sub]
            out[f"{p}_ms"] = {"p50": round(pct(ms, 50), 1), "p95": round(pct(ms, 95), 1), "max": round(max(ms), 1)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--questions", type=Path, default=Path("eval/questions.jsonl"))
    ap.add_argument("--mode", choices=["fast", "slm", "hybrid"], default="hybrid")
    ap.add_argument("--model", type=Path, default=Path("models/qwen2.5-0.5b-instruct-q4_k_m.gguf"))
    ap.add_argument("--server", type=Path, default=Path("tools/llama.cpp/llama-server.exe"))
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--profile", choices=sorted(PROFILES), default="edge")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()

    questions = [json.loads(line) for line in a.questions.read_text(encoding="utf-8").splitlines() if line.strip()]
    if a.mode == "fast":
        res = run(questions, "fast", None)
    else:
        with SlmServer(a.server, a.model, threads=a.threads, **PROFILES[a.profile]) as srv:
            slm = SlmParser(srv.url, style=prompt_style(a.model))
            slm.warm_up()
            res = run(questions, a.mode, slm)
    res["model"] = a.model.name if a.mode != "fast" else None
    res["threads"] = a.threads
    print(f"mode={a.mode} model={res['model']} n={res['n']} accuracy={res['accuracy']:.1%} paths={res['paths']}")
    for p in ("fast", "slm"):
        if f"{p}_accuracy" in res:
            print(f"  {p:4s}: accuracy {res[f'{p}_accuracy']:.1%}  latency {res[f'{p}_ms']}")
    print("  by category:", res["by_category"])
    for r in res["rows"]:
        if not r["ok"]:
            raw = f"   (model: {r['model_line']})" if r["model_line"] else ""
            print(f"  ✗ [{r['path']}] {r['id']}: {r['q']}\n      got  {r['pred']}{raw}\n      want {r['gold'][0]}")
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
