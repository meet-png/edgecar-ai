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
