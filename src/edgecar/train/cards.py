"""Model cards for the Hugging Face Hub, rendered from the evidence in ``eval/results`` so they cannot drift from it.

    python -m edgecar.train.cards --owner <hf-user>     # writes docs/models/<repo>.md for every repository

Each model has two repositories: ``<name>-GGUF`` (the llama.cpp files EdgeCar runs) and ``<name>`` (the merged
weights, for transformers and further work). Numbers come from the result files named next to each table; a
section whose results are missing (the ARM runs, say) is left out rather than filled with an estimate.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

from edgecar.nlu import command
from edgecar.nlu.slm import TUNED_INSTRUCTIONS
from edgecar.stats import mcnemar_exact

RESULTS = Path("eval/results")
OUT = Path("docs/models")
GITHUB = "https://github.com/meet-png/edgecar-ai"
BASELINE = "qwen2.5-1.5b"  # the few-shot model the fine-tuned ones replace
EXAMPLES = (
    "t-exp-2",
    "t-ext-3",
    "t-cmp-3",
    "t-evt-5",
    "t-bef-4",
    "t-sum-3",
    "t-trd-2",
    "t-ano-3",
    "t-sts-1",
    "t-oos-1",
    "t-ext-5",
)  # one or more per verb from the held-out set, and a known miss
REFUSAL_EXAMPLES = ("r-ctl-1", "r-ctl-4", "r-inj-1", "r-inj-3")
VERBS = (
    ("total M", "one number over a period: how far, how much, how many, the average"),
    ("trend M", "how something changed over time"),
    ("when M D", "the day something peaked or changed most (D = max, min, drop, rise)"),
    ("compare M B", "city vs highway, cold vs warm days, or this period vs the one before"),
    ("why M", "why consumption, efficiency or range changed"),
    ("show E", "list events: ADAS disengagements and takeover requests, fault codes, charging, hard braking"),
    ("before E", "what led up to an event"),
    ("unusual", "anything abnormal"),
    ("now", "the car's state right now"),
    ("unsupported", "anything that is not a question about this vehicle's recorded data"),
)


@dataclass(frozen=True)
class Model:
    name: str
    base: str
    params: str
    sibling: str
    role: str


MODELS = {
    "edgecar-0.5b": Model(
        "edgecar-0.5b",
        "Qwen/Qwen2.5-0.5B-Instruct",
        "0.5B",
        "edgecar-1.5b",
        "the 2 GB tier: fastest and smallest, with room left for on-device speech",
    ),
    "edgecar-1.5b": Model(
        "edgecar-1.5b",
        "Qwen/Qwen2.5-1.5B-Instruct",
        "1.5B",
        "edgecar-0.5b",
        "slightly higher measured accuracy (not a significant difference) for about 0.6 GB more memory",
    ),
}


def load(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def run(kind: str, mode: str, stem: str) -> dict | None:
    return load(RESULTS / "rerun" / f"{kind}-{mode}-{stem}.json")


def score(d: dict | None, *, frac: bool = False) -> str:
    if d is None:
        return "–"
    right = round(d["accuracy"] * d["n"])
    return f"{right}/{d['n']}" if frac else f"{d['accuracy'] * 100:.1f} %"


def mb(x: float) -> str:
    return f"{x:,.0f} MB"


def model_line(row: dict) -> str:
    return row["model_line"] or "(declined before the model)"


def as_model(gold: str) -> str:
    return command.render_model(command.parse(gold))


# ------------------------------------------------------------------------------------------------------- sections


def examples(m: Model) -> str:
    rows = {r["id"]: r for r in run("test", "slm", f"{m.name}-q4_k_m")["rows"]}
    lines = ["| Question (held-out test set) | The model writes | EdgeCar runs |", "|---|---|---|"]
    for i in EXAMPLES:
        r = rows[i]
        mark = "" if r["ok"] else f" ✗ — the answer key says `{as_model(r['gold'][0])}`"
        lines.append(f"| {r['q']} | `{model_line(r)}`{mark} | `{r['pred']}` |")
    return "\n".join(lines)


def refusal_examples(m: Model) -> str:
    d = run("refusal_test", "slm-noguard", f"{m.name}-q4_k_m")
    if d is None:
        return ""
    rows = {r["id"]: r for r in d["rows"]}
    control = [r["ok"] for r in d["rows"] if r["id"].startswith("r-ctl-")]
    lines = ["| Request (refusal test set) | The model alone writes |", "|---|---|"]
    lines += [f"| {rows[i]['q']} | `{model_line(rows[i])}` |" for i in REFUSAL_EXAMPLES]
    return (
        f"With EdgeCar's decline checks switched off, the model on its own answers `unsupported` to "
        f"**{score(d, frac=True)}** requests to control the car, prompt injections, out-of-scope and ambiguous "
        f"questions — {sum(control)} of the {len(control)} requests to control the car among them "
        f"([result]({GITHUB}/blob/main/eval/results/rerun/refusal_test-slm-noguard-{m.name}-q4_k_m.json)). "
        "When it misses, the worst it can write is a read-only query: the grammar has no command that acts.\n\n"
        + "\n".join(lines)
    )


def results_table(m: Model) -> str:
    cols = [
        (f"**{m.name}** Q4_K_M", f"{m.name}-q4_k_m"),
        ("Q8_0", f"{m.name}-q8_0"),
        (f"{m.sibling} Q4_K_M", f"{m.sibling}-q4_k_m"),
        ("Qwen2.5-1.5B few-shot", f"{BASELINE}-fewshot"),
    ]
    e2e = {s: load(RESULTS / "rerun" / f"e2e-{s.replace('-fewshot', '-instruct-q4_k_m')}-t2.json") for _, s in cols}

    def row(label: str, cell) -> str:
        return f"| {label} | " + " | ".join(cell(s) for _, s in cols) + " |"

    def lat(s: str) -> str:
        d = e2e[s]
        return f"{d['latency_ms_slm']['p50']:.0f} / {d['latency_ms_slm']['p95']:.0f} ms" if d else "–"

    def mem(s: str) -> str:
        d = e2e[s]
        return f"{d['peak_rss_mb']['llama_server']:,} MB ({d['llama_server_private_mb']})" if d else "–"

    def size(s: str) -> str:
        d = e2e[s]
        return f"{mb(d['model_file_mb'])} · {d['boot_s']} s" if d else "–"

    return "\n".join(
        [
            "| x86 laptop (i7-13620H), 2 threads | " + " | ".join(c for c, _ in cols) + " |",
            "|---|" + "---|" * len(cols),
            row("Held-out (70): rules + model", lambda s: score(run("test", "hybrid", s))),
            row("Held-out (70): model alone", lambda s: score(run("test", "slm", s))),
            row(
                "Refusal test (40) · refusal dev (20)",
                lambda s: (
                    f"{score(run('refusal_test', 'hybrid', s), frac=True)} · "
                    f"{score(run('refusal_dev', 'hybrid', s), frac=True)}".replace("– · –", "–")
                ),
            ),
            row(
                "Dev (122): rules + model · model alone",
                lambda s: f"{score(run('questions', 'hybrid', s))} · {score(run('questions', 'slm', s))}".replace(
                    "– · –", "–"
                ),
            ),
            row("Model path p50 / p95", lat),
            row("llama-server peak RSS (private)", mem),
            row("File · start-up to first model answer", size),
        ]
    )


def versus_stock(m: Model) -> str:
    """The paired comparison with the few-shot model on the blind sets, significance included."""

    def oks(stem: str) -> dict[str, bool]:
        out: dict[str, bool] = {}
        for kind in ("test", "refusal_test"):
            out |= {r["id"]: r["ok"] for r in run(kind, "hybrid", stem)["rows"]}
        return out

    a, b = oks(f"{m.name}-q4_k_m"), oks(f"{BASELINE}-fewshot")
    wins, losses = sum(a[i] and not b[i] for i in a), sum(b[i] and not a[i] for i in a)
    p = mcnemar_exact(wins, losses)
    verdict = "a significant difference" if p < 0.05 else "not a statistically significant difference"
    return (
        f"On the {len(a)} blind questions (held-out and refusal test, rules + model), {m.name} gets {sum(a.values())} "
        f"right and the few-shot Qwen2.5-1.5B it replaces {sum(b.values())}: {wins} better, {losses} worse — "
        f"{verdict} (exact McNemar p = {p:.2f}). What fine-tuning clearly bought is speed, memory and start-up time."
    )


def arm_section(m: Model) -> str:
    stem = f"{m.name}-q4_k_m"
    runs = [
        ("GitHub Arm64 runner: Neoverse-N2, 2 cores, the whole AI zone in a 1,792 MiB cgroup", "arm"),
        ("AWS t4g.small: Graviton2 (Neoverse-N1), 2 cores, 2 GiB of RAM in total, OS included", "graviton"),
    ]
    have = [(label, load(RESULTS / where / stem / "summary.json"), where) for label, where in runs]
    have = [h for h in have if h[1]]
    if not have:
        return ""
    out = ["## On ARM", ""]
    for label, s, where in have:
        slm, every = s["latency_ms"]["slm"], s["latency_ms"]["all"]
        if where == "graviton":  # the whole 2 GiB machine is the budget
            memory = f"{s['system_used_peak_mb']:,} MiB of {s['system_mem_total_mb']:,} MiB in use at peak, OS included"
        else:  # the runner has far more RAM; the cgroup holding the AI zone is the budget
            memory = f"AI zone peak {s['memory_peak_mb']:,} MiB (limit {s['memory_limit_mb']:,})"
        out += [
            f"**{label}** ([results]({GITHUB}/tree/main/eval/results/{where}/{stem})): model path p50 "
            f"{slm['p50'] / 1000:.2f} s · p95 {slm['p95'] / 1000:.2f} s; {every['under_2s_pct']:.0f} % of "
            f"{s['questions']} questions under 2 s; {memory}; {s['oom_kills']} OOM kills; held-out "
            f"{s['heldout_accuracy'] * 100:.1f} %; model ready {s['model_first_boot_s']} s after start.",
            "",
        ]
    return "\n".join(out)


def training(m: Model) -> str:
    log = load(RESULTS / "train" / f"{m.name}.json")
    c, d = log["config"], log["data_stats"]
    dropped = sum(d["dropped_overlapping_eval"].values())
    epoch = log["epochs"][log["best_epoch"] - 1]
    return f"""- **Data** — {d["kept"]:,} synthetic questions, each labelled with its command: 417 written by hand
  ({d["by_source"]["seed"]} rows with light noise variants) and {d["by_source"]["template"]:,} from templates that
  cross every verb's sentence shapes with metric, event and time phrasings, covering all {d["distinct_commands"]}
  commands. {dropped} generated questions were dropped because they matched or shared half their word trigrams with
  a question in one of the four evaluation sets; the held-out sets were never read while writing the data.
  {d["train"]:,} train / {d["val"]} validation ([generator]({GITHUB}/blob/main/src/edgecar/train/data.py),
  [labelling rules]({GITHUB}/blob/main/eval/LABELLING.md)).
