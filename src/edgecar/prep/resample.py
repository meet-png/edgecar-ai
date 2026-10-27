"""Resample irregular per-trip samples onto a regular 1 Hz grid."""

from __future__ import annotations

import pandas as pd

from edgecar.schema import SIGNALS

# Short sensor dropouts are bridged by linear interpolation; longer gaps stay empty.
MAX_GAP_S = 5

# Temperatures of large thermal masses cannot jump: a sample this far from its local median is a sensor glitch.
SLOW_SIGNALS = ("batt_temp_c", "batt_temp_max_c", "cabin_temp_c", "ambient_temp_c")
SPIKE_C = 5.0


def despike(df: pd.DataFrame, window: int = 21) -> pd.DataFrame:
    """Blank out isolated spikes in slowly varying temperature channels (window in samples)."""
    df = df.copy()
    for c in SLOW_SIGNALS:
        if c in df:
            med = df[c].rolling(window, center=True, min_periods=1).median()
            df.loc[(df[c] - med).abs() > SPIKE_C, c] = float("nan")
    return df


def to_1hz(trip: pd.DataFrame) -> pd.DataFrame:
    """Resample one trip (columns: ``ts`` + canonical signals) to one row per second.

    Each second takes the mean of its samples (max for peak-type signals, last for positions); seconds with no
    sample are interpolated only when the gap is at most ``MAX_GAP_S`` seconds.
    """
    signal_cols = [c for c in trip.columns if c in SIGNALS]
    trip = trip.sort_values("ts")
    second = trip["ts"].dt.floor("s")
    agg = {c: {"max": "max", "last": "last"}.get(SIGNALS[c].rollup, "mean") for c in signal_cols}
    per_s = trip.groupby(second)[signal_cols].agg(agg)
    grid = pd.date_range(per_s.index.min(), per_s.index.max(), freq="s")
    per_s = per_s.reindex(grid)
    per_s[signal_cols] = per_s[signal_cols].interpolate(method="linear", limit=MAX_GAP_S, limit_area="inside")
    per_s.index.name = "ts"
    return per_s.reset_index()
