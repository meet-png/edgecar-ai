"""Does the model know when it is wrong? Confidence and selective answering for noisy questions.

    python -m edgecar.bench.calibration --model models/edgecar-1.5b-q4_k_m.gguf --voice models/voice

Every question goes to the model (rules off, decline checks on, as in ``nlu_eval --mode slm``), in three wordings:
clean, mistyped and spoken (synthesised by the Piper voice, transcribed by Moonshine — see ``bench.robustness``).
For each answer we read the probability the model gave each token it wrote — before the grammar — and take the
*lowest* as its confidence: one doubtful token is enough to make a 2–6-token command wrong.

* AUROC — how well confidence separates right from wrong answers (0.5 = chance);
* risk–coverage — if the assistant asked "could you rephrase?" whenever confidence is below a threshold, how many
  questions would it still answer, and how many of those would be wrong.

The threshold is chosen on the dev questions (all three wordings) as the one that answers most while keeping errors
among answered questions at or below ``--max-risk``; only then is it applied to the blind questions (whose spoken
versions are the very transcripts scored in ``eval/results/robustness``). Results: ``eval/results/calibration.md``.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from edgecar.bench.nlu_eval import run
from edgecar.bench.robustness import typos, variant, voice_variant
from edgecar.nlu.slm import PROFILES, ParseOutput, SlmParser, SlmServer, find_server, prompt_style

PORT = 8099  # not the benchmarks' 8089, so a run here never queries another session's model server
DEV = {"dev": "eval/questions.jsonl", "refusal dev": "eval/refusal_dev.jsonl"}
BLIND = {"held-out": "eval/test.jsonl", "refusal test": "eval/refusal_test.jsonl"}
BLIND_VOICE = {"held-out": "held-out", "refusal test": "refusal"}  # eval/results/robustness/<x>-voice-fast.json


class ProbingParser(SlmParser):
    """The production parser, also asking llama-server for the probability of each generated token."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.confidence: dict[str, float] = {}

    def parse(self, question: str, timeout_s: float | None = None) -> ParseOutput:
        body = {
            "prompt": self.prompt(question),
            "grammar": self._grammar,
            "n_predict": 16,
            "temperature": 0.0,
            "cache_prompt": True,
            "stop": ["\n"],
            "n_probs": 1,
        }
        out = self._post("/completion", body, timeout_s)
        probs = out.get("completion_probabilities") or []
        logps = [p["logprob"] for p in probs if "logprob" in p and p.get("token", "").strip()]
        self.confidence[question.strip()] = math.exp(min(logps)) if logps else 0.0
        tm = out.get("timings", {})
        return ParseOutput(
            line=out.get("content", "").strip(),
            prompt_tokens=int(tm.get("prompt_n", 0)),
            cached_tokens=int(out.get("tokens_cached", 0)),
            generated_tokens=int(tm.get("predicted_n", 0)),
            prompt_ms=float(tm.get("prompt_ms", 0.0)),
            generate_ms=float(tm.get("predicted_ms", 0.0)),
            total_ms=0.0,
        )


def auroc(pairs: list[tuple[float, bool]]) -> float:
    """Probability that a random right answer has higher confidence than a random wrong one (ties count half)."""
    right = [c for c, ok in pairs if ok]
    wrong = [c for c, ok in pairs if not ok]
    if not right or not wrong:
        return float("nan")
    wins = sum((r > w) + 0.5 * (r == w) for r in right for w in wrong)
    return wins / (len(right) * len(wrong))


def at_threshold(pairs: list[tuple[float, bool]], t: float) -> dict:
    kept = [ok for c, ok in pairs if c >= t]
    return {
        "threshold": t,
        "coverage": len(kept) / len(pairs),
        "answered": len(kept),
        "wrong_answered": sum(not ok for ok in kept),
        "right_declined": sum(ok for c, ok in pairs if c < t),
    }


def choose_threshold(pairs: list[tuple[float, bool]], max_risk: float) -> float:
    """The lowest threshold (most questions answered) whose answered questions are at most ``max_risk`` wrong."""
    for t in sorted({0.0, *(c for c, _ in pairs)}):
        r = at_threshold(pairs, t)
        if r["answered"] and r["wrong_answered"] / r["answered"] <= max_risk:
            return t
    return 1.0


CURVE = (0.0, 0.3, 0.5, 0.7, 0.8, 0.9, 0.95, 0.99)


def curve_row(t: float, groups: dict) -> str:
    cells = [f"{t:.2f}"]
    for g in ("dev", "blind"):
        noisy = [x for k, v in groups[g].items() if not k.endswith("clean") for x in v]
        r = at_threshold(noisy, t)
        cells += [f"{r['coverage']:.1%}", f"{r['wrong_answered'] / max(1, r['answered']):.1%}"]
    return "| " + " | ".join(cells) + " |"


def read(path: str) -> list[dict]:
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]


