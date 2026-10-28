"""Command line: ask the car a question, or run the case study's example questions.

    edgecar ask "Why has my range dropped over the last two weeks?" --vehicle leaf-455
    edgecar brief --out eval/results/brief.json

With a model file present, llama-server is started (prompt prefix restored from ``data/cache`` after the first
run); without one, only the rules path answers.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from contextlib import nullcontext
from pathlib import Path

from edgecar.assistant import Assistant
from edgecar.fleet import VEHICLES
from edgecar.nlu.slm import PROFILES, SlmParser, SlmServer
from edgecar.privacy import Role
from edgecar.store import Store

# The case study's five example questions, each asked of the demo car it fits, plus a paraphrase only the model
# understands and a request the assistant must refuse.
BRIEF = [
    ("leaf-455", "Why has my driving range dropped over the last two weeks?"),
    ("leaf-455", "When did fuel efficiency show the largest drop?"),
    ("phev-457", "Show all ADAS disengagement events from yesterday."),
    ("i3-tum", "What happened before the battery overheating warning?"),
    ("i3-tum", "Compare energy consumption between city and highway driving."),
    ("leaf-455", "Is the car using more power than it used to?"),
    ("i3-tum", "Turn off lane keeping assist"),
]
ROUTE = {"fast": "rules", "slm": "model", "refused": "declined", "unavailable": "declined", "unsupported": "declined"}


def _server_binary(given: Path | None) -> Path | None:
    for p in [given, Path("tools/llama.cpp/llama-server.exe"), Path("tools/llama.cpp/llama-server")]:
        if p is not None and p.exists():
            return p
    found = shutil.which("llama-server")
    return Path(found) if found else None


def _model_context(a: argparse.Namespace):
    server = _server_binary(a.server)
    if a.no_model or server is None or not a.model.exists():
        return nullcontext(None)
    return SlmServer(server, a.model, threads=a.threads, cache_dir=Path("data/cache"), **PROFILES["edge"])


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog="edgecar", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    ask = sub.add_parser("ask", help="answer one question")
    ask.add_argument("question")
    ask.add_argument("--vehicle", default="leaf-455", choices=sorted(VEHICLES))
    ask.add_argument("--role", default="driver", choices=[r.value for r in Role])
    brief = sub.add_parser("brief", help="the case study's example questions, with route and latency")
    brief.add_argument("--out", type=Path, default=None)
    for p in (ask, brief):
        p.add_argument("--store", type=Path, default=Path("data/edgecar.duckdb"))
        p.add_argument("--model", type=Path, default=Path("models/qwen2.5-1.5b-instruct-q4_k_m.gguf"))
        p.add_argument("--server", type=Path, default=None)
        p.add_argument("--threads", type=int, default=2)
        p.add_argument("--no-model", action="store_true", help="rules only")
    a = ap.parse_args(argv)

    store = Store(a.store, read_only=True, threads=a.threads)
    with _model_context(a) as srv:
        slm = SlmParser(srv.url) if srv else None
        if slm:
            slm.ready(a.model)
        if a.cmd == "ask":
            ans = Assistant(store, a.vehicle, slm, role=a.role).ask(a.question)
            print(ans.text)
            print(f"[{ROUTE.get(ans.path, ans.path)} · {ans.command or '—'} · {ans.timings_ms['total']:.0f} ms]")
            return
        rows = []
        for vehicle, question in BRIEF:
            ans = Assistant(store, vehicle, slm).ask(question)
            rows.append(
                {
                    "vehicle": vehicle,
                    "question": question,
                    "route": ROUTE.get(ans.path, ans.path),
                    "command": ans.command,
                    "ms": round(ans.timings_ms["total"], 1),
                    "answer": ans.text,
                    "facts": ans.result.facts if ans.result else {},
                    "table": ans.result.table if ans.result else None,
                }
            )
            print(f"\n{vehicle} · {question}\n  [{rows[-1]['route']} · {ans.command or '—'} · {rows[-1]['ms']} ms]")
            print(f"  {ans.text}")
        if a.out:
            a.out.parent.mkdir(parents=True, exist_ok=True)
            a.out.write_text(json.dumps(rows, indent=2, default=str, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
