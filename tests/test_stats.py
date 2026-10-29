import math
import statistics

import pytest

from edgecar.stats import bootstrap_ci, mcnemar_exact, percentile, wilson


def test_percentile_stays_within_the_data():
    xs = [93.0, 110.2, 112.0, 119.4]  # four questions: the exclusive definition put their p95 at 125.9 ms
    assert percentile(xs, 95) <= max(xs)
    assert percentile(xs, 50) == pytest.approx(111.1)


def test_percentile_small_inputs():
    assert percentile([5.0], 95) == 5.0
    assert math.isnan(percentile([], 50))


def test_wilson_interval_matches_the_textbook_values():
    lo, hi = wilson(40, 40)  # a perfect score on 40 is still only "at least ~91 %"
    assert round(lo, 3) == 0.912 and hi == 1.0
    lo, hi = wilson(66, 70)
    assert round(lo, 3) == 0.862 and round(hi, 3) == 0.978


def test_mcnemar_needs_enough_disagreements():
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(2, 1) == 1.0  # one model wins 2 questions, the other 1: no evidence either way
    assert round(mcnemar_exact(10, 1), 4) == 0.0117  # 2 * P(X <= 1), X ~ Binomial(11, 1/2)


def test_bootstrap_interval_brackets_the_median():
    xs = [float(i) for i in range(1, 101)]
    lo, hi = bootstrap_ci(xs, reps=2000)
    assert lo < statistics.median(xs) < hi and bootstrap_ci(xs, reps=2000) == (lo, hi)  # seeded: reproducible
