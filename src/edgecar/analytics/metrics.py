"""Metric definitions: how each metric is computed from the ``trips`` or ``frames`` tables."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricSpec:
    label: str
    unit: str
    trip_sql: str | None = None  # aggregate over rows of ``trips``
    frame_sql: str | None = None  # aggregate over rows of ``frames``
    higher_is_better: bool | None = None
    decimals: int = 1
    needs: str | None = None  # "battery" or "fuel": capability the vehicle must have


METRICS: dict[str, MetricSpec] = {
    "energy_consumption": MetricSpec(
        "energy consumption",
        "kWh/100 km",
        "100 * sum(energy_kwh) / nullif(sum(distance_km), 0)",
        higher_is_better=False,
    ),
    "energy_efficiency": MetricSpec(
        "energy efficiency",
        "km/kWh",
        "sum(distance_km) / nullif(sum(energy_kwh), 0)",
        higher_is_better=True,
        decimals=2,
    ),
    "range": MetricSpec(
        "estimated full-charge range",
        "km",
        "{capacity} * sum(distance_km) / nullif(sum(energy_kwh), 0)",
        higher_is_better=True,
        decimals=0,
        needs="battery",
    ),
    "fuel_consumption": MetricSpec(
        "fuel consumption",
        "L/100 km",
        "100 * sum(fuel_l) / nullif(sum(distance_km), 0)",
        higher_is_better=False,
        decimals=2,
        needs="fuel",
    ),
    "fuel_efficiency": MetricSpec(
        "fuel efficiency", "km/L", "sum(distance_km) / nullif(sum(fuel_l), 0)", higher_is_better=True, needs="fuel"
    ),
    "distance": MetricSpec("distance driven", "km", "sum(distance_km)", decimals=0),
    "energy_used": MetricSpec("battery energy used", "kWh", "sum(energy_kwh)"),
    "hvac_energy": MetricSpec("heating and A/C energy", "kWh", "sum(heater_kwh + ac_kwh)"),
    "regen_energy": MetricSpec("energy recovered by regenerative braking", "kWh", "sum(regen_kwh)"),
    "hard_brakes": MetricSpec("hard-braking events", "", "sum(hard_brakes)", higher_is_better=False, decimals=0),
    "trip_count": MetricSpec("trips", "", "count(*)", decimals=0),
    "speed": MetricSpec(
        "average moving speed", "km/h", frame_sql="avg(speed_kmh) FILTER (WHERE speed_kmh >= 1)", decimals=0
    ),
    "battery_temp": MetricSpec(
        "peak battery temperature", "°C", frame_sql="max(coalesce(batt_temp_max_c, batt_temp_c))"
    ),
    "soc": MetricSpec("average battery level", "%", frame_sql="avg(soc_pct)", decimals=0),
    "ambient_temp": MetricSpec("average outside temperature", "°C", frame_sql="avg(ambient_temp_c)"),
}

# Ask for fuel on a battery-electric car and you get the electric equivalent (and are told so).
BEV_EQUIVALENT = {"fuel_consumption": "energy_consumption", "fuel_efficiency": "energy_efficiency"}


def fmt(value: float | None, spec: MetricSpec | None = None, decimals: int | None = None) -> str:
    if value is None:
        return "n/a"
    d = decimals if decimals is not None else (spec.decimals if spec else 1)
    text = f"{value:,.{d}f}"
    return f"{text} {spec.unit}".strip() if spec and spec.unit else text
