"""Deterministic analytics: executes a ``Query`` against the store.

Every number that reaches the user is computed here, never by the language model. Each handler returns a
``Result`` holding the numbers (``facts``), a templated answer (``headline`` + ``details``), optional table and
chart data for the UI, and the ids of the trips/events used as evidence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from edgecar.analytics.metrics import BEV_EQUIVALENT, METRICS, MetricSpec, fmt
from edgecar.query import Query
from edgecar.store import Store
from edgecar.store.db import CITY_MAX_KMH, HIGHWAY_MIN_KMH

MIN_TRIP_KM = 0.5  # shorter "trips" are parking manoeuvres / logger noise
MODEL_MIN_KM = 2.0  # trips used to fit the consumption model
MIN_DAY_KM = 5.0  # days with less driving are skipped for per-km metrics
ADAPTIVE_TRIPS = 8  # explain_change falls back to "last N trips vs the N before" when a window is too sparse
RATIO_METRICS = {"energy_consumption", "energy_efficiency", "range", "fuel_consumption", "fuel_efficiency"}


@dataclass
class Result:
    intent: str
    headline: str
    facts: dict[str, Any] = field(default_factory=dict)
    details: list[str] = field(default_factory=list)
    table: list[dict] | None = None
    chart: dict | None = None
    evidence: list[str] = field(default_factory=list)
    window: tuple[datetime, datetime] | None = None
    notes: list[str] = field(default_factory=list)


def _d(ts: datetime) -> str:
    return ts.strftime("%a %d %b %Y")


def _pct(new: float, old: float) -> float | None:
    if old is None or new is None or old == 0 or math.isnan(old) or math.isnan(new):
        return None
    return 100.0 * (new - old) / abs(old)


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


class Engine:
    def __init__(self, store: Store, vehicle_id: str, now: datetime | None = None) -> None:
        self.store = store
        self.vehicle_id = vehicle_id
        rows = store.sql("SELECT * FROM vehicles WHERE vehicle_id = ?", [vehicle_id])
        if rows.empty:
            raise KeyError(f"unknown vehicle {vehicle_id!r}")
        self.vehicle = rows.iloc[0].to_dict()
        if now is None:
            now = store.sql("SELECT max(ts) AS t FROM frames WHERE vehicle_id = ?", [vehicle_id])["t"][0]
            now = pd.Timestamp(now).to_pydatetime()
        self.now = now
        self.capacity = _num(self.vehicle.get("battery_kwh_usable"))
        has_fuel = store.sql("SELECT count(*) AS n FROM trips WHERE vehicle_id = ? AND fuel_l > 0", [vehicle_id])["n"][
            0
        ]
        self.has_fuel = bool(has_fuel)

    # ------------------------------------------------------------------ entry

    def run(self, q: Query) -> Result:
        notes: list[str] = []
        if q.metric in BEV_EQUIVALENT and not self.has_fuel:
            notes.append(
                f"This vehicle is battery-electric, so {METRICS[q.metric].label} is reported as "
                f"{METRICS[BEV_EQUIVALENT[q.metric]].label}."
            )
            q = q.model_copy(update={"metric": BEV_EQUIVALENT[q.metric]})
        if q.metric == "range" and self.capacity is None:
            notes.append("Battery capacity is unknown for this vehicle, so range is reported as energy consumption.")
            q = q.model_copy(update={"metric": "energy_consumption"})
        res: Result = getattr(self, f"_{q.intent}")(q)
        res.notes = notes + res.notes
        return res

    # ---------------------------------------------------------------- helpers

    def _spec(self, metric: str | None, default: str = "energy_consumption") -> tuple[str, MetricSpec]:
        m = metric or default
        return m, METRICS[m]

    def _trip_expr(self, spec: MetricSpec) -> str:
        return spec.trip_sql.format(capacity=self.capacity or "NULL")

    def _trips(self, start: datetime, end: datetime) -> pd.DataFrame:
        return self.store.sql(
            "SELECT * FROM trips WHERE vehicle_id = ? AND start_ts >= ? AND start_ts < ? AND distance_km >= ? "
            "ORDER BY start_ts",
            [self.vehicle_id, start, end, MIN_TRIP_KM],
        )

    def _value(self, metric: str, start: datetime, end: datetime) -> float | None:
        spec = METRICS[metric]
        if spec.trip_sql:
            sql = (
                f"SELECT {self._trip_expr(spec)} AS v FROM trips WHERE vehicle_id = ? AND start_ts >= ? "
                f"AND start_ts < ? AND distance_km >= ?"
            )
            params = [self.vehicle_id, start, end, MIN_TRIP_KM]
        else:
            sql = f"SELECT {spec.frame_sql} AS v FROM frames WHERE vehicle_id = ? AND ts >= ? AND ts < ?"
            params = [self.vehicle_id, start, end]
        return _num(self.store.sql(sql, params)["v"][0])

    def _daily(self, metric: str, start: datetime, end: datetime) -> pd.DataFrame:
        spec = METRICS[metric]
        if spec.trip_sql:
            sql = (
                f"SELECT date_trunc('day', start_ts) AS day, {self._trip_expr(spec)} AS v, "
                f"sum(distance_km) AS km, count(*) AS trips FROM trips WHERE vehicle_id = ? AND start_ts >= ? "
                f"AND start_ts < ? AND distance_km >= ? GROUP BY day ORDER BY day"
            )
            params = [self.vehicle_id, start, end, MIN_TRIP_KM]
        else:
            sql = (
                f"SELECT date_trunc('day', ts) AS day, {spec.frame_sql} AS v FROM frames WHERE vehicle_id = ? "
                f"AND ts >= ? AND ts < ? GROUP BY day ORDER BY day"
            )
            params = [self.vehicle_id, start, end]
        return self.store.sql(sql, params).dropna(subset=["v"])

    def _window(self, q: Query) -> tuple[datetime, datetime]:
        return q.window.resolve(self.now)

    # --------------------------------------------------------------- intents

    def _summary(self, q: Query) -> Result:
        metric, spec = self._spec(q.metric, "distance")
        start, end = self._window(q)
        value = self._value(metric, start, end)
        prev = self._value(metric, start - (end - start), start) if q.window.kind != "all" else None
        change = _pct(value, prev) if value is not None and prev is not None else None
        headline = f"Your {spec.label} {q.window.phrase()} was {fmt(value, spec)}."
        details = []
        if change is not None:
            details.append(
                f"That is {abs(change):.0f}% {'higher' if change > 0 else 'lower'} than the previous "
                f"period ({fmt(prev, spec)})."
            )
        trips = self._trips(start, end)
        if value is None:
            headline = f"There is no {spec.label} data {q.window.phrase()}."
        return Result(
            "summary",
            headline,
            {"metric": metric, "value": value, "previous": prev, "change_pct": change, "trips": len(trips)},
            details,
            evidence=list(trips["trip_id"]),
            window=(start, end),
        )

    def _trend(self, q: Query) -> Result:
        metric, spec = self._spec(q.metric)
        start, end = self._window(q)
        daily = self._daily(metric, start, end)
        if metric in RATIO_METRICS:
            daily = daily[daily["km"] >= MIN_DAY_KM]  # one 2 km trip is not a representative day
        if len(daily) < 2:
            return Result(
                "trend",
                f"Not enough data to show a trend in {spec.label} {q.window.phrase()}.",
                {"metric": metric, "days": len(daily)},
                window=(start, end),
            )
        x = (daily["day"] - daily["day"].min()).dt.total_seconds().to_numpy() / 86400.0
        slope_per_week = float(np.polyfit(x, daily["v"].to_numpy(), 1)[0]) * 7 if len(daily) >= 3 else None
        half = len(daily) // 2
        first, second = float(daily["v"].iloc[:half].mean()), float(daily["v"].iloc[half:].mean())
        change = _pct(second, first)
        word = "rose" if second > first else "fell"
        headline = (
            f"Your {spec.label} {word} from {fmt(first, spec)} to {fmt(second, spec)} "
            f"({change:+.0f}%) {q.window.phrase()}."
            if change is not None
            else f"Your {spec.label} averaged {fmt(daily['v'].mean(), spec)} {q.window.phrase()}."
        )
        peak = daily.loc[daily["v"].idxmax()]
        low = daily.loc[daily["v"].idxmin()]
        details = [
            f"Highest: {fmt(peak['v'], spec)} on {_d(peak['day'])}; lowest: {fmt(low['v'], spec)} on {_d(low['day'])}."
        ]
        chart = {
            "type": "line",
            "x": [d.strftime("%Y-%m-%d") for d in daily["day"]],
            "series": {spec.label: [round(float(v), 3) for v in daily["v"]]},
            "unit": spec.unit,
        }
        return Result(
            "trend",
            headline,
            {
                "metric": metric,
                "first_half": first,
                "second_half": second,
                "change_pct": change,
                "slope_per_week": slope_per_week,
                "days": len(daily),
            },
            details,
            chart=chart,
            window=(start, end),
        )

    def _extreme(self, q: Query) -> Result:
        metric, spec = self._spec(q.metric, "energy_efficiency")
        direction = q.direction or "drop"
        start, end = self._window(q)
        daily = self._daily(metric, start, end)
        if metric in RATIO_METRICS:
            daily = daily[daily["km"] >= MIN_DAY_KM]
        if len(daily) < 4:
            return Result("extreme", f"Not enough driving days to compare {spec.label}.", {"metric": metric})
        if direction in ("max", "min"):
            row = daily.loc[daily["v"].idxmax() if direction == "max" else daily["v"].idxmin()]
            headline = (
                f"Your {'highest' if direction == 'max' else 'lowest'} {spec.label} was "
                f"{fmt(row['v'], spec)} on {_d(row['day'])}."
            )
            return Result(
                "extreme",
                headline,
                {"metric": metric, "day": row["day"], "value": float(row["v"])},
                window=(start, end),
            )
        # deviation from the trailing 7-driving-day median baseline
        daily = daily.reset_index(drop=True)
        daily["baseline"] = daily["v"].shift(1).rolling(7, min_periods=3).median()
        daily["dev_pct"] = 100.0 * (daily["v"] - daily["baseline"]) / daily["baseline"].abs()
        cand = daily.dropna(subset=["dev_pct"])
        row = cand.loc[cand["dev_pct"].idxmin() if direction == "drop" else cand["dev_pct"].idxmax()]
        day0 = row["day"].to_pydatetime()
        ctx = self._day_context(day0, day0 - timedelta(days=10))
        headline = (
            f"{spec.label.capitalize()} showed its largest {direction} on {_d(day0)}: "
            f"{fmt(row['v'], spec)} versus a typical {fmt(row['baseline'], spec)} "
            f"({row['dev_pct']:+.0f}%)."
        )
        chart = {
            "type": "line",
            "x": [d.strftime("%Y-%m-%d") for d in daily["day"]],
            "series": {
                spec.label: [round(float(v), 3) for v in daily["v"]],
                "typical (7-day median)": [None if pd.isna(b) else round(float(b), 3) for b in daily["baseline"]],
            },
            "unit": spec.unit,
            "highlight": day0.strftime("%Y-%m-%d"),
        }
        trips = self._trips(day0, day0 + timedelta(days=1))
        return Result(
            "extreme",
            headline,
            {
                "metric": metric,
                "day": day0,
                "value": float(row["v"]),
                "baseline": float(row["baseline"]),
                "deviation_pct": float(row["dev_pct"]),
                **ctx["facts"],
            },
            ctx["details"],
            chart=chart,
            evidence=list(trips["trip_id"]),
            window=(start, end),
        )

    def _day_context(self, day0: datetime, base_start: datetime) -> dict:
        """Compare one day's driving conditions with the preceding days."""

        def cond(s: datetime, e: datetime) -> dict:
            t = self._trips(s, e)
            km = t["distance_km"].sum()
            if km <= 0:
                return {}
            w = t["distance_km"] / km
            out = {
                "ambient": float((t["ambient_c"] * w).sum()) if t["ambient_c"].notna().any() else None,
                "hvac": 100.0 * float((t["heater_kwh"] + t["ac_kwh"]).sum()) / km,
                "highway": 100.0 * float((t["share_highway"].fillna(0) * w).sum()),
                "km": float(km),
                "trips": len(t),
            }
            if self.has_fuel:  # plug-in hybrid: how much of the distance was driven on electricity alone
                out["electric"] = _num(
                    self.store.sql(
                        "SELECT 100 * sum(speed_kmh) FILTER (WHERE coalesce(fuel_rate_lph, 0) < 0.05) "
                        "/ nullif(sum(speed_kmh), 0) AS p FROM frames WHERE vehicle_id = ? AND ts >= ? AND ts < ?",
                        [self.vehicle_id, s, e],
                    )["p"][0]
                )
            return out

        day, base = cond(day0, day0 + timedelta(days=1)), cond(base_start, day0)
        details, facts = [], {}
        if day and base:
            facts = {
                "day_ambient_c": day["ambient"],
                "base_ambient_c": base["ambient"],
                "day_hvac_kwh_100km": day["hvac"],
                "base_hvac_kwh_100km": base["hvac"],
                "day_highway_pct": day["highway"],
                "base_highway_pct": base["highway"],
            }
            if day.get("electric") is not None and base.get("electric") is not None:
                facts |= {"day_electric_pct": day["electric"], "base_electric_pct": base["electric"]}
                details.append(
                    f"{day['electric']:.0f}% of that day's distance was driven on electricity alone, "
                    f"vs {base['electric']:.0f}% in the days before, so the petrol engine ran for more "
                    f"of the distance."
                )
            if day["ambient"] is not None and base["ambient"] is not None:
                details.append(
                    f"Outside temperature averaged {day['ambient']:.1f} °C that day vs "
                    f"{base['ambient']:.1f} °C in the days before."
                )
            details.append(f"Heating/A-C used {day['hvac']:.1f} kWh/100 km vs {base['hvac']:.1f} kWh/100 km.")
            details.append(
                f"Highway share of distance: {day['highway']:.0f}% vs {base['highway']:.0f}%; "
                f"{day['trips']} trip(s), {day['km']:.0f} km."
            )
        return {"facts": facts, "details": details}

    def _compare(self, q: Query) -> Result:
        metric, spec = self._spec(q.metric)
        by = q.compare_by or "road_type"
        if by == "previous_period":
            s, e = self._window(q)
            now_v, prev_v = self._value(metric, s, e), self._value(metric, s - (e - s), s)
            ch = _pct(now_v, prev_v) if now_v is not None and prev_v is not None else None
            headline = (
                f"Your {spec.label} was {fmt(now_v, spec)} {q.window.phrase()}, compared with "
                f"{fmt(prev_v, spec)} in the period before" + (f" ({ch:+.0f}%)." if ch is not None else ".")
            )
            return Result(
                "compare",
                headline,
                {"metric": metric, "current": now_v, "previous": prev_v, "change_pct": ch},
                window=(s, e),
            )
        s, e = self._window(q)
        fuel = metric in ("fuel_consumption", "fuel_efficiency")
        if by == "road_type":
            rows = self.store.sql(
                f"""
                SELECT CASE WHEN speed_kmh < {CITY_MAX_KMH} THEN 'city'
                            WHEN speed_kmh >= {HIGHWAY_MIN_KMH} THEN 'highway' ELSE 'suburban' END AS segment,
                       sum(speed_kmh) / 3600 AS km, sum(batt_power_kw) / 3600 AS kwh,
                       sum(fuel_rate_lph) / 3600 AS fuel_l, avg(ambient_temp_c) AS ambient
                FROM frames WHERE vehicle_id = ? AND ts >= ? AND ts < ? AND speed_kmh IS NOT NULL
                GROUP BY segment""",
                [self.vehicle_id, s, e],
            )
            order = {"city": 0, "suburban": 1, "highway": 2}
            label = {
                "city": f"City (< {CITY_MAX_KMH:.0f} km/h)",
                "suburban": f"Suburban ({CITY_MAX_KMH:.0f}–{HIGHWAY_MIN_KMH:.0f} km/h)",
                "highway": f"Highway (≥ {HIGHWAY_MIN_KMH:.0f} km/h)",
            }
            rows = rows.sort_values("segment", key=lambda c: c.map(order))
        else:  # temperature
            rows = self.store.sql(
                """
                SELECT CASE WHEN ambient_c < 0 THEN 'below 0 °C' WHEN ambient_c < 10 THEN '0–10 °C'
                            WHEN ambient_c < 20 THEN '10–20 °C' ELSE '20 °C and above' END AS segment,
                       sum(distance_km) AS km, sum(energy_kwh) AS kwh, sum(fuel_l) AS fuel_l,
                       avg(ambient_c) AS ambient, min(ambient_c) AS lo
                FROM trips WHERE vehicle_id = ? AND start_ts >= ? AND start_ts < ? AND distance_km >= ?
                  AND ambient_c IS NOT NULL
                GROUP BY segment ORDER BY lo""",
                [self.vehicle_id, s, e, MIN_TRIP_KM],
            )
            label = {seg: seg for seg in rows["segment"]}
        rows = rows[rows["km"] > 1].copy()
        if rows.empty:
            return Result("compare", f"No driving data to compare {q.window.phrase()}.", {"metric": metric})
        if fuel:
            rows["value"] = (
                (100 * rows["fuel_l"] / rows["km"]) if metric == "fuel_consumption" else rows["km"] / rows["fuel_l"]
            )
        elif metric == "energy_efficiency":
            rows["value"] = rows["km"] / rows["kwh"]
        else:
            metric, spec = "energy_consumption", METRICS["energy_consumption"]
            rows["value"] = 100 * rows["kwh"] / rows["km"]
        total_km = rows["km"].sum()
        table = [
            {
                "segment": label[r.segment],
                "distance_km": round(r.km, 1),
                "share_pct": round(100 * r.km / total_km, 1),
                "value": round(float(r.value), 2),
                "unit": spec.unit,
            }
            for r in rows.itertuples()
        ]

        def phrase(seg: str) -> str:
            if by == "road_type":
                return f"{seg.split(' (')[0].lower()} driving"
            return f"driving {'' if seg.startswith('below') else 'at '}{seg}"

        def cap(s: str) -> str:
            return s[:1].upper() + s[1:]

        by_name = {r["segment"].split(" (")[0]: r for r in table}
        if by == "road_type" and {"City", "Highway"} <= by_name.keys():
            city, hwy = by_name["City"], by_name["Highway"]
            d = _pct(hwy["value"], city["value"])
            headline = (
                f"City driving averaged {fmt(city['value'], spec)} and highway driving "
                f"{fmt(hwy['value'], spec)} ({abs(d):.0f}% {'lower' if d < 0 else 'higher'} on the highway)."
            )
        else:
            lo, hi = min(table, key=lambda r: r["value"]), max(table, key=lambda r: r["value"])
            d = _pct(hi["value"], lo["value"])
            headline = (
                f"{cap(phrase(hi['segment']))} averaged {fmt(hi['value'], spec)}, {abs(d):.0f}% higher than "
                f"{phrase(lo['segment'])} ({fmt(lo['value'], spec)})."
            )
        details = [
            f"{r['segment']}: {fmt(r['value'], spec)} over {r['distance_km']:.0f} km "
            f"({r['share_pct']:.0f}% of distance)."
            for r in table
        ]
        if by == "road_type" and not fuel:
            details.append(
                "City figures include energy used while stopped (heating, A/C, electronics), "
                "which is why low speeds can cost more per km than steady suburban driving."
            )
        chart = {
            "type": "bar",
            "x": [r["segment"] for r in table],
            "series": {spec.label: [r["value"] for r in table]},
            "unit": spec.unit,
        }
        return Result(
            "compare",
            headline,
            {"metric": metric, "by": by, "segments": table},
            details,
            table=table,
            chart=chart,
            window=(s, e),
        )

    # ---- explain_change: decomposition of a consumption change into named factors

    def _fit_traction_model(self) -> tuple[np.ndarray, list[str]] | None:
        t = self.store.sql(
            "SELECT distance_km, traction_kwh, mean_v2, ambient_c, stops_per_km FROM trips "
            "WHERE vehicle_id = ? AND distance_km >= ? AND mean_v2 IS NOT NULL AND ambient_c IS NOT NULL "
            "AND start_ts < ?",
            [self.vehicle_id, MODEL_MIN_KM, self.now],
        ).dropna()
        if len(t) < 15:
            return None
        y = 100 * t["traction_kwh"] / t["distance_km"]
        names = ["intercept", "mean_v2", "ambient_c", "stops_per_km"]
        x = np.column_stack([np.ones(len(t)), t["mean_v2"] / 1000.0, t["ambient_c"], t["stops_per_km"]])
        w = np.sqrt(t["distance_km"].to_numpy())
        coef, *_ = np.linalg.lstsq(x * w[:, None], y.to_numpy() * w, rcond=None)
        return coef, names

    def _explain_change(self, q: Query) -> Result:
        metric = q.metric or "range"
        if metric not in ("range", "energy_consumption", "energy_efficiency"):
            metric = "energy_consumption"
        start, end = self._window(q)
        base_start = start - (end - start)
        a, b = self._trips(base_start, start), self._trips(start, end)
        period = q.window.phrase()
        notes: list[str] = []
        if len(a) < 3 or len(b) < 3:
            # Too little driving in the requested window: compare the most recent trips with the ones before.
            hist = self._trips(datetime(1970, 1, 1), end)
            hist = hist[hist["distance_km"] >= MODEL_MIN_KM]
            if len(hist) < 2 * ADAPTIVE_TRIPS:
                return Result(
                    "explain_change",
                    f"Not enough trips to explain the change ({len(b)} recent, {len(a)} earlier).",
                    {"metric": metric},
                    window=(start, end),
                )
            b, a = hist.iloc[-ADAPTIVE_TRIPS:], hist.iloc[-2 * ADAPTIVE_TRIPS : -ADAPTIVE_TRIPS]
            start = pd.Timestamp(b["start_ts"].iloc[0]).to_pydatetime()
            period = f"over your last {ADAPTIVE_TRIPS} trips (since {_d(start)})"
            notes.append(
                f"There were too few trips {q.window.phrase()}, so your last {ADAPTIVE_TRIPS} trips are "
                f"compared with the {ADAPTIVE_TRIPS} before them."
            )

        def stats(t: pd.DataFrame) -> dict:
            km = t["distance_km"].sum()
            w = t["distance_km"] / km
            return {
                "km": km,
                "c": 100 * t["energy_kwh"].sum() / km,
                "hvac": 100 * (t["heater_kwh"] + t["ac_kwh"]).sum() / km,
                "heater": 100 * t["heater_kwh"].sum() / km,
                "traction": 100 * t["traction_kwh"].sum() / km,
                "mean_v2": float((t["mean_v2"].fillna(t["mean_v2"].mean()) * w).sum()),
                "ambient": float((t["ambient_c"] * w).sum()),
                "stops": float((t["stops_per_km"].fillna(0) * w).sum()),
                "highway": 100 * float((t["share_highway"].fillna(0) * w).sum()),
                "trips": len(t),
            }

        sa, sb = stats(a), stats(b)
        d_total = sb["c"] - sa["c"]
        factors: dict[str, float] = {"Cabin heating and A/C": sb["hvac"] - sa["hvac"]}
        d_traction = sb["traction"] - sa["traction"]
        model = self._fit_traction_model()
        explained = 0.0
        if model is not None:
            coef, _ = model
            contrib_temp = coef[2] * (sb["ambient"] - sa["ambient"]) if coef[2] < 0 else 0.0
            contrib_speed = coef[1] * (sb["mean_v2"] - sa["mean_v2"]) / 1000.0 if coef[1] > 0 else 0.0
            contrib_stops = coef[3] * (sb["stops"] - sa["stops"]) if coef[3] > 0 else 0.0
            speed_label = (
                "Faster driving / more highway" if sb["mean_v2"] > sa["mean_v2"] else "Slower driving / less highway"
            )
            factors["Colder air, battery and tyres" if sb["ambient"] < sa["ambient"] else "Warmer weather"] = (
                contrib_temp
            )
            factors[speed_label] = contrib_speed
            factors["More stop-and-go traffic" if sb["stops"] > sa["stops"] else "Less stop-and-go"] = contrib_stops
            explained = contrib_temp + contrib_speed + contrib_stops
        factors["Other (driving style, load, tyre pressure)"] = d_traction - explained

        capacity = self.capacity
        r_a = capacity * 100 / sa["c"] if capacity else None
        r_b = capacity * 100 / sb["c"] if capacity else None
        table = []
        for name, dc in sorted(factors.items(), key=lambda kv: -abs(kv[1])):
            row = {"factor": name, "delta_kwh_100km": round(float(dc), 2)}
            if capacity and d_total:
                row["delta_range_km"] = round(float((r_b - r_a) * dc / d_total), 1)
            table.append(row)
        top = [r for r in table if (r["delta_kwh_100km"] > 0) == (d_total > 0)][:2]

        if metric == "range" and capacity:
            ch = _pct(r_b, r_a)
            headline = (
                f"Your estimated full-charge range {'fell' if r_b < r_a else 'rose'} from {r_a:.0f} km to "
                f"{r_b:.0f} km ({ch:+.0f}%) {period}, because energy use per km "
                f"{'rose' if d_total > 0 else 'fell'} from {sa['c']:.1f} to {sb['c']:.1f} kWh/100 km."
            )
        else:
            headline = (
                f"Energy consumption {'rose' if d_total > 0 else 'fell'} from {sa['c']:.1f} to "
                f"{sb['c']:.1f} kWh/100 km {period} ({_pct(sb['c'], sa['c']):+.0f}%)."
            )
        details = []
        for r in top:
            extra = f", ≈ {abs(r['delta_range_km']):.0f} km of range" if "delta_range_km" in r else ""
            details.append(f"{r['factor']}: {r['delta_kwh_100km']:+.1f} kWh/100 km{extra}.")
        details.append(
            f"Outside temperature averaged {sb['ambient']:.1f} °C vs {sa['ambient']:.1f} °C; heater energy "
            f"{sb['heater']:.1f} vs {sa['heater']:.1f} kWh/100 km; highway share {sb['highway']:.0f}% vs "
            f"{sa['highway']:.0f}%."
        )
        details.append(
            f"Based on {sb['trips']} recent trips ({sb['km']:.0f} km) and {sa['trips']} earlier trips "
            f"({sa['km']:.0f} km)."
        )
        chart = {
            "type": "waterfall",
            "x": [r["factor"] for r in table],
            "series": {"kWh/100 km": [r["delta_kwh_100km"] for r in table]},
            "start": round(sa["c"], 2),
            "end": round(sb["c"], 2),
            "unit": "kWh/100 km",
        }
        facts = {
            "metric": metric,
            "consumption_before": sa["c"],
            "consumption_after": sb["c"],
            "range_before_km": r_a,
            "range_after_km": r_b,
            "ambient_before": sa["ambient"],
            "ambient_after": sb["ambient"],
            "factors": table,
        }
        return Result(
            "explain_change",
            headline,
            facts,
            details,
            table=table,
            chart=chart,
            evidence=list(b["trip_id"]) + list(a["trip_id"]),
            window=(start, end),
            notes=notes,
        )

    # ---- events

    _EVENT_FILTER = {
        "any": "TRUE",
        "adas_any": "category = 'adas'",
        "adas_disengagement": "category = 'adas' AND code LIKE 'ADAS_DISENGAGE%'",
        "adas_takeover_request": "category = 'adas' AND code = 'ADAS_TAKEOVER_REQUEST'",
        "battery_overheat_warning": "code = 'BATT_OVERTEMP_WARNING'",
        "dtc": "category = 'dtc'",
        "thermal_any": "category = 'thermal'",
        "charging": "category = 'charging'",
        "hard_brake": "code = 'HARD_BRAKE'",
        "system": "category = 'system'",
    }

    def _events(self, kind: str, start: datetime, end: datetime) -> pd.DataFrame:
        cond = self._EVENT_FILTER[kind]
        return self.store.sql(
            f"SELECT * FROM events WHERE vehicle_id = ? AND ts >= ? AND ts < ? AND {cond} ORDER BY ts",
            [self.vehicle_id, start, end],
        )

    def _list_events(self, q: Query) -> Result:
        kind = q.event or "any"
        start, end = self._window(q)
        ev = self._events(kind, start, end)
        name = kind.replace("_", " ").replace("adas", "ADAS").replace("dtc", "diagnostic trouble code")
        if ev.empty:
            return Result("list_events", f"No {name} events {q.window.phrase()}.", {"count": 0}, window=(start, end))
        table = [
            {
                "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "code": r.code,
                "severity": r.severity,
                "source": r.source,
                "title": r.title,
                "provenance": r.provenance,
            }
            for r in ev.itertuples()
        ]
        headline = f"{len(ev)} {name} event{'s' if len(ev) != 1 else ''} {q.window.phrase()}."
        details = [f"{t['time'][11:19]} — {t['title']} ({t['source']})" for t in table[:8]]
        if len(table) > 8:
            details.append(f"…and {len(table) - 8} more.")
        return Result(
            "list_events",
            headline,
            {"count": len(ev), "kind": kind},
            details,
            table=table,
            evidence=list(ev["event_id"]),
            window=(start, end),
        )

    def _events_before(self, q: Query) -> Result:
        kind = q.event or "battery_overheat_warning"
        start, end = self._window(q) if q.window.kind != "last_days" else (datetime(1970, 1, 1), self.now)
        anchors = self._events(kind, start, end)
        if anchors.empty:
            return Result("events_before", f"No {kind.replace('_', ' ')} event was found.", {"kind": kind})
        anchor = anchors.iloc[-1]
        t1 = pd.Timestamp(anchor["ts"]).to_pydatetime()
        lookback = timedelta(minutes=q.lookback_min or 15)
        t0 = t1 - lookback
        prior = self._events("any", t0, t1 + timedelta(seconds=1))
        prior = prior[prior["event_id"] != anchor["event_id"]]
        sig = self.store.sql(
            """
            SELECT ts, speed_kmh, batt_power_kw, coalesce(batt_temp_max_c, batt_temp_c) AS batt_temp,
                   ambient_temp_c, hvac_heater_kw + hvac_ac_kw AS hvac_kw, soc_pct
            FROM frames WHERE vehicle_id = ? AND ts >= ? AND ts <= ? ORDER BY ts""",
            [self.vehicle_id, t0, t1],
        )
        timeline = [
            {"time": r.ts.strftime("%H:%M:%S"), "kind": "event", "code": r.code, "text": r.title, "source": r.source}
            for r in prior.itertuples()
        ]
        details: list[str] = []
        facts: dict[str, Any] = {
            "anchor_time": t1,
            "anchor_code": anchor["code"],
            "lookback_min": lookback.total_seconds() / 60,
            "prior_events": len(prior),
        }
        if not sig.empty:
            for col, label, unit in [
                ("batt_temp", "Battery temperature", "°C"),
                ("batt_power_kw", "Battery power", "kW"),
                ("speed_kmh", "Speed", "km/h"),
            ]:
                s = sig[col].dropna()
                if s.empty:
                    continue
                facts[f"{col}_start"], facts[f"{col}_end"] = float(s.iloc[0]), float(s.iloc[-1])
                facts[f"{col}_max"] = float(s.max())
                details.append(f"{label}: {s.iloc[0]:.1f} → {s.iloc[-1]:.1f} {unit} (peak {s.max():.1f} {unit}).")
        headline = (
            f"In the {lookback.total_seconds() / 60:.0f} minutes before the {anchor['title'].lower()} at "
            f"{t1:%H:%M:%S} on {_d(t1)}, {len(prior)} related event{'s' if len(prior) != 1 else ''} "
            f"occurred."
        )
        details = [f"{e['time']} — {e['text']} ({e['source']})" for e in timeline[:10]] + details
        chart = None
        if not sig.empty:
            chart = {
                "type": "line",
                "x": [t.strftime("%H:%M:%S") for t in sig["ts"]],
                "series": {
                    "battery temp (°C)": [None if pd.isna(v) else round(float(v), 2) for v in sig["batt_temp"]],
                    "speed (km/h)": [None if pd.isna(v) else round(float(v), 1) for v in sig["speed_kmh"]],
                },
                "unit": "",
            }
        return Result(
            "events_before",
            headline,
            facts,
            details,
            table=timeline,
            chart=chart,
            evidence=[anchor["event_id"], *prior["event_id"]],
            window=(t0, t1),
        )

    def _anomalies(self, q: Query) -> Result:
        start, end = self._window(q)
        hist = self.store.sql(
            "SELECT * FROM trips WHERE vehicle_id = ? AND distance_km >= ? AND start_ts < ? ORDER BY start_ts",
            [self.vehicle_id, MODEL_MIN_KM, end],
        )
        model = self._fit_traction_model()
        flagged = []
        if model is not None and len(hist) >= 15:
            coef, _ = model
            x = np.column_stack(
                [
                    np.ones(len(hist)),
                    hist["mean_v2"].fillna(hist["mean_v2"].mean()) / 1000.0,
                    hist["ambient_c"].fillna(hist["ambient_c"].mean()),
                    hist["stops_per_km"].fillna(0),
                ]
            )
            pred = x @ coef + 100 * (hist["heater_kwh"] + hist["ac_kwh"]) / hist["distance_km"]
            resid = hist["kwh_per_100km"] - pred
            mad = float(np.median(np.abs(resid - np.median(resid)))) or 1.0
            hist["z"] = (resid - np.median(resid)) / (1.4826 * mad)
            recent = hist[(hist["start_ts"] >= start) & (hist["start_ts"] < end)]
            flagged = [
                {
                    "trip_id": r.trip_id,
                    "time": r.start_ts.strftime("%Y-%m-%d %H:%M"),
                    "kwh_per_100km": round(r.kwh_per_100km, 1),
                    "expected": round(r.kwh_per_100km - (r.z * 1.4826 * mad), 1),
                    "z": round(r.z, 1),
                }
                for r in recent.itertuples()
                if abs(r.z) >= 3
            ]
        events = self.store.sql(
            "SELECT * FROM events WHERE vehicle_id = ? AND ts >= ? AND ts < ? AND severity IN ('warning','critical') "
            "ORDER BY ts",
            [self.vehicle_id, start, end],
        )
        parts = []
        if flagged:
            parts.append(f"{len(flagged)} trip{'s' if len(flagged) != 1 else ''} with unusual energy use")
        if not events.empty:
            parts.append(f"{len(events)} warning/critical event{'s' if len(events) != 1 else ''}")
        headline = (
            f"Found {' and '.join(parts)} {q.window.phrase()}."
            if parts
            else f"Nothing unusual {q.window.phrase()}: energy use matched what driving conditions predict "
            "and no warnings were logged."
        )
        details = [
            f"{f['time']}: {f['kwh_per_100km']} kWh/100 km where ≈{f['expected']} was expected (z = {f['z']:+.1f})."
            for f in flagged[:5]
        ]
        details += [f"{r.ts:%Y-%m-%d %H:%M} — {r.title} ({r.severity})" for r in events.head(5).itertuples()]
        return Result(
            "anomalies",
            headline,
            {"trips_flagged": len(flagged), "events": len(events)},
            details,
            table=flagged or None,
            evidence=[f["trip_id"] for f in flagged] + list(events["event_id"]),
            window=(start, end),
        )

    def _status(self, q: Query) -> Result:
        last = self.store.sql(
            "SELECT * FROM frames WHERE vehicle_id = ? AND ts <= ? ORDER BY ts DESC LIMIT 1",
            [self.vehicle_id, self.now],
        )
        if last.empty:
            return Result("status", "No recent data from the vehicle.", {})
        r = last.iloc[0]
        soc = _num(r["soc_pct"])
        recent_c = self._value("energy_consumption", self.now - timedelta(days=14), self.now)
        rng = self.capacity * (soc / 100) * 100 / recent_c if self.capacity and soc and recent_c else None
        facts = {
            "time": r["ts"],
            "soc_pct": soc,
            "range_km": rng,
            "ambient_c": _num(r["ambient_temp_c"]),
            "battery_temp_c": _num(r["batt_temp_max_c"]) or _num(r["batt_temp_c"]),
        }
        headline = (
            f"Battery at {soc:.0f}%"
            + (f", about {rng:.0f} km of range at your recent consumption" if rng else "")
            + "."
            if soc is not None
            else "Vehicle status:"
        )
        details = []
        if facts["ambient_c"] is not None:
            details.append(f"Outside temperature {facts['ambient_c']:.1f} °C.")
        if facts["battery_temp_c"] is not None:
            details.append(f"Battery temperature {facts['battery_temp_c']:.1f} °C.")
        details.append(f"Last update {pd.Timestamp(r['ts']):%Y-%m-%d %H:%M}.")
        return Result("status", headline, facts, details)
