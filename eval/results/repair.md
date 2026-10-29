# The input repair, before and after

`edgecar.nlu.normalise` repairs a typed or transcribed question before it is understood: spoken forms of domain
words, number words before a unit, and spelling. Spelling is repaired only towards the training questions' words
and the code's real words, never into a control verb, and never when the word is already a common English word
(`src/edgecar/nlu/words.txt`, from the Qwen2.5 tokenizer, Apache-2.0) or an inflection of a known stem. A question
with nothing to repair passes through exactly as asked. 0.18 ms per question on x86.

x86 laptop, 2 threads, rules + edgecar-1.5b Q4_K_M unless marked. Every comparison is paired — the same questions
with the repair off and on — with a two-sided exact McNemar test.

## Protocol, and the one deviation from it

1. Designed and tuned on the dev questions only (`python -m edgecar.bench.robustness --dev`, dev Moonshine
   transcripts); design frozen at commit d8e7c3d; blind sets run once (commit 97f3a9c).
2. Before merging, the clean sets were scored with the repair off and on for all three models. That found two
   held-out questions the stock few-shot model lost, and listing every clean question the repair changed showed two
   bugs: regex escapes leaking into the vocabulary ("difference" rewritten as "bdifference"), and correct short words
   "repaired" ("point" → "print", "cost" → "most", "farthest" → "fastest") — 19 clean questions altered across the
   sets. The fix (commit ebee89b) adds the common-English list and the other "never repair" rules above.
3. **The blind sets were therefore run a second time**, with the fixed design. This is a deviation from "run once",
   disclosed here: the fix was prompted by *clean*-question damage, not by the blind perturbation errors, and it made
   the repair do less, not more. Both runs are reported.

## Clean questions — the published numbers stay valid

| Model | Held-out 70 | Refusal test 40 | Refusal dev 20 |
|---|---|---|---|
| edgecar-0.5b | 65 → 65 | 39 → 39 | 19 → 19 |
| edgecar-1.5b | 67 → 67 | 40 → 40 | 20 → 20 |
| Qwen2.5-1.5B few-shot (stock) | 67 → 67 | 40 → 40 ¹ | 19 → 19 |

With the fixed design no clean question in any evaluation set is changed except number words before a unit (three
dev questions: "two weeks" → "2 weeks"). With the first design the stock model lost t-cmp-7 and t-trd-3; t-cmp-7
("What difference does the outside temperature make to my consumption?") is also the item that flips between x86
and ARM — a borderline question for that model, which any change of wording (here, lower case) can tip.
([`repair-clean.json`](repair-clean.json))

¹ The published stock run has 39/40 on the refusal test; both runs here have 40/40. The one borderline refusal
("Is that normal?") depends on what the model server processed before it (prompt cache), not on the repair.

## Blind questions

| Held-out 70, wording | Without repair | First design (frozen) | **Fixed design** | Fixed: gained · lost, p |
|---|---|---|---|---|
| clean | 67 (95.7 %) | 67 | **67 (95.7 %)** | 0 · 0, 1.00 |
| lower case, no punctuation | 67 (95.7 %) | 67 | **67 (95.7 %)** | 0 · 0, 1.00 |
| typos | 53 (75.7 %) | 67 | **66 (94.3 %)** | 13 · 0, **0.0002** |
| spoken filler | 67 (95.7 %) | 66 | **67 (95.7 %)** | 0 · 0, 1.00 |
| numbers and units as words | 59 (84.3 %) | 67 | **67 (95.7 %)** | 8 · 0, **0.008** |
| real voice loop, same transcripts ² | 55 (78.6 %) | 58 | **59 (84.3 %)** | 4 · 0, 0.13 |

Refusal test: 40/40 declined in every wording, with and without the repair, in both runs. Rules alone (no model):
typos 32 → 51 of 70, spoken numbers 45 → 53.

² The speech loop is not deterministic — two runs of Piper → Moonshine on the same questions gave identical
transcripts for only 41 of 70 — so comparing two runs would mix recogniser variance into the result. The voice row
scores one saved set of transcripts with the repair off and on ([`voice-repair-paired.json`](voice-repair-paired.json)).
The fixed run's own voice row is in [`robustness.md`](robustness.md) (61/70), recognition p50 39.5 ms.

## What it means

- Typed noise is close to solved for these questions: 75.7 % → 94.3 %, against 95.7 % for clean wording.
- Numbers said as words — which the on-device recogniser writes for 11 of the 17 held-out questions containing
  numbers — no longer lose their time window: 84.3 % → 95.7 %.
- Speech improves less, and not significantly (4 more right, none lost): the remaining errors are mishearings no
  spelling rule can undo ("any of the normal trips" for "abnormal trips"). That needs a recogniser biased towards
  the domain's words (contextual biasing / hot-words), not more repair.
- Nothing that was right is lost: zero losses on every clean and perturbed set with the fixed design, every refusal
  still declined, and the published clean numbers of all three models unchanged.
