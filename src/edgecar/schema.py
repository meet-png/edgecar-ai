"""Canonical vocabulary shared by every layer: signals, units, event categories.

Every data source (CAN frame, SOME/IP notification, VHAL property, dataset column) is mapped onto these names
before it reaches the store, so analytics and the language layer only ever see one schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


@dataclass(frozen=True)
class Signal:
    name: str
    unit: str
    description: str
    aliases: tuple[str, ...] = field(default_factory=tuple)
    # How a window of samples is summarised when rolled up to a coarser tier.
    rollup: str = "mean"


# Physically plausible ranges. Ingestion discards samples outside them as sensor or logger glitches
# (e.g. a 39.9 kW reading from the BMW i3's ~7 kW cabin heater, or a 135 % accelerator position).
VALID_RANGE: dict[str, tuple[float, float]] = {
    "speed_kmh": (0, 250),
    "soc_pct": (0, 100),
    "batt_voltage_v": (50, 1000),
    "batt_current_a": (-1000, 1000),
    "batt_power_kw": (-500, 500),
    "batt_temp_c": (-40, 90),
    "batt_temp_max_c": (-40, 90),
    "ambient_temp_c": (-50, 60),
    "cabin_temp_c": (-40, 80),
    "hvac_heater_kw": (0, 8),
    "hvac_ac_kw": (0, 8),
    "elevation_m": (-500, 9000),
    "accel_mps2": (-15, 15),
    "throttle_pct": (0, 100),
    "motor_torque_nm": (-1000, 1000),
    "regen_active": (0, 1),
    "lat_deg": (-90, 90),
    "lon_deg": (-180, 180),
    "fuel_rate_lph": (0, 100),
    "engine_rpm": (0, 10000),
}


SIGNALS: dict[str, Signal] = {
    s.name: s
    for s in [
        Signal("speed_kmh", "km/h", "Vehicle speed", ("speed", "velocity", "how fast")),
        Signal(
            "soc_pct",
            "%",
            "High-voltage battery state of charge",
            ("battery level", "charge level", "state of charge", "soc", "battery percentage"),
        ),
        Signal("batt_voltage_v", "V", "High-voltage battery pack voltage", ("battery voltage", "pack voltage")),
        Signal(
            "batt_current_a",
            "A",
            "High-voltage battery current, positive = discharging",
            ("battery current", "pack current"),
        ),
        Signal(
            "batt_power_kw",
            "kW",
            "High-voltage battery power, positive = discharging",
            ("battery power", "power draw", "power consumption"),
        ),
        Signal(
            "batt_temp_c", "°C", "Battery temperature (average)", ("battery temperature", "pack temperature"), "mean"
        ),
        Signal(
            "batt_temp_max_c",
            "°C",
            "Hottest battery temperature sensor",
            ("max battery temperature", "hottest cell"),
            "max",
        ),
        Signal(
            "ambient_temp_c",
            "°C",
            "Outside air temperature",
            ("outside temperature", "ambient temperature", "weather", "cold", "temperature outside"),
        ),
        Signal("cabin_temp_c", "°C", "Cabin air temperature", ("cabin temperature", "inside temperature")),
        Signal("hvac_heater_kw", "kW", "Cabin heater electrical power", ("heater power", "heating", "heater")),
        Signal(
            "hvac_ac_kw", "kW", "Air-conditioning compressor electrical power", ("ac power", "air conditioning", "a/c")
        ),
        Signal("elevation_m", "m", "Elevation above sea level", ("altitude", "elevation")),
        Signal("accel_mps2", "m/s²", "Longitudinal acceleration", ("acceleration", "braking")),
        Signal("throttle_pct", "%", "Accelerator pedal position", ("throttle", "accelerator pedal")),
        Signal("motor_torque_nm", "Nm", "Traction motor torque", ("motor torque", "torque")),
        Signal("regen_active", "bool", "Regenerative braking active", ("regen", "regenerative braking"), "max"),
        Signal("lat_deg", "°", "Latitude", ("latitude", "location"), "last"),
        Signal("lon_deg", "°", "Longitude", ("longitude", "location"), "last"),
        Signal("fuel_rate_lph", "L/h", "Engine fuel flow rate", ("fuel rate", "fuel consumption", "fuel flow")),
        Signal("engine_rpm", "rpm", "Engine speed", ("rpm", "engine speed", "revs")),
    ]
}

# Columns of the canonical 1 Hz frame table, in storage order.
FRAME_COLUMNS: tuple[str, ...] = ("ts", "vehicle_id", "trip_id", *SIGNALS.keys())


class EventCategory(StrEnum):
    TRIP = "trip"
    CHARGING = "charging"
    ADAS = "adas"
    DTC = "dtc"
    THERMAL = "thermal"
    DRIVING = "driving"
    HVAC = "hvac"
    SYSTEM = "system"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Provenance(StrEnum):
    """Where a record came from. Shown to users; never hidden."""

    REAL_VED = "real:ved"
    REAL_TUM = "real:tum"
    SIM = "sim"
