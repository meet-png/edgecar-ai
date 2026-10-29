"""Robustness: the blind questions again, the way people actually type and speak them.

    python -m edgecar.bench.robustness                                    # rules only, text perturbations
    python -m edgecar.bench.robustness --model models/edgecar-1.5b-q4_k_m.gguf --voice models/voice

Every held-out question (and every refusal-test request) is rewritten, deterministically, in several ways and scored
exactly like ``edgecar.bench.nlu_eval`` — same accepted answers, same pipeline:

=========  ===========================================================================================================
plain      lower case, no punctuation (how a phone keyboard or a speech recogniser delivers text)
typos      about one word in four mistyped: two letters swapped, one dropped or doubled
filler     spoken padding around the question: "um", "hey car,", "… please"
spoken     what a recogniser writes: numbers and units as words ("10 days" → "ten days", kWh → kilowatt hours)
voice      the real thing: the question synthesised by the on-device Piper voice and transcribed by Moonshine
=========  ===========================================================================================================

The refusal set matters most here: a misspelt "turn of lane keepng assist" must still be declined. Each variant is
compared with the clean questions on the same items (paired exact McNemar test); results go to
``eval/results/robustness.md`` and ``eval/results/robustness/``.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

from edgecar.bench.nlu_eval import run
from edgecar.nlu.slm import PROFILES, SlmParser, SlmServer, find_server, prompt_style
from edgecar.stats import mcnemar_exact, wilson

SETS = {"held-out": "eval/test.jsonl", "refusal": "eval/refusal_test.jsonl"}
DEV_SETS = {"dev": "eval/questions.jsonl", "refusal-dev": "eval/refusal_dev.jsonl"}  # for building fixes
PORT = 8099  # not the benchmarks' 8089, so a run here never queries another session's model server

_ONES = (  # noqa: SIM905
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
    "eighteen nineteen"
).split()
_TENS = ["_", "_", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def number_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        rest = n % 100
        return _ONES[n // 100] + " hundred" + ("" if rest == 0 else " and " + number_words(rest))
    return str(n)


def plain(q: str, rng: random.Random | None = None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s'%°/]", " ", q.lower())).strip()


def typos(q: str, rng: random.Random) -> str:
    out = []
    for w in q.split():
        if len(w) >= 4 and w.isalpha() and rng.random() < 0.25:
            i = rng.randrange(1, len(w) - 1)
            w = rng.choice(
                [
                    w[:i] + w[i + 1] + w[i] + w[i + 2 :],  # swap two letters
                    w[:i] + w[i + 1 :],  # drop one
                    w[:i] + w[i] + w[i:],  # double one
                ]
            )
        out.append(w)
    return " ".join(out)


def filler(q: str, rng: random.Random) -> str:
    head = rng.choice(["um ", "hey car, ", "so uh ", "okay so ", "quick question, "])
    tail = rng.choice(["", "", " please", " thanks"])
    return head + q[0].lower() + q[1:].rstrip("?.!") + tail


_UNITS = [
    (r"kwh\s*/\s*100\s*km", "kilowatt hours per hundred kilometers"),
    (r"km\s*/\s*kwh", "kilometers per kilowatt hour"),
    (r"\bkwh\b", "kilowatt hours"),
    (r"\bkm\b", "kilometers"),
    (r"%", " percent"),
    (r"°\s*c\b", " degrees"),
    (r"\badas\b", "a das"),
    (r"\bdtcs?\b", "d t c"),
    (r"\bsoc\b", "s o c"),
]


def spoken(q: str, rng: random.Random | None = None) -> str:
    text = q.lower()
    for pattern, words in _UNITS:
        text = re.sub(pattern, words, text)
    text = re.sub(r"\d+", lambda m: number_words(int(m.group())), text)
    return plain(text)


TEXT_VARIANTS = {"plain": plain, "typos": typos, "filler": filler, "spoken": spoken}


def variant(items: list[dict], name: str, transform) -> list[dict]:
    """The same items with rewritten questions; each item's randomness is seeded by its id, so reruns agree."""
    return [{**it, "q": transform(it["q"], random.Random(f"{name}:{it['id']}"))} for it in items]


