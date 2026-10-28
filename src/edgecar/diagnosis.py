"""Fault-propagation knowledge: which subsystem an event comes from, and which subsystems can cause a warning.

Root-cause analysis must not blame whichever fault happens to come first before a warning — "the earliest fault in
the window" picks unrelated faults (lost CAN communication, an infotainment watchdog reset) most of the time, see
``edgecar.bench.rootcause``. It only considers faults from subsystems that can physically lead to the warning *on
this vehicle*. A battery over-temperature on a refrigerant-cooled pack (BMW i3) can come from the refrigerant
circuit, battery cooling, the cells or the thermal manager — not from the drive electronics' coolant loop, the
network or the infotainment system.

This is configuration, not training: a new vehicle brings its DTC catalogue and its cooling architecture.
"""

from __future__ import annotations

DTC_SUBSYSTEM = {
    "P0534": "refrigerant",  # A/C refrigerant charge loss
    "P0C32": "battery_cooling",  # hybrid/EV battery pack cooling system performance
    "P0C4A": "battery_coolant",  # hybrid/EV battery pack coolant pump control performance
    "P0A7E": "battery",  # hybrid/EV battery pack over temperature
    "P0A7F": "battery",  # hybrid/EV battery pack deterioration
    "P0A93": "drive_cooling",  # inverter "A" cooling system performance
    "P0C73": "drive_cooling",  # motor electronics coolant pump "A" control performance
    "P0562": "low_voltage",  # system voltage low (12 V)
    "U0121": "network",  # lost communication with the ABS module
    "U0155": "network",  # lost communication with the instrument cluster
}
PROCESS_SUBSYSTEM = {  # QNX processes whose log lines become system events
    "thermal_mgr": "thermal_management",
    "bms_thermal": "battery",
    "media_svc": "infotainment",
    "nav_gnss": "infotainment",
    "hmi_svc": "infotainment",
}

# What can raise the battery's temperature, by how the pack is cooled.
BATTERY_HEAT_CAUSES = {
    "refrigerant": {"refrigerant", "battery_cooling", "battery", "thermal_management"},  # A/C circuit, e.g. BMW i3
    "liquid": {"battery_coolant", "battery_cooling", "refrigerant", "battery", "thermal_management"},  # loop + chiller
    "passive": {"battery"},  # no active cooling, e.g. early Nissan Leaf
}
BATTERY_HEAT_ANCHORS = {"BATT_OVERTEMP_WARNING", "BATT_OVERTEMP_FLAG", "BATT_TEMP_HIGH", "P0A7E"}
# The event that marks when the symptom began: a cause must come before it.
SYMPTOM_ONSET = {code: "BATT_TEMP_RISE_RATE" for code in BATTERY_HEAT_ANCHORS}


def subsystem(code: str) -> str | None:
    if code.startswith("QNX_"):
        return PROCESS_SUBSYSTEM.get(code[4:].lower())
    return DTC_SUBSYSTEM.get(code)


def possible_causes(anchor_code: str, battery_cooling: str | None) -> set[str] | None:
    """Subsystems that can lead to the anchor event on this vehicle; ``None`` when there is no such knowledge."""
    if anchor_code not in BATTERY_HEAT_ANCHORS:
        return None
    if battery_cooling in BATTERY_HEAT_CAUSES:
        return BATTERY_HEAT_CAUSES[battery_cooling]
    return set().union(*BATTERY_HEAT_CAUSES.values())  # architecture unknown: anything that can heat a pack
