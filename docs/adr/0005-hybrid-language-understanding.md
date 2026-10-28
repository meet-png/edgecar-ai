# ADR-0005 — Hybrid language understanding: rules, a verb-aligned SLM, deterministic slots

- **Status:** Accepted
- **Date:** 2026-10-27
- **Supersedes:** the JSON-output detail of ADR-0001 (the semantic-parser principle stands)

## Context

ADR-0001 made the model a semantic parser. The first implementation asked Qwen2.5 to write a full command
(`intent metric=… window=… …`) under a GBNF grammar, with few-shot examples in the system prompt. Measured on a
122-question development set with 2 CPU threads:

| Model (Q4_K_M) | Model-only accuracy | p50 latency |
|---|---|---|
| Qwen2.5-0.5B-Instruct | 23.8 % | 0.55 s |
| Qwen2.5-1.5B-Instruct | 48.4 % | 0.80 s |

Error analysis showed two systematic failure modes: **surface copying** (a question containing "events" was mapped to
`events_before`) and **arithmetic** ("2 months" → `last_24d`, "3 months" → `last_3d`).

## Decision

Split the work by what each component does reliably.

1. **Fast path (rules).** Deterministic patterns recognise the common question shapes and fill every slot. A
   question is answered here only when the intent *and* all required slots are found; otherwise it falls through.
2. **SLM classifies meaning only.** The model writes 2–6 tokens: a verb aligned with how people ask (`why`,
   `when`, `show`, `before`, `compare`, `trend`, `total`, `unusual`, `now`, `unsupported`) plus the metric, event,
   direction or comparison axis. A per-verb GBNF grammar makes every output executable. Few-shot examples are given
   as real chat turns.
3. **Deterministic slots.** Time windows and look-back minutes are extracted by code from the question
   (or default per intent) — never by the model.
4. **`unsupported`** lets the model decline questions that are not about the vehicle's data.
5. **Edge server profile.** `llama-server` runs with `--no-repack` (weights stay a shared, read-only memory map
   instead of a ~630 MB private repacked copy), a 2,048-token context, smaller batches and no RAM prompt cache.
   The fixed prompt prefix (1,187 tokens) is processed once at boot and reused for every question.

## Results (development machine, 2 threads, Qwen2.5-1.5B-Instruct Q4_K_M)

| | Dev set (122) | **Held-out test (70)** |
|---|---|---|
| SLM-only accuracy (verb format) | 82.0 % | — |
| Hybrid accuracy | 93.4 % | **95.7 %** |
| Fast-path coverage / precision | 78 % / 100 %\* | 73 % / 98.0 % |
| SLM accuracy on fall-through questions | — | 89.5 % |

\* rules were tuned on the dev set; the held-out set was written afterwards and not tuned against.

End-to-end over all 192 questions, store + model loaded: latency p50 **6.5 ms**, p95 **573 ms**, max 702 ms,
**100 % under 2 s**; peak resident memory **1.28 GB** for both processes (llama-server 1,157 MB of which 227 MB
private, assistant 120 MB). Qwen2.5-0.5B reached only 80.0 % hybrid / 31.6 % on fall-through questions, so the 1.5B
model is chosen; it fits the budget only with the edge profile.

## Model comparison (2026-10-28)

Six candidates, same prompt (each in its own chat template via llama-server `/apply-template`), same grammar, same
`edge` server profile, 2 threads:

| Model (GGUF) | Model-only, 192 q | Hybrid, held-out 70 q | p50 / p95 | RSS (private) |
|---|---|---|---|---|
| Qwen2.5-0.5B-Instruct Q4_K_M | 34.4 % | 81.4 % | 223 / 375 ms | 519 MB (167) |
| Qwen3-0.6B Q8_0 | 5.2 % | 75.7 % | 340 / 558 ms | 968 MB (378) |
| Llama-3.2-1B-Instruct Q4_K_M | 35.9 % | 82.9 % | 408 / 602 ms | 992 MB (214) |
| Gemma-3-1B-it Q4_K_M | 39.6 % | 85.7 % | 493 / 679 ms | 980 MB (229) |
| **Qwen2.5-1.5B-Instruct Q4_K_M** | **85.4 %** | **95.7 %** | 510 / 713 ms | 1,158 MB (225) |
| SmolLM2-1.7B-Instruct Q4_K_M | 50.5 % | 84.3 % | 960 / 1,351 ms | 1,534 MB (513) |

Qwen2.5-1.5B leads model-only accuracy by 35 points at latency close to the 1B models and the same private memory
(its weights are a shared memory map). Final end-to-end run with this model: p50 6.1 ms, p95 549 ms, max 684 ms over
192 questions; peak RSS 1.28 GB.

## Consequences

- Accuracy and latency are reproducible: `python -m edgecar.bench.nlu_eval` and `python -m edgecar.bench.e2e`.
- Most questions never touch the model (milliseconds, zero model risk); the model handles the long tail.
- Held-out failure modes to address next: unparsed time phrases ("since the start of the month"), verb sense
  ("biggest hit" read as `max`, not `drop`), bare "fuel" not mapped to fuel metrics on the fast path.
- Next step for the model: LoRA fine-tuning on generated question → command pairs (the approach Mercedes-Benz R&D
  reports at 0.86–0.88 function-calling accuracy with a pruned 1.8B model, arXiv:2501.02342), which may allow the
  0.5B model and halve memory.
- Numbers above are from an x86 laptop limited to 2 threads. **ARM, measured 2026-10-28** (Neoverse-N2, 2 cores, the
  whole AI zone in a 1.75 GB cgroup; `.github/workflows/arm-bench.yml`): model path p50 0.92 s / p95 1.35 s, rules
  path p50 5.7 ms, 100 % of 192 questions under 2 s; held-out accuracy 94.3 % (one borderline answer flips between
  CPU architectures). Prompt processing is the bottleneck: 34 tokens/s without weight repacking, 55–61 with it.
- **AWS t4g.small, measured 2026-10-28** (2 Graviton2 cores = Neoverse-N1, 2 GiB in total, OS included, no swap;
  `eval/results/graviton`): model path p50 1.46 s / p95 1.89 s / max 1.90 s, rules p50 10 ms, 100 % of 192 questions
  under 2 s; whole machine peak 670 MiB in use, 0 OOM; held-out 94.3 %, refusals 40/40.
- Since 2026-10-28 the pipeline also declines requests to act, questions about data the car lacks, and model commands
  the question does not support (ADR-0008); held-out accuracy is unchanged at 95.7 % on x86.
