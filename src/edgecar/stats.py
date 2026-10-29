"""Percentiles for latency reports (the benchmarks and the live-ingest readout).

``statistics.quantiles`` defaults to the "exclusive" definition, which extrapolates past the data for small samples:
a p95 above the slowest question. Reports use the "inclusive" definition instead — linear interpolation between
order statistics, NumPy's default — which always lies within the observed range.
"""

from __future__ import annotations

import statistics


def percentile(xs: list[float], p: int) -> float:
    """The p-th percentile (1–99) of ``xs``; NaN for an empty list."""
    if not xs:
        return float("nan")
    if len(xs) == 1:
        return float(xs[0])
    return statistics.quantiles(xs, n=100, method="inclusive")[p - 1]
