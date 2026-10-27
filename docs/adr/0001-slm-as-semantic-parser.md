# ADR-0001 — The language model is a semantic parser, not an oracle

- **Status:** Accepted
- **Date:** 2026-10-27

## Context

The assistant must answer natural-language questions about weeks of vehicle history in **under 2 seconds** on
hardware with **~2 GB RAM and ~2 ARM Cortex-A5x cores**, without the cloud.

Measured and published numbers bound what a model can do in that budget:

| Model (4-bit, llama.cpp) | Generation on Raspberry Pi 5 | Source |
|---|---|---|
| ~0.5B (Qwen2/2.5) | ~17–20 tok/s | community benchmarks |
| ~1.5B (Qwen2.5) | ~10–15 tok/s | community benchmarks |
| Phi-3 mini pruned to 1.8B | ~11 tok/s on a 7-core ARM head unit, < 2 GB RAM | Mercedes-Benz R&D, arXiv:2501.02342 |

At 10–20 tok/s, the 2 s budget leaves room for roughly **20–40 output tokens** after prompt processing. That
is enough to emit a compact structured query, but not to read raw telemetry or write a long analysis.
Small models also invent numbers when asked to compute from context, and the brief judges *accuracy*.

## Decision

Split every question into four stages, and give the model only the job it is good at:

1. **Fast path.** A lightweight intent matcher (embedding similarity / lexical patterns over a catalogue of
   question templates) answers frequent questions without invoking the generator.
2. **Parse.** Otherwise, the SLM converts the question into a typed query object, decoded under a
   **JSON grammar** so the output is always schema-valid — e.g.
   `{"intent": "trend_explain", "signal": "range_km", "window": "14d"}`.
3. **Compute.** A deterministic analytics engine executes the query over the on-device store: aggregation,
   comparison, trend and change-point detection, anomaly detection, and "what happened before event X"
   correlation. Every number in an answer comes from here.
4. **Phrase.** Facts are rendered into a short answer — by template, or by the SLM constrained to the computed
   facts — and streamed, so the first words appear well inside 2 s.

The system prompt (tool schema + instructions) is fixed, so its KV cache is computed once at boot and reused,
removing most prompt-processing time per query (Arm reports ~2× end-to-end speed-up from this technique).

## Consequences

- **Accuracy is testable**: the parser can be scored on exact-match of the query object, and the engine on
  ground truth, separately.
- **Latency is predictable**: model work is bounded by a short, fixed-size output.
- **Explainability**: each answer can cite the query it ran and the evidence rows it used.
- Questions outside the query vocabulary need a graceful fallback (clarifying question or "not supported"),
  which must be designed explicitly.
- The query vocabulary becomes a product surface that must be versioned.

## Alternatives considered

- **RAG over raw logs with free-form generation** — prompt too long for the latency budget; numbers unreliable.
- **Text-to-SQL** — expressive, but a 0.5–1.5B model writes invalid or unsafe SQL too often; a typed
  intermediate query is easier to validate and to restrict to read-only operations.
- **Larger model (3–8B)** — does not fit 2 GB RAM alongside the data store at usable speed.
