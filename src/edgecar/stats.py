"""Statistics for the reports: latency percentiles, and how far small-sample accuracy can be trusted.

``statistics.quantiles`` defaults to the "exclusive" definition, which extrapolates past the data for small samples:
a p95 above the slowest question. Reports use the "inclusive" definition instead — linear interpolation between
order statistics, NumPy's default — which always lies within the observed range.
"""

from __future__ import annotations

import math
import random
import statistics


def percentile(xs: list[float], p: int) -> float:
    """The p-th percentile (1–99) of ``xs``; NaN for an empty list."""
    if not xs:
        return float("nan")
    if len(xs) == 1:
        return float(xs[0])
    return statistics.quantiles(xs, n=100, method="inclusive")[p - 1]


# ---- accuracy on small evaluation sets
#
# The evaluation sets are small (70 held-out questions, 40 refusals), so a single question moves accuracy by 1.4 or
# 2.5 points. These helpers say how much a number — or a difference between two models — can be trusted.


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """95 % Wilson score interval for k successes in n trials. Unlike the normal approximation it stays inside
    [0, 1] and is not degenerate at 100 % (40/40 gives 91.2–100 %, not 100–100 %)."""
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def mcnemar_exact(only_a: int, only_b: int) -> float:
    """Two-sided exact McNemar p-value for two models scored on the same questions: ``only_a`` questions only model
    A got right, ``only_b`` only model B. Questions both got right (or wrong) carry no information about which is
    better, so with a handful of disagreements no difference can be significant."""
    n = only_a + only_b
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(only_a, only_b) + 1)) / 2**n
    return min(1.0, 2 * tail)


def bootstrap_ci(
    xs: list[float], stat=statistics.median, reps: int = 10_000, seed: int = 0, level: float = 0.95
) -> tuple[float, float]:
    """Percentile-bootstrap interval for ``stat`` of ``xs`` (e.g. a latency median), with a fixed seed so reports
    are reproducible."""
    if not xs:
        return float("nan"), float("nan")
    rng = random.Random(seed)
    n = len(xs)
    sims = sorted(stat([xs[rng.randrange(n)] for _ in range(n)]) for _ in range(reps))
    lo = int((1 - level) / 2 * reps)
    return sims[lo], sims[min(reps - 1, reps - 1 - lo)]