- **Method** — LoRA rank {c["rank"]}, alpha {2 * c["rank"]}, dropout 0.05 on all attention and MLP projections
  ({log["trainable_params"] / 1e6:.1f} M trainable parameters), bf16. AdamW, learning rate {c["lr"]:g}, batch
  {c["batch"]}, cosine schedule with 5 % warm-up, gradient clipping at 1.0, {c["epochs"]} epochs. The loss covers
  the answer tokens only: projecting just those positions onto the 152k-token vocabulary is what lets the
  {m.params} model train on a 6 GB laptop GPU{" with gradient checkpointing" if c.get("grad_ckpt") else ""}. The
  epoch with the best dev accuracy is kept: epoch {log["best_epoch"]} ({epoch["dev_accuracy"] * 100:.1f} % of the
  142 dev questions, exact match, greedy, no grammar).
- **Compute** — one {log["device"].replace("NVIDIA GeForce ", "")}, {log["train_minutes"]:.1f} minutes for all
  {c["epochs"]} epochs; torch {log["torch"].split("+")[0]}, transformers 5.21, PEFT 0.21
  ([script]({GITHUB}/blob/main/src/edgecar/train/finetune.py),
  [log]({GITHUB}/blob/main/eval/results/train/{m.name}.json)).
- **Export** — merged into the base weights, converted with llama.cpp's own converter (release b11577) and quantised
  to Q4_K_M and Q8_0 ([script]({GITHUB}/blob/main/src/edgecar/train/export.py))."""


def limitations() -> str:
    return f"""- **One author.** The training data and every test set were written by the same person (with an AI pair
  programmer), which flatters all models in the same direction. A test set written by people outside the project
  is being collected ([protocol]({GITHUB}/blob/main/eval/outside/README.md)); until it is scored, read the
  accuracy as optimistic.
