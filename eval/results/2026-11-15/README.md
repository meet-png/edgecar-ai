# 2026-11-15 — after two rule fixes found on the held-out set

`python -m edgecar.bench.nlu_eval --mode hybrid` (x86 laptop, 2 threads) on every set, three models, after fixing two
gaps the held-out set exposed: "since the start of the month" was read as a 14-day window (`t-exp-1`), and "am I
using less fuel" as energy (`t-cmp-8`). Both questions are therefore **no longer blind**; the blind comparison stays
in [`../rerun/`](../rerun) and [`../significance.md`](../significance.md).

| Set | edgecar-0.5b | edgecar-1.5b | Qwen2.5-1.5B few-shot |
|---|---|---|---|
| Held-out (70) | 95.7 % | 98.6 % | 98.6 % |
| Refusal test (40) | 39/40 | 40/40 | 40/40 |
| Dev (122) | 100 % | 100 % | 93.4 % |
| Refusal dev (20) | 19/20 | 20/20 | 19/20 |

Nothing else changed: on every set the only rows that differ from the blind runs are those two questions.
