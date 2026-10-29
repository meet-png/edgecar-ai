# ADR-0009 — Our own command model: LoRA fine-tuning on synthetic, overlap-checked data

- **Status:** Accepted
- **Date:** 2026-10-29

## Context

The stock Qwen2.5-1.5B-Instruct learns the command language from a ~1,190-token few-shot prompt. That prompt costs a
12 s warm-up at every start (81 s on Graviton2, hence the saved prompt cache), and the model alone gets only 82.8 %
of the dev questions right — the rules carry it. On Graviton2 its model path peaked at 1.90 s against the 2 s budget.
The Mercedes-Benz R&D paper on in-vehicle function calling (arXiv:2501.02342) pointed to fine-tuning as the next step.

## Decision

1. **Training data written for training only** (`edgecar/train/`): 417 hand-written questions grouped by the
   command they mean (`seeds.txt`), plus templates — each verb's sentence shapes × metric and event phrasings × time
   phrases × light noise (casing, punctuation, fillers, the odd typo) — covering all 115 commands. Labels follow
   [`eval/LABELLING.md`](../../eval/LABELLING.md).
2. **Nothing from the evaluation sets.** Every generated question is checked against all four eval sets; one that is
   identical once normalised, or shares half of either question's word trigrams, is dropped (576 of 4,371 — mostly
   generic template phrasings). The held-out and refusal sets were never read while writing the data.
3. **LoRA** (rank 16 on all attention and MLP projections, 3 epochs) on Qwen2.5-0.5B- and 1.5B-Instruct, trained on
   the answer tokens only — projecting only those positions onto the 152k-token vocabulary is what fits the 1.5B,
   with gradient checkpointing, on a 6 GB laptop GPU (RTX 3050: 5.2 min for the 0.5B at batch 32 and learning rate
   2e-4; 13.9 min for the 1.5B at batch 16 and 1e-4). The epoch with the best dev accuracy is kept: epoch 1 for the
   0.5B (epoch 3 only tied it), epoch 3 for the 1.5B. Logs: [`eval/results/train/`](../../eval/results/train).
4. **The same runtime.** The merged model is converted with llama.cpp's own converter (release b11577) and quantised
   to Q4_K_M; it runs in the same llama-server, under the same grammar and decline checks. Its prompt is one
   instruction (36 tokens in total), selected by the `edgecar-` file-name prefix.

## Results (x86 laptop, 2 threads, after the decline-check fix of ADR-0008)

| | Qwen2.5-1.5B few-shot | **edgecar-0.5b** | **edgecar-1.5b** |
|---|---|---|---|
| Held-out (70), rules + model | 94.3 % | 92.9 % | **95.7 %** |
| Held-out, model alone | 90.0 % | 94.3 % | **97.1 %** |
| Dev (122), rules + model / model alone | 100 % / 82.8 % | 100 % / 99.2 % | 100 % / 99.2 % |
| Refusal test (40) · dev (20) | 39 · 19 | 39 · 19 | **40 · 20** |
| Model path p50 / p95 (192 questions) | 480 / 670 ms | **221 / 299 ms** | 384 / 557 ms |
| llama-server memory (private) | 1,157 MB (227) | **499 MB (163)** | 1,123 MB (222) |
| File (Q4_K_M) · start-up | 1,117 MB · 13.9 s | **398 MB · 1.0 s** | 986 MB · 1.6 s |
| Q8_0: held-out rules + model · model alone · refusal test | – | 94.3 % · 95.7 % · 39 | 95.7 % · 97.1 % · 40 |

Latency and memory are from a re-run with corrected percentiles (2026-10-29: the exclusive definition used before
could put a small sample's p95 above its maximum); the first run measured 470 / 669, 205 / 295 and 393 / 562 ms.

## What the numbers do and don't say

- The dev numbers are optimistic: the epoch was chosen on them. The held-out set was blind to the training data,
  but it, the dev set and the training data share an author, which flatters every model in the same direction.
  The test that settles it is the outside set ([`eval/outside/`](../../eval/outside/README.md)).
- Fine-tuned, the model alone beats rules + model on the held-out set: the rules, tuned on dev, are now the weaker
  part there. On all 192 questions the two tie (0.5B: 97.4 % both ways; 1.5B: 98.4 %), so the hybrid stays — it
  answers three questions in four in ~5 ms.
- ARM latency is not measured yet; scaling the Graviton2 run by the x86 ratios suggests a model-path p95 near 0.8 s
  for the 0.5B. That is an estimate until the model runs there.

## Consequences

- Tiers: **edgecar-0.5b** for the 2 GB tier, where it also leaves room for on-device speech (ADR-0010);
  **edgecar-1.5b** where accuracy matters most and there is ~1 GB more. The prompt-prefix cache (ADR-0005) is no
  longer needed for start-up with either.
- The weights derive from Apache-2.0 models and are published under Apache-2.0 (2026-10-29) on Hugging Face —
  [edgecar-0.5b-GGUF](https://huggingface.co/meettttttttttt/edgecar-0.5b-GGUF),
  [edgecar-1.5b-GGUF](https://huggingface.co/meettttttttttt/edgecar-1.5b-GGUF) and the merged weights — each
  repository checked file by file against the local SHA-256 before it was made public
  (`python -m edgecar.train.publish`, cards from `python -m edgecar.train.cards`). `prep.fetch tuned` downloads them;
  `python -m edgecar.train.finetune` rebuilds them. The repository's default model stays the stock one until the
  fine-tuned ones are measured on ARM.
- On the blind sets the fine-tuned models are at least as accurate as the stock one, not significantly better
  (1.5B: 107 vs 105 of 110, exact McNemar p = 0.62; [`eval/results/significance.md`](../../eval/results/significance.md)).
