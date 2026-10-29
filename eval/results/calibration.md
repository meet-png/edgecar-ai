# Does the model know when it is wrong?

`python -m edgecar.bench.calibration --model edgecar-1.5b-q4_k_m.gguf` (x86 laptop, 2 threads): the model alone on
every question, in three wordings. Confidence = the lowest probability among the tokens the model wrote.
The threshold **1.000** was chosen on the dev questions (answer the most with ≤ 5% of
answers wrong) and applied unchanged to the blind ones. Below it the assistant would ask the driver to
rephrase instead of answering.

| Questions | n | Right, no threshold | AUROC | Answered | Right among answered | Right, asked to rephrase |
|---|---|---|---|---|---|---|
| dev · clean | 122 | 99.2% | 0.86 | 45.9% | 100.0% | 65 |
| dev · typos | 122 | 73.8% | 0.70 | 34.4% | 88.1% | 53 |
| dev · voice | 122 | 77.0% | 0.74 | 38.5% | 91.5% | 51 |
| refusal dev · clean | 20 | 100.0% | nan | 85.0% | 100.0% | 3 |
| refusal dev · typos | 20 | 100.0% | nan | 80.0% | 100.0% | 4 |
| refusal dev · voice | 20 | 100.0% | nan | 80.0% | 100.0% | 4 |
| held-out · clean | 70 | 97.1% | 0.64 | 35.7% | 96.0% | 44 |
| held-out · typos | 70 | 74.3% | 0.68 | 31.4% | 77.3% | 35 |
| held-out · voice | 70 | 78.6% | 0.76 | 30.0% | 95.2% | 35 |
| refusal test · clean | 40 | 100.0% | nan | 82.5% | 100.0% | 7 |
| refusal test · typos | 40 | 100.0% | nan | 80.0% | 100.0% | 8 |
| refusal test · voice | 40 | 100.0% | nan | 77.5% | 100.0% | 9 |
| **dev, pooled** (threshold chosen here) | 426 | 85.7% | 0.76 | 45.5% | 95.4% | 180 |
| **blind, pooled** | 330 | 89.4% | 0.79 | 49.7% | 95.7% | 138 |

AUROC is undefined (nan) where a group has no errors. The spoken questions are synthetic speech — cleaner
than a real driver in a moving car — so their numbers are an optimistic bound.

## The trade-off on noisy questions (mistyped and spoken, pooled)

| Threshold | dev: answered | dev: wrong among answered | blind: answered | blind: wrong among answered |
|---|---|---|---|---|
| 0.00 | 100.0% | 21.1% | 100.0% | 15.0% |
| 0.30 | 98.6% | 20.4% | 98.2% | 13.4% |
| 0.50 | 97.2% | 19.2% | 96.8% | 12.7% |
| 0.70 | 95.4% | 19.2% | 95.0% | 12.0% |
| 0.80 | 94.4% | 18.7% | 95.0% | 12.0% |
| 0.90 | 90.5% | 17.5% | 92.7% | 11.8% |
| 0.95 | 87.0% | 16.2% | 89.1% | 11.2% |
| 0.99 | 81.3% | 14.7% | 84.5% | 10.2% |

Reading: the model's confidence carries real signal (AUROC ≈ 0.75–0.8: a wrong answer usually has lower
confidence than a right one), but on noisy input it is not sharp enough to gate on — asking for a
rephrase on the least certain sixth of questions only trims the error rate by a third. Most noisy-input
errors are confident misreadings of a misspelt or misheard word, so the fix belongs before the model:
repairing the words (domain-vocabulary spelling correction, number words read as numbers), not abstaining.

