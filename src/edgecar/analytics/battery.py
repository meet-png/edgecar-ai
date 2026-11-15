"""Battery health from the vehicle's own driving history (ADR-0011).

The usable capacity of a pack is the charge it delivers per unit of state of charge. On every long enough stretch of
driving, the battery management system's own current and state-of-charge signals give one measurement:

    capacity [Ah] = ∫ I dt  /  (ΔSoC / 100)

Single measurements are noisy (SoC is itself an estimate, and moves in coarse steps), so the estimate is the median
over all stretches, with a bootstrap interval, and the trend is a Theil–Sen slope over time — both robust to the
odd bad stretch. State of health is that capacity over the pack's rated capacity, when the rating is known.

Everything here is arithmetic over recorded signals: no model is involved, so every number can be recomputed from
the same data — which is what lets a report built from it be signed (``edgecar.passport``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

MIN_DSOC = 15.0  # % of charge a stretch must use: shorter ones are dominated by the SoC signal's resolution
MAX_GAP_S = 5.0  # samples further apart than this are not integrated across (logger gaps)
HIGH_SOC = 90.0  # parked above this, the pack ages faster (calendar ageing)
HOT_C = 35.0  # battery temperature above which it ages faster
LOW_SOC = 10.0  # deep discharges
BOOTSTRAP = 2000

# Rated capacity of packs we know (Ah). The store's vehicle names carry them; anything else gets no SoH, only Ah.
RATED_AH = {"BMW i3 (60 Ah)": 60.0, "Nissan Leaf (2013, 24 kWh)": 66.2}


@dataclass
class Stretch:
    trip_id: str
    start: datetime
    dsoc_pct: float
    charge_ah: float
    energy_kwh: float
    battery_temp_c: float | None

    @property
    def capacity_ah(self) -> float:
        return self.charge_ah / (self.dsoc_pct / 100.0)

    @property
    def capacity_kwh(self) -> float:
        return self.energy_kwh / (self.dsoc_pct / 100.0)


@dataclass
class Health:
    n: int  # stretches measured
    first: datetime | None
    last: datetime | None
    capacity_ah: float | None
    capacity_ah_ci: tuple[float, float] | None
    capacity_kwh: float | None
    rated_ah: float | None
    soh_pct: float | None
    soh_pct_ci: tuple[float, float] | None
    trend_pct_per_year: float | None  # change of capacity per year, % of the median; None below 6 stretches / 60 days
    trend_ci: tuple[float, float] | None
    wear: dict = field(default_factory=dict)
    stretches: list[Stretch] = field(default_factory=list)

    def summary(self) -> dict:
        d = asdict(self)
        d.pop("stretches")
        return d


def stretches(frames: pd.DataFrame, min_dsoc: float = MIN_DSOC) -> list[Stretch]:
    """One measurement per trip that used at least ``min_dsoc`` % of charge. ``frames``: ts, trip_id, soc_pct,
    batt_current_a (positive = discharging), batt_voltage_v, batt_temp_c."""
    out: list[Stretch] = []
    f = frames.dropna(subset=["soc_pct", "batt_current_a", "batt_voltage_v"]).sort_values(["trip_id", "ts"])
    for trip_id, t in f.groupby("trip_id", sort=False):
        if len(t) < 60:
            continue
        dt = t["ts"].diff().dt.total_seconds().to_numpy()
        ok = (dt > 0) & (dt <= MAX_GAP_S)
        i = t["batt_current_a"].to_numpy()
        u = t["batt_voltage_v"].to_numpy()
        ah = float(np.sum(np.where(ok, i * dt, 0.0)) / 3600.0)
        kwh = float(np.sum(np.where(ok, u * i * dt, 0.0)) / 3.6e6)
        dsoc = float(t["soc_pct"].iloc[0] - t["soc_pct"].iloc[-1])
        if dsoc < min_dsoc or ah <= 0:
            continue
        temp = t["batt_temp_c"].mean() if "batt_temp_c" in t else np.nan
        out.append(
            Stretch(str(trip_id), t["ts"].iloc[0].to_pydatetime(), dsoc, ah, kwh, None if np.isnan(temp) else temp)
        )
    return out


def _bootstrap_median(x: np.ndarray, rng: np.random.Generator) -> tuple[float, float]:
    meds = np.median(rng.choice(x, size=(BOOTSTRAP, len(x)), replace=True), axis=1)
    lo, hi = np.percentile(meds, [2.5, 97.5])
    return float(lo), float(hi)


def theil_sen(days: np.ndarray, y: np.ndarray) -> float:
    """Median of pairwise slopes (per day)."""
    i, j = np.triu_indices(len(days), k=1)
    dx = days[j] - days[i]
    keep = dx > 0
    return float(np.median((y[j] - y[i])[keep] / dx[keep]))


def estimate(st: list[Stretch], rated_ah: float | None = None, seed: int = 0) -> Health:
    if not st:
        return Health(0, None, None, None, None, None, rated_ah, None, None, None, None)
    rng = np.random.default_rng(seed)
    cap = np.array([s.capacity_ah for s in st])
    med = float(np.median(cap))
    ci = _bootstrap_median(cap, rng) if len(cap) >= 3 else None
    soh = 100.0 * med / rated_ah if rated_ah else None
    soh_ci = (100.0 * ci[0] / rated_ah, 100.0 * ci[1] / rated_ah) if rated_ah and ci else None
    t0 = min(s.start for s in st)
    days = np.array([(s.start - t0).total_seconds() / 86400.0 for s in st])
    trend = trend_ci = None
    if len(st) >= 6 and days.max() - days.min() >= 60:
        trend = 100.0 * 365.0 * theil_sen(days, cap) / med
        boots = []
        for _ in range(500):
            k = rng.integers(0, len(st), len(st))
            if np.ptp(days[k]) > 0:
                boots.append(100.0 * 365.0 * theil_sen(days[k], cap[k]) / med)
        lo, hi = np.percentile(boots, [2.5, 97.5])
        trend_ci = (float(lo), float(hi))
    return Health(
        n=len(st),
        first=t0,
        last=max(s.start for s in st),
        capacity_ah=med,
        capacity_ah_ci=ci,
        capacity_kwh=float(np.median([s.capacity_kwh for s in st])),
        rated_ah=rated_ah,
        soh_pct=soh,
        soh_pct_ci=soh_ci,
        trend_pct_per_year=trend,
        trend_ci=trend_ci,
        stretches=st,
    )


def wear(frames: pd.DataFrame, charges: pd.DataFrame) -> dict:
    """Exposure to what is known to age a lithium-ion pack faster, as recorded: hours parked above 90 % after a
    charge, driving hours with the pack above 35 °C, and stretches driven below 10 %. ``charges``: the charging
    events' details (soc_to, parked_hours)."""
    dt = frames["ts"].diff().dt.total_seconds().clip(upper=MAX_GAP_S).fillna(0) / 3600.0
    hot = float(dt[frames["batt_temp_c"] > HOT_C].sum()) if "batt_temp_c" in frames else None
    low = float(dt[frames["soc_pct"] < LOW_SOC].sum())
    parked_high = float(charges.loc[charges["soc_to"] >= HIGH_SOC, "parked_hours"].sum()) if len(charges) else 0.0
    return {
        "charges": int(len(charges)),
        "charges_to_90_plus": int((charges["soc_to"] >= HIGH_SOC).sum()) if len(charges) else 0,
        "hours_parked_above_90": round(parked_high, 1),
        "hours_driven_above_35c": None if hot is None or np.isnan(hot) else round(hot, 1),
        "hours_driven_below_10": round(low, 1),
    }
