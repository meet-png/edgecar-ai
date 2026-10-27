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
