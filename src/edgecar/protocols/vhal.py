"""Android Automotive Vehicle HAL property events.

Records mirror AOSP's ``VehiclePropValue`` (``timestamp`` in ns, ``prop``, ``areaId``, ``status``, and the value in
``RawPropValues`` fields ``int32Values`` / ``floatValues``), one JSON object per line. Property IDs, units and enum
values are taken from AOSP ``VehicleProperty.aidl`` (verified 2026-10-27):

``id = property index + VehiclePropertyGroup (SYSTEM 0x10000000, VENDOR 0x20000000) + VehicleArea (GLOBAL
0x01000000) + VehiclePropertyType (INT32 0x00400000, FLOAT 0x00600000)``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

PERF_VEHICLE_SPEED = 0x11600207  # FLOAT, m/s
EV_BATTERY_LEVEL = 0x11600309  # FLOAT, Wh
RANGE_REMAINING = 0x11600308  # FLOAT, m
ENV_OUTSIDE_TEMPERATURE = 0x11600703  # FLOAT, °C
LANE_KEEP_ASSIST_STATE = 0x11401009  # INT32, LaneKeepAssistState / ErrorState
CRUISE_CONTROL_STATE = 0x11401011  # INT32, CruiseControlState / ErrorState
# Vendor property (group VENDOR, area GLOBAL, INT32): battery thermal warning shown on the cluster, 0 = none.
VENDOR_BATTERY_THERMAL_WARNING = 0x21400101

NAMES = {
    PERF_VEHICLE_SPEED: "PERF_VEHICLE_SPEED",
    EV_BATTERY_LEVEL: "EV_BATTERY_LEVEL",
    RANGE_REMAINING: "RANGE_REMAINING",
    ENV_OUTSIDE_TEMPERATURE: "ENV_OUTSIDE_TEMPERATURE",
    LANE_KEEP_ASSIST_STATE: "LANE_KEEP_ASSIST_STATE",
    CRUISE_CONTROL_STATE: "CRUISE_CONTROL_STATE",
    VENDOR_BATTERY_THERMAL_WARNING: "VENDOR_BATTERY_THERMAL_WARNING",
}

# CruiseControlState
CC_ENABLED, CC_ACTIVATED, CC_USER_OVERRIDE, CC_SUSPENDED, CC_FORCED_DEACTIVATION_WARNING = 1, 2, 3, 4, 5
# ErrorState
NOT_AVAILABLE_SPEED_LOW, NOT_AVAILABLE_POOR_VISIBILITY = -3, -5


def is_float(prop: int) -> bool:
    return prop & 0x00FF0000 == 0x00600000


@dataclass(frozen=True)
class PropValue:
    timestamp_ns: int
    prop: int
    value: float | int
    area_id: int = 0
    status: int = 0  # VehiclePropertyStatus.AVAILABLE

    def to_json(self) -> str:
        key = "floatValues" if is_float(self.prop) else "int32Values"
        return json.dumps(
            {
                "timestamp": self.timestamp_ns,
                "prop": self.prop,
                "areaId": self.area_id,
                "status": self.status,
                "value": {key: [self.value]},
            },
            separators=(",", ":"),
        )

    @staticmethod
    def from_json(line: str) -> PropValue:
        d = json.loads(line)
        v = d["value"]
        value = v["floatValues"][0] if "floatValues" in v else v["int32Values"][0]
        return PropValue(d["timestamp"], d["prop"], value, d.get("areaId", 0), d.get("status", 0))


def write_jsonl(values: Iterable[PropValue], path: Path) -> int:
    n = 0
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for v in sorted(values, key=lambda v: (v.timestamp_ns, v.prop)):
            fh.write(v.to_json() + "\n")
            n += 1
    return n


def read_jsonl(path: Path) -> Iterator[PropValue]:
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield PropValue.from_json(line)