def wordings(dev: bool, voice_root: Path | None, results: Path) -> dict[str, list[dict]]:
    """Every question of the dev or the blind sets in each wording, keyed "<set> · <wording>"."""
    out: dict[str, list[dict]] = {}
    for name, path in (DEV if dev else BLIND).items():
        items = read(path)
        out[f"{name} · clean"] = items
        out[f"{name} · typos"] = variant(items, "typos", typos)
        if dev and voice_root:
            out[f"{name} · voice"], _ = voice_variant(items, voice_root)
        elif not dev:  # the transcripts the robustness report scored, so both reports describe the same questions
            saved = results / "robustness" / f"{BLIND_VOICE[name]}-voice-fast.json"
            if saved.exists():
                rows = json.loads(saved.read_text(encoding="utf-8"))["rows"]
                out[f"{name} · voice"] = [{"id": r["id"], "q": r["q"], "gold": r["gold"]} for r in rows]
    return out


def scored(parser: ProbingParser, items: list[dict]) -> list[tuple[float, bool]]:
    parser.confidence.clear()
    rows = run(items, "slm", parser)["rows"]
    # a question the decline checks stop before the model never reaches it: certain by construction
    return [(parser.confidence.get(r["q"].strip(), 1.0), r["ok"]) for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", type=Path, required=True)
    ap.add_argument("--server", type=Path, default=None)
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--voice", type=Path, default=None, help="speech models: adds spoken dev questions")
    ap.add_argument("--max-risk", type=float, default=0.05, help="errors allowed among answered dev questions")
    ap.add_argument("--out", type=Path, default=Path("eval/results"))
    a = ap.parse_args()

    dev_q, blind_q = wordings(True, a.voice, a.out), wordings(False, None, a.out)
    with SlmServer(find_server(a.server), a.model, port=PORT, threads=a.threads, **PROFILES["edge"]) as srv:
        parser = ProbingParser(srv.url, style=prompt_style(a.model))
        parser.warm_up()
        groups = {
            "dev": {k: scored(parser, v) for k, v in dev_q.items()},
            "blind": {k: scored(parser, v) for k, v in blind_q.items()},
        }

    t = choose_threshold([x for v in groups["dev"].values() for x in v], a.max_risk)

    def summary(pairs: list[tuple[float, bool]]) -> dict:
        return {"n": len(pairs), "right": sum(ok for _, ok in pairs), "auroc": auroc(pairs), **at_threshold(pairs, t)}

    res = {
        "model": a.model.name,
        "confidence": "lowest token probability (raw, before the grammar)",
        "threshold": t,
        "max_risk": a.max_risk,
        "groups": {g: {k: summary(v) for k, v in d.items()} for g, d in groups.items()},
        "pooled": {g: summary([x for v in d.values() for x in v]) for g, d in groups.items()},
        "pairs": {g: {k: [[round(c, 4), ok] for c, ok in v] for k, v in d.items()} for g, d in groups.items()},
    }
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "calibration.json").write_text(json.dumps(res, indent=1), encoding="utf-8", newline="\n")

    def row(name: str, d: dict) -> str:
        right = d["answered"] - d["wrong_answered"]
        return (
            f"| {name} | {d['n']} | {d['right'] / d['n']:.1%} | {d['auroc']:.2f} | {d['coverage']:.1%} | "
            f"{right / max(1, d['answered']):.1%} | {d['right_declined']} |"
        )

    md = [
        "# Does the model know when it is wrong?",
        "",
        f"`python -m edgecar.bench.calibration --model {a.model.name}` (x86 laptop, 2 threads): the model alone on",
        "every question, in three wordings. Confidence = the lowest probability among the tokens the model wrote.",
        f"The threshold **{t:.3f}** was chosen on the dev questions (answer the most with ≤ {a.max_risk:.0%} of",
        "answers wrong) and applied unchanged to the blind ones. Below it the assistant would ask the driver to",
        "rephrase instead of answering.",
        "",
        "| Questions | n | Right, no threshold | AUROC | Answered | Right among answered | Right, asked to rephrase |",
        "|---|---|---|---|---|---|---|",
        *[row(k, v) for g in groups for k, v in res["groups"][g].items()],
        row("**dev, pooled** (threshold chosen here)", res["pooled"]["dev"]),
        row("**blind, pooled**", res["pooled"]["blind"]),
        "",
        "AUROC is undefined (nan) where a group has no errors. The spoken questions are synthetic speech — cleaner",
        "than a real driver in a moving car — so their numbers are an optimistic bound.",
        "",
        "## The trade-off on noisy questions (mistyped and spoken, pooled)",
        "",
        "| Threshold | dev: answered | dev: wrong among answered | blind: answered | blind: wrong among answered |",
        "|---|---|---|---|---|",
        *[curve_row(th, groups) for th in CURVE],
        "",
        "Reading: the model's confidence carries real signal (AUROC ≈ 0.75–0.8: a wrong answer usually has lower",
        "confidence than a right one), but on noisy input it is not sharp enough to gate on — asking for a",
        "rephrase on the least certain sixth of questions only trims the error rate by a third. Most noisy-input",
        "errors are confident misreadings of a misspelt or misheard word, so the fix belongs before the model:",
        "repairing the words (domain-vocabulary spelling correction, number words read as numbers), not abstaining.",
        "",
    ]
    (a.out / "calibration.md").write_text("\n".join(md), encoding="utf-8", newline="\n")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    print("\n".join(md))


if __name__ == "__main__":
    main()
