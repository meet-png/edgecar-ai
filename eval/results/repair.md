# The input repair, before and after

`edgecar.nlu.normalise` repairs a typed or transcribed question before it is understood: spoken forms of domain
words, number words before a unit, and spelling against the code's own lexicon plus our training questions (no
outside word list, no evaluation question; never into a control verb; 0.18 ms per question on x86).

**Protocol.** Designed and tuned on the dev questions only (`python -m edgecar.bench.robustness --dev`, dev Moonshine
transcripts). The design was frozen (commit d8e7c3d) and the blind sets were then run **once**. Every comparison
below is paired — the same questions with and without the repair — with a two-sided exact McNemar test. x86 laptop,
2 threads, rules + edgecar-1.5b Q4_K_M unless marked.

## Dev questions (where it was built — optimistic)

Rules only, 122 dev questions: typos 46.7 → 82.0 %, spoken numbers 76.2 → 82.8 %, voice 61.5 → 74.6 %, clean
82.0 → 82.8 %; refusal dev 20/20 throughout.

## Blind questions (run once)

| Held-out 70, wording | Without repair | With repair | Gained · lost | p |
|---|---|---|---|---|
| clean | 67 (95.7 %) | 67 (95.7 %) | 0 · 0 | 1.00 |
| lower case, no punctuation | 67 (95.7 %) | 67 (95.7 %) | 0 · 0 | 1.00 |
| typos | 53 (75.7 %) | **67 (95.7 %)** | 14 · 0 | **0.0001** |
| spoken filler | 67 (95.7 %) | 66 (94.3 %) | 0 · 1 | 1.00 |
| numbers and units as words | 59 (84.3 %) | **67 (95.7 %)** | 8 · 0 | **0.008** |
| real voice loop, same transcripts ¹ | 55 (78.6 %) | 58 (82.9 %) | 4 · 1 | 0.38 |

Refusal test: 40/40 declined in every wording, with and without the repair. Rules only (no model), typos go from
32 to 52 of 70 and spoken numbers from 45 to 53.

¹ The speech loop is not deterministic: two runs of Piper → Moonshine on the same questions gave identical
transcripts for only 41 of 70. Comparing two runs would mix that variance into the result, so the voice row scores
the *same* saved transcripts (from the pre-repair run) with the repair off and on
([`voice-repair-paired.json`](voice-repair-paired.json)). The new run's own voice row (59/70) is in
[`robustness.md`](robustness.md).

## What it means

- Typed noise is essentially solved for these questions: a mistyped question is now answered as often as a clean one.
- Numbers said as words — which the on-device recogniser writes for 11 of the 17 held-out questions containing
  numbers — no longer lose their time window.
- Speech improves less, and not significantly: the remaining errors are mishearings no spelling rule can undo
  ("any of the normal trips" for "abnormal trips"). That needs a better recogniser, or one biased towards the
  domain's words (contextual biasing / hot-words), not more repair.
- The one question lost (with filler) is the outside-temperature question that also flips between x86 and ARM:
  the model sits on the boundary between "compare by temperature" and "explain the change" there.
- The clean held-out score and every refusal are unchanged, so the repair costs nothing where nothing was wrong.
