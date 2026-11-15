# ADR-0008 — Decline instead of guessing

- **Status:** Accepted
- **Date:** 2026-10-28

## Context

A frozen set of 40 requests the assistant must not answer with data — control ("clear the stored fault codes"),
instruction injection, out-of-scope questions, data the car does not record (tyre pressure, video) and ambiguous
follow-ups ("Is it bad?") — was declined only 29 times (72.5 %). Two causes: a rules match on a keyword ("fault codes")
answered a *write* request with a listing, and the 1.5B model, forced through a grammar, always writes some command
("What is the capital of France?" → `why energy_efficiency`).

## Decision

Three deterministic checks (`edgecar/nlu/guard.py`), with word lists taken from the signal catalogue and general
vehicle-control vocabulary:

1. **Action requests** — imperative control verbs and instruction-override phrases → "I can't control the car".
2. **Not recorded** — topics outside the signal catalogue → the answer names what is missing.
3. **Grounding** — a model command whose metric, event or verb the question never mentions is declined.

## Results

| Set | Before | After |
|---|---|---|
| Refusal test (40, frozen before the checks) | 72.5 % | 100 % |
| Refusal dev (20) | 70 % | 95 % |
| Held-out accuracy (70) | 95.7 % | 92.9 % on the first run → 95.7 % |
| Dev accuracy (122) | 93.4 % | 93.4 % |

The checks were written after seeing the baseline failures, so the 100 % is not a blind number. The held-out set's
first run exposed two vocabulary gaps (inflections such as "driving"; "worried"); both were fixed generically, which
makes that set no longer blind for these checks. Of the 184 legitimate dev and held-out questions, the deterministic
checks change none.

## Consequences

- Safety does not depend on these checks — the grammar has no write commands and the AI zone has no transmit path —
  but they decide whether the user gets an honest "I can't" or an unrelated answer.
- Next: a refusal and paraphrase set written by people outside the project.

## Correction (2026-10-29)

Every multi-word cue in these checks was dead. `re.escape` also escapes spaces, so escaping a phrase such as
"battery level" and then replacing its spaces with `\s+` left a literal backslash in the pattern, which never
matched. Single-word cues worked, which is why it went unnoticed; it surfaced when a fine-tuned model's correct `now`
for "What's my battery level?" was declined. Fixed (cues are escaped word by word) with a regression test, and
re-measured with the same model (Qwen2.5-1.5B few-shot, x86, 2 threads):

| Set | Published (with the bug) | Re-measured |
|---|---|---|
| Dev (122) | 93.4 % | 100 % |
| Held-out (70) | 95.7 % | 94.3 % |
| Refusal test (40) | 40/40 | 39/40 |
| Refusal dev (20) | 19/20 | 19/20 |

The bug declined eight correct dev answers, and by luck one wrong answer in the refusal test, which is now let
through. The R1 slide had already been submitted with the earlier numbers; the repository carries the corrected ones.

## Second correction (2026-11-15): the re-measurement above was not the stock model

The "Re-measured" column was served by the wrong model. A llama-server with edgecar-0.5b was still running on the
evaluation port, so the run labelled "Qwen2.5-1.5B few-shot" sent its questions to the 0.5B: its 24 dev answers on
the model path are identical to the 0.5B's, and its model path took ~275 ms against ~490 ms for the real stock
model. Measured again with nothing else running, same code (commit b17d08a) and model file (SHA-256 checked):

| Set | Published (with the bug) | First re-measurement (wrong model) | Re-measured, stock model |
|---|---|---|---|
| Dev (122) | 93.4 % | 100 % | 93.4 % |
| Held-out (70) | 95.7 % | 94.3 % | 95.7 % |
| Refusal test (40) | 40/40 | 39/40 | 40/40 |
| Refusal dev (20) | 19/20 | 19/20 | 19/20 |

So the cue bug did not change the stock model's x86 scores, and the R1 slide's x86 figures stand. `SlmServer` now
refuses to start when its port is already in use (`tests/test_slm_server.py`). Files:
[`eval/results/rerun/`](../../eval/results/rerun), [`significance.md`](../../eval/results/significance.md).