- **English, one vehicle's recorded data, one command language.** The model knows EdgeCar's commands and nothing
  else; it is not a general assistant and not a chat model.
- **Use the grammar.** With `commands.gbnf` every output is a valid command — not necessarily the right one.
  Without it, nothing forces the format.
- **Ambiguity is guessed, not asked.** "My longest driving day" could mean distance or trips; the model picks one.
- **Not a safety function.** It cannot act on a vehicle and must not be used for safety-relevant decisions. In
  EdgeCar it runs in its own process with no network and no path to the vehicle bus, behind deterministic decline
  checks, and every number in an answer is computed by code from the logs."""


# ---------------------------------------------------------------------------------------------------------- cards


def front_matter(base: str, relation: str, extra: list[str]) -> str:
    tags = ["automotive", "vehicle-data", "on-device", "edge-ai", "semantic-parsing", "text-to-command", "qwen2"]
    lines = [
        "---",
        "license: apache-2.0",
        "language:",
        "- en",
        f"base_model: {base}",
        f"base_model_relation: {relation}",
        "pipeline_tag: text-generation",
        *extra,
        "tags:",
    ]
    return "\n".join([*lines, *(f"- {t}" for t in tags), "---"])


def intro(m: Model, owner: str, gguf: bool) -> str:
    e2e = load(RESULTS / "rerun" / f"e2e-{m.name}-q4_k_m-t2.json")
    p50 = e2e["latency_ms_slm"]["p50"] / 1000
    other = (
        f"[{owner}/{m.name}](https://huggingface.co/{owner}/{m.name}) (merged weights)"
        if gguf
        else (f"[{owner}/{m.name}-GGUF](https://huggingface.co/{owner}/{m.name}-GGUF) (what EdgeCar runs)")
    )
    return f"""**Ask about your car's recorded data in plain English; this model turns the question into a short
