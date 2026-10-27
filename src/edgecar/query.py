"""The typed query: the only thing the language layer is allowed to produce.

A question becomes one ``Query``. The analytics engine executes it deterministically. Keeping this vocabulary small
and closed is what lets a 0.5–1.5B model parse reliably under a JSON grammar.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

Intent = Literal[
    "summary",  # one aggregate value over a window          "How far did I drive last week?"
    "trend",  # how a metric evolves over a window          "Show battery temperature this month"
    "extreme",  # when a metric peaked / dropped the most     "When did efficiency drop the most?"
    "compare",  # metric across segments                      "City vs highway consumption"
    "explain_change",  # why a metric changed vs the previous period "Why has my range dropped?"
    "list_events",  # events matching a filter                    "Show ADAS disengagements yesterday"
    "events_before",  # what led up to an event                     "What happened before the overheat warning?"
    "anomalies",  # unusual trips / behaviour                   "Anything unusual this month?"
    "status",  # current values                              "What's my battery level?"
]

Metric = Literal[
    "energy_consumption",  # kWh/100 km  (lower is better)
    "energy_efficiency",  # km/kWh      (higher is better)
    "range",  # estimated full-charge range, km
    "fuel_consumption",  # L/100 km    (lower is better)
    "fuel_efficiency",  # km/L        (higher is better)
    "distance",  # km
    "energy_used",  # kWh
    "hvac_energy",  # kWh (heater + A/C)
    "regen_energy",  # kWh recovered
    "speed",  # km/h
    "battery_temp",  # °C
    "soc",  # %
    "ambient_temp",  # °C
    "hard_brakes",  # count
    "trip_count",
]

EventKind = Literal[
    "any",
    "adas_disengagement",
    "adas_takeover_request",
    "adas_any",
    "battery_overheat_warning",
    "dtc",
    "thermal_any",
    "charging",
    "hard_brake",
    "system",
]


class TimeWindow(BaseModel):
    kind: Literal["last_days", "today", "yesterday", "this_week", "last_week", "this_month", "last_month", "all"]
    days: int | None = Field(default=None, ge=1, le=366)

    def resolve(self, now: datetime) -> tuple[datetime, datetime]:
        today = datetime.combine(now.date(), datetime.min.time())
        match self.kind:
            case "last_days":
                return now - timedelta(days=self.days or 14), now
            case "today":
                return today, now
            case "yesterday":
                return today - timedelta(days=1), today
            case "this_week":
                return today - timedelta(days=today.weekday()), now
            case "last_week":
                start = today - timedelta(days=today.weekday() + 7)
                return start, start + timedelta(days=7)
            case "this_month":
                return today.replace(day=1), now
            case "last_month":
                first = today.replace(day=1)
                prev = (first - timedelta(days=1)).replace(day=1)
                return prev, first
            case _:
                return datetime(1970, 1, 1), now

    def phrase(self) -> str:
        """Time phrase ready to drop into a sentence: "over the last 14 days", "yesterday", …"""
        if self.kind == "last_days":
            return f"over the last {self.days or 14} days"
        return {"all": "across all recorded history"}.get(self.kind, self.kind.replace("_", " "))


class Query(BaseModel):
    intent: Intent
    metric: Metric | None = None
    window: TimeWindow = Field(default_factory=lambda: TimeWindow(kind="last_days", days=14))
    compare_by: Literal["road_type", "temperature", "previous_period"] | None = None
    direction: Literal["drop", "rise", "max", "min"] | None = None
    event: EventKind | None = None
    lookback_min: int | None = Field(default=None, ge=1, le=1440)