def voice_variant(items: list[dict], root: Path) -> tuple[list[dict], dict]:
    """Questions spoken by the Piper voice and heard by Moonshine — both on the CPU, as in the car."""
    from edgecar.voice import MAX_SECONDS, Voice, read_wav

    v = Voice(root)
    if not (v.can_listen and v.can_speak):
        raise SystemExit(f"voice models missing under {root}: python -m edgecar.prep.fetch voice")
    out, stt_ms, clipped = [], [], 0
    for it in items:
        wav = v.speak(it["q"])
        samples, rate = read_wav(wav)
        clipped += len(samples) / rate > MAX_SECONDS
        t0 = time.perf_counter()
        heard = v.transcribe(wav)
        stt_ms.append((time.perf_counter() - t0) * 1000)
        out.append({**it, "q": heard, "spoken_from": it["q"]})
    stt_ms.sort()
    return out, {"stt_ms_p50": round(stt_ms[len(stt_ms) // 2], 1), "clipped_over_9s": clipped}


def summarise(clean: dict, other: dict) -> dict:
    a = {r["id"]: r["ok"] for r in clean["rows"]}
    b = {r["id"]: r["ok"] for r in other["rows"]}
    kept_wrong = sum(a[i] and not b[i] for i in a)  # right when clean, wrong when perturbed
    fixed = sum(b[i] and not a[i] for i in a)
    k, n = sum(b.values()), len(b)
    return {"k": k, "n": n, "lost": kept_wrong, "gained": fixed, "p": mcnemar_exact(kept_wrong, fixed)}


def cell(s: dict) -> str:
    lo, hi = wilson(s["k"], s["n"])
    change = f" · −{s['lost']}/+{s['gained']}, p = {s['p']:.2f}" if s.get("p") is not None else ""
    return f"{s['k']}/{s['n']} = **{s['k'] / s['n']:.1%}** ({lo:.0%}–{hi:.0%}){change}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", type=Path, default=None, help="also score rules + model (hybrid)")
    ap.add_argument("--server", type=Path, default=None)
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--voice", type=Path, default=None, help="speech models folder: adds the spoken round trip")
    ap.add_argument("--out", type=Path, default=Path("eval/results"))
    ap.add_argument(
        "--dev",
        action="store_true",
        help="perturb the dev sets instead: for building a fix without seeing blind errors",
    )
    a = ap.parse_args()
    which, stem = (DEV_SETS, "robustness-dev") if a.dev else (SETS, "robustness")

    sets = {
        name: [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines() if x.strip()]
        for name, p in which.items()
    }
    variants: dict[str, dict[str, list[dict]]] = {name: {"clean": items} for name, items in sets.items()}
    voice_info: dict[str, dict] = {}
    for name, items in sets.items():
        for v, fn in TEXT_VARIANTS.items():
            variants[name][v] = variant(items, v, fn)
        if a.voice:
            variants[name]["voice"], voice_info[name] = voice_variant(items, a.voice)

    modes = ["fast"] + (["hybrid"] if a.model else [])
    results: dict[tuple[str, str, str], dict] = {}

    def score(mode: str, slm: SlmParser | None) -> None:
        for name, vs in variants.items():
            for v, items in vs.items():
                results[name, v, mode] = run(items, mode, slm)

    score("fast", None)
    if a.model:
        with SlmServer(find_server(a.server), a.model, port=PORT, threads=a.threads, **PROFILES["edge"]) as srv:
            slm = SlmParser(srv.url, style=prompt_style(a.model))
            slm.warm_up()
            score("hybrid", slm)

    raw = a.out / stem
    raw.mkdir(parents=True, exist_ok=True)
    for (name, v, mode), res in results.items():
        out = raw / f"{name}-{v}-{mode}.json"
        out.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8", newline="\n")

    labels = {"fast": "rules only", "hybrid": f"rules + {a.model.stem if a.model else 'model'}"}
    lines = [
        "# Robustness: the " + ("dev" if a.dev else "blind") + " questions as people type and say them",
        "",
        "Generated by `python -m edgecar.bench.robustness` (x86 laptop, 2 threads). Each cell: accuracy (95 % Wilson",
        "interval) · questions lost / gained against the clean wording, with a paired exact McNemar p-value.",
        "The *voice* rows are synthetic speech — each question read by the on-device Piper voice and transcribed by",
        "Moonshine — so they measure the recogniser and the pipeline, not real drivers: no accents, no cabin noise,",
        "no hesitations. Treat them as an optimistic bound.",
        "",
    ]
    for name in sets:
        lines += [f"## {name.capitalize()} set", "", "| Wording | " + " | ".join(labels[m] for m in modes) + " |"]
        lines.append("|---|" + "---|" * len(modes))
        for v in variants[name]:
            row = []
            for m in modes:
                res = results[name, v, m]
                s = (
                    summarise(results[name, "clean", m], res)
                    if v != "clean"
                    else {"k": sum(r["ok"] for r in res["rows"]), "n": res["n"]}
                )
                row.append(cell(s))
            lines.append(f"| {v} | " + " | ".join(row) + " |")
        if name in voice_info:
            info = voice_info[name]
            lines += [
                "",
                f"Recognition p50 {info['stt_ms_p50']} ms per question; {info['clipped_over_9s']} longer than 9 s.",
            ]
        worst = modes[-1]
        lost = [
            (v, r)
            for v in variants[name]
            if v != "clean"
            for r, c in zip(results[name, v, worst]["rows"], results[name, "clean", worst]["rows"], strict=True)
            if c["ok"] and not r["ok"]
        ]
        if lost:
            lines += ["", f"Lost with {labels[worst]}:", ""]
            lines += [f"- *{v}* “{r['q']}” → `{r['pred']}` (expected `{r['gold'][0]}`)" for v, r in lost]
        lines.append("")
    (a.out / f"{stem}.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    if hasattr(sys.stdout, "reconfigure"):  # a cp1252 console must not cost the report
        sys.stdout.reconfigure(errors="replace")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