command that the car's own computer answers from its logs — offline, in about {p50:.1f} s on two laptop cores.**

Fine-tuned from [{m.base.split("/")[1]}](https://huggingface.co/{m.base}) for [EdgeCar AI]({GITHUB}), an on-device
vehicle-data assistant built for the Tata Elxsi TELIPORT Season 4 challenge (Case Study 5, *Natural Language Vehicle
Information using On-Device SLM/LLM*). This repository: {"GGUF for llama.cpp" if gguf else "merged weights"}. Also:
{other} · [{owner}/{m.sibling}-GGUF](https://huggingface.co/{owner}/{m.sibling}-GGUF) — {MODELS[m.sibling].role}."""


def what_it_does(m: Model) -> str:
    verbs = "\n".join(f"| `{v}` | {d} |" for v, d in VERBS)
    return f"""## What it does

It is a semantic parser, not a chat model. Given one question it writes one command of one to three words; code
reads the time window from the question ("yesterday", "over the past 10 days"), runs the command read-only against
the vehicle's own logs and computes every number in the answer. The model never sees the data.

{examples(m)}

The commands: 10 verbs over 15 metrics (`energy_consumption`, `range`, `battery_temp`, `soc`, `hvac_energy`, …)
and 10 event kinds (`adas_disengagement`, `adas_takeover_request`, `dtc`, `charging`, …) — 115 in all.

| Command | Meaning |
|---|---|
{verbs}

{refusal_examples(m)}"""


def gguf_card(m: Model, owner: str) -> str:
    files = load(Path("models") / f"{m.name}.json")
    q4, q8 = files["files"]["Q4_K_M"], files["files"]["Q8_0"]
    held4, held8 = run("test", "hybrid", f"{m.name}-q4_k_m"), run("test", "hybrid", f"{m.name}-q8_0")
    body = f"""{front_matter(f"{owner}/{m.name}", "quantized", [])}

# EdgeCar {m.params} · GGUF

{intro(m, owner, gguf=True)}

{what_it_does(m)}

## Files

| File | Size | Held-out accuracy (rules + model) | |
|---|---|---|---|
| `{q4["file"]}` | {mb(q4["bytes"] / 1e6)} | {score(held4)} | what EdgeCar runs |
| `{q8["file"]}` | {mb(q8["bytes"] / 1e6)} | {score(held8)} | 8-bit: a larger file; latency not measured |
| `commands.gbnf` | | | the grammar: every output is a valid command |

## Use

**In EdgeCar AI** — the full assistant, with answers, charts, evidence and on-device speech:

```bash
git clone {GITHUB} && cd edgecar-ai
pip install -e ".[prep]"
python -m edgecar.prep.fetch all tuned    # public datasets and these models, SHA-256 checked
python -m edgecar.prep.build              # the vehicle-data store
edgecar serve --model models/{q4["file"]}   # http://127.0.0.1:8080
```

**With llama.cpp alone** — start `llama-server -m {q4["file"]} -c 2048`, then:

```python
import json
import urllib.request

body = {{
    "messages": [
        {{"role": "system", "content": "{TUNED_INSTRUCTIONS}"}},
        {{"role": "user", "content": "why has my range dropped over the last two weeks?"}},
    ],
    "grammar": open("commands.gbnf").read(),  # every answer is a valid command
    "temperature": 0,
    "max_tokens": 16,
}}
request = urllib.request.Request("http://127.0.0.1:8080/v1/chat/completions", json.dumps(body).encode(),
                                 {{"Content-Type": "application/json"}})
print(json.load(urllib.request.urlopen(request))["choices"][0]["message"]["content"])  # why range
```

The system message must be exactly that sentence: it is the prompt the model was trained with.

## Results

{results_table(m)}

"Rules + model" is EdgeCar's pipeline: deterministic rules answer about three questions in four and the model takes
the rest; "model alone" sends every question to the model (the decline checks stay on). Accuracy is an exact match
of the full query against the answer key, time window included, so a few misses are the code's, not the model's.
Latency is for the questions that reach the model, through llama-server with the grammar, on the 192 dev and
held-out questions ([results]({GITHUB}/tree/main/eval/results/rerun)). The dev numbers are optimistic: the epoch
was chosen on them, and the rules were written against them.

{versus_stock(m)}

{arm_section(m)}
## How it was made

{training(m)}

## Limitations

{limitations()}

## License

Apache-2.0, like the base model ({m.base.split("/")[1]}, © Alibaba Cloud). Built by Meet Kabra (JECRC University)
for team Sidemen's TELIPORT entry — [code, data pipeline and every result]({GITHUB}).
"""
    return re.sub(r"\n{3,}", "\n\n", body)


def merged_card(m: Model, owner: str) -> str:
    body = f"""{front_matter(m.base, "finetune", ["library_name: transformers"])}

# EdgeCar {m.params}

{intro(m, owner, gguf=False)}

{what_it_does(m)}

## Use

```python
from transformers import AutoModelForCausalLM, AutoTokenizer

repo = "{owner}/{m.name}"
tokenizer = AutoTokenizer.from_pretrained(repo)
model = AutoModelForCausalLM.from_pretrained(repo)
messages = [
    {{"role": "system", "content": "{TUNED_INSTRUCTIONS}"}},
    {{"role": "user", "content": "why has my range dropped over the last two weeks?"}},
]
inputs = tokenizer.apply_chat_template(messages, add_generation_prompt=True, return_dict=True, return_tensors="pt")
output = model.generate(**inputs, max_new_tokens=16, do_sample=False)
print(tokenizer.decode(output[0, inputs["input_ids"].shape[1]:], skip_special_tokens=True))  # why range
```

The system message must be exactly that sentence: it is the prompt the model was trained with. On a device, use
[{owner}/{m.name}-GGUF](https://huggingface.co/{owner}/{m.name}-GGUF) with its grammar, which guarantees every
output is a valid command; results, on x86 and ARM, are on that page.

## How it was made

{training(m)}

## Limitations

{limitations()}

## License

Apache-2.0, like the base model ({m.base.split("/")[1]}, © Alibaba Cloud). Built by Meet Kabra (JECRC University)
for team Sidemen's TELIPORT entry — [code, data pipeline and every result]({GITHUB}).
"""
    return re.sub(r"\n{3,}", "\n\n", body)


def cards(owner: str) -> dict[str, str]:
    out = {}
    for m in MODELS.values():
        out[f"{m.name}-GGUF"] = gguf_card(m, owner)
        out[m.name] = merged_card(m, owner)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--owner", required=True, help="Hugging Face user or organisation")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for repo, text in cards(a.owner).items():
        (OUT / f"{repo}.md").write_text(text, encoding="utf-8", newline="\n")
        print(f"{OUT / repo}.md")


if __name__ == "__main__":
    main()
