import math

import pytest

from edgecar.stats import percentile


def test_percentile_stays_within_the_data():
    xs = [93.0, 110.2, 112.0, 119.4]  # four questions: the exclusive definition put their p95 at 125.9 ms
    assert percentile(xs, 95) <= max(xs)
    assert percentile(xs, 50) == pytest.approx(111.1)


def test_percentile_small_inputs():
    assert percentile([5.0], 95) == 5.0
    assert math.isnan(percentile([], 50))
