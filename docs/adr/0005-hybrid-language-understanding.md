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

## Consequences

- Accuracy and latency are reproducible: `python -m edgecar.bench.nlu_eval` and `python -m edgecar.bench.e2e`.
- Most questions never touch the model (milliseconds, zero model risk); the model handles the long tail.
- Held-out failure modes to address next: unparsed time phrases ("since the start of the month"), verb sense
  ("biggest hit" read as `max`, not `drop`), bare "fuel" not mapped to fuel metrics on the fast path.
- Next step for the model: LoRA fine-tuning on generated question → command pairs (the approach Mercedes-Benz R&D
  reports at 0.86–0.88 function-calling accuracy with a pruned 1.8B model, arXiv:2501.02342), which may allow the
  0.5B model and halve memory.
- Numbers above are from an x86 laptop limited to 2 threads; ARM (Cortex-A76 / Graviton) measurements are pending.
