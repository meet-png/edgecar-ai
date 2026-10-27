"""Protocol adapters: turn decoded wire traffic into events.

Each adapter owns its event vocabulary — titles, severities and categories are produced here from decoded fields,
exactly as they would be from a live vehicle. Simulators only ever produce wire traffic.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable

import pandas as pd

from edgecar.protocols import someip, uds, vhal
from edgecar.protocols.can import Frame
from edgecar.protocols.slog2 import SEVERITY, LogRecord
from edgecar.store import Store

EVENT_COLUMNS = [
    "event_id",
    "ts",
    "vehicle_id",
    "trip_id",
    "category",
    "code",
    "severity",
    "source",
    "title",
    "detail",
    "provenance",
]

# ---------------------------------------------------------------------------------------------------- ADAS

ADAS_STATES = {0: "disengaged", 1: "engaged", 2: "takeover_request"}
ADAS_CAUSES = (
    "none",
    "driver_brake",
    "driver_steer",
    "standstill_hold",
    "lane_markings_lost",
    "sensor_blocked",
    "takeover_completed",
    "takeover_late",
    "system_limit",
)
CRITICAL_SITUATIONS = ("front_vehicle", "left_pedestrian", "right_pedestrian")
NO_AMBIENT = -128


def _event(ts, vehicle_id, code, category, severity, source, title, detail, provenance) -> dict:
    ts = pd.Timestamp(ts)
    return {
        "event_id": f"{vehicle_id}:{code}:{ts.value}",
        "ts": ts,
        "vehicle_id": vehicle_id,
        "trip_id": None,
        "category": category,
        "code": code,
        "severity": severity,
        "source": source,
        "title": title,
        "detail": json.dumps(detail),
        "provenance": provenance,
    }


def _adas_event(ts, vehicle_id, state, cause, speed, value, ambient, provenance) -> dict:
    src = "SOME/IP 0x1A01 (ADAS)"
    detail = {"cause": cause, "speed_kmh": round(speed, 2)}
    if ambient != NO_AMBIENT:
        detail["ambient_c"] = ambient
    if state == 1:
        return _event(
            ts,
            vehicle_id,
            "ADAS_ENGAGED",
            "adas",
            "info",
            src,
            f"Adaptive cruise + lane keeping engaged at {speed:.0f} km/h",
            detail,
            provenance,
        )
    if state == 2:
        situation = CRITICAL_SITUATIONS[value] if value < len(CRITICAL_SITUATIONS) else "unknown"
        detail["situation"] = situation
        return _event(
            ts,
            vehicle_id,
            "ADAS_TAKEOVER_REQUEST",
            "adas",
            "warning",
            src,
            f"Takeover requested: system limit reached ({situation.replace('_', ' ')})",
            detail,
            provenance,
        )
    table = {
        "driver_brake": ("ADAS_DISENGAGE_DRIVER_BRAKE", "info", f"Disengaged: driver braked (−{value / 100:.1f} m/s²)"),
        "driver_steer": ("ADAS_DISENGAGE_DRIVER_STEER", "info", "Disengaged: driver took over steering"),
        "standstill_hold": ("ADAS_DISENGAGE_STANDSTILL", "info", "Disengaged: standing still for more than 30 s"),
        "lane_markings_lost": ("ADAS_DISENGAGE_LANE_LOST", "warning", "Disengaged: lane markings not detected"),
        "sensor_blocked": (
            "ADAS_DISENGAGE_SENSOR_BLOCKED",
            "warning",
            "Disengaged: front radar/camera blocked (ice or snow)",
        ),
        "takeover_completed": (
            "ADAS_DISENGAGE_TAKEOVER",
            "info",
            f"Driver took over: responded after {value / 1000:.2f} s",
        ),
        "takeover_late": (
            "ADAS_DISENGAGE_TAKEOVER_LATE",
            "critical",
            f"Late takeover: responded after {value / 1000:.2f} s",
        ),
    }
    code, severity, title = table[cause]
    if cause.startswith("takeover"):
        detail["reaction_time_s"] = value / 1000
    if cause == "driver_brake":
        detail["decel_mps2"] = -value / 100
    return _event(ts, vehicle_id, code, "adas", severity, src, title, detail, provenance)


def someip_events(records: Iterable[tuple[float, bytes]], vehicle_id: str, provenance: str) -> pd.DataFrame:
    """Events from SOME/IP notifications (ADAS state service, thermal-management service)."""
    out = []
    for ts_s, data in records:
        msg = someip.decode(data)
        ts = pd.to_datetime(ts_s, unit="s")
        if (msg.service_id, msg.event_id) == (someip.ADAS_SERVICE, someip.ADAS_STATE_EVENT):
            state, cause_ix, speed_c, value, ambient = someip.ADAS_PAYLOAD.unpack(msg.payload)
            out.append(
                _adas_event(ts, vehicle_id, state, ADAS_CAUSES[cause_ix], speed_c / 100, value, ambient, provenance)
            )
        elif (msg.service_id, msg.event_id) == (someip.THERMAL_SERVICE, someip.THERMAL_LIMIT_EVENT):
            limit_kw, cell_dc, _reason = someip.THERMAL_PAYLOAD.unpack(msg.payload)
            out.append(
                _event(
                    ts,
                    vehicle_id,
                    "BATT_POWER_DERATE",
                    "thermal",
                    "warning",
                    "SOME/IP 0x2B01 (thermal)",
                    f"Drive power limited to {limit_kw} kW to protect the battery",
                    {"power_limit_kw": limit_kw, "hottest_cell_c": cell_dc / 10},
                    provenance,
                )
            )
    return pd.DataFrame(out, columns=EVENT_COLUMNS)


# ------------------------------------------------------------------------------------------- diagnostics


def uds_events(frames: Iterable[Frame], vehicle_id: str, provenance: str) -> pd.DataFrame:
    """Events for diagnostic trouble codes the first time each appears in a ReadDTCInformation response."""
    responders = {resp: ecu for ecu, (_req, resp) in uds.ECU_ADDRESSES.items()}
    reassembly = defaultdict(uds.IsoTpReassembler)
    seen: set[tuple[str, str]] = set()
    out = []
    for f in frames:
        ecu = responders.get(f.arbitration_id)
        if f.channel != "diag" or ecu is None:
            continue  # requests from the diagnostic manager are not ours to act on
        payload = reassembly[f.arbitration_id].feed(f.data)
        if payload is None or payload[0] != (uds.SID_READ_DTC | uds.POSITIVE):
            continue
        for rec in uds.decode_report(payload):
            if (ecu, rec.code) in seen:
                continue
            seen.add((ecu, rec.code))
            critical = rec.code in ("P0A7E",)
            text = uds.DTC_TEXT.get(rec.code, "manufacturer-specific code")
            out.append(
                _event(
                    pd.to_datetime(f.ts, unit="s"),
                    vehicle_id,
                    rec.code,
                    "dtc",
                    "critical" if critical else "warning",
                    f"UDS 0x19 ({ecu})",
                    f"DTC {rec.code} — {text}",
                    {
                        "dtc": rec.code,
                        "ecu": ecu,
                        "status": f"0x{rec.status:02X}",
                        "status_flags": uds.status_flags(rec.status),
                    },
                    provenance,
                )
            )
    return pd.DataFrame(out, columns=EVENT_COLUMNS)


def slog2_events(records: Iterable[LogRecord], vehicle_id: str, provenance: str) -> pd.DataFrame:
    """System events from QNX logs at WARNING (3) or more severe."""
    out = []
    for r in records:
        if r.severity > 3:
            continue
        sev = "critical" if r.severity <= 1 else "warning"
        out.append(
            _event(
                r.ts,
                vehicle_id,
                f"QNX_{r.process.upper()}",
                "system",
                sev,
                "QNX slog2",
                f"{r.process}: {r.message}",
                {"pid": r.pid, "buffer": r.buffer, "slog2_severity": SEVERITY[r.severity]},
                provenance,
            )
        )
    return pd.DataFrame(out, columns=EVENT_COLUMNS)


# ------------------------------------------------------------------------------------------- VHAL / CAN FD


def vhal_frames(values: Iterable[vhal.PropValue]) -> pd.DataFrame:
    """Continuous VHAL properties → canonical signals (unit conversions from AOSP units)."""
    rows: dict[int, dict] = defaultdict(dict)
    for v in values:
        if v.prop == vhal.PERF_VEHICLE_SPEED:
            rows[v.timestamp_ns]["speed_kmh"] = v.value * 3.6  # m/s → km/h
        elif v.prop == vhal.ENV_OUTSIDE_TEMPERATURE:
            rows[v.timestamp_ns]["ambient_temp_c"] = v.value
    df = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    df.index = pd.to_datetime(df.index, unit="ns")
    df.index.name = "ts"
    return df.reset_index()


def vhal_events(values: Iterable[vhal.PropValue], vehicle_id: str, provenance: str) -> pd.DataFrame:
    out = []
    for v in values:
        if v.prop == vhal.VENDOR_BATTERY_THERMAL_WARNING and v.value:
            out.append(
                _event(
                    pd.to_datetime(v.timestamp_ns, unit="ns"),
                    vehicle_id,
                    "BATT_OVERTEMP_WARNING",
                    "thermal",
                    "critical",
                    "VHAL 0x21400101 (cluster)",
                    "Battery overheating warning shown to the driver",
                    {"vhal_prop": hex(v.prop), "level": int(v.value)},
                    provenance,
                )
            )
    return pd.DataFrame(out, columns=EVENT_COLUMNS)


def canfd_thermal_events(frames: pd.DataFrame, vehicle_id: str, provenance: str) -> pd.DataFrame:
    """BMS thermal-warning level changes (CAN FD ``BMS_Status_FD.ThermalWarning``) → events on each rise."""
    if "thermal_warning" not in frames:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    level = frames["thermal_warning"].fillna(0).astype(int)
    rising = level > level.shift(fill_value=0)
    labels = {
        1: ("BATT_TEMP_RISE_RATE", "warning", "BMS: battery temperature rising abnormally fast"),
        2: ("BATT_TEMP_HIGH_FLAG", "warning", "BMS: battery temperature high"),
        3: ("BATT_OVERTEMP_FLAG", "critical", "BMS: battery over-temperature flag set"),
    }
    out = []
    for i in rising[rising].index:
        code, sev, title = labels[int(level[i])]
        out.append(
            _event(
                frames.at[i, "ts"],
                vehicle_id,
                code,
                "thermal",
                sev,
                "CAN FD 0x3A0 (BMS)",
                title,
                {"warning_level": int(level[i]), "hottest_cell_c": round(float(frames.at[i, "batt_temp_max_c"]), 1)},
                provenance,
            )
        )
    return pd.DataFrame(out, columns=EVENT_COLUMNS)


# ----------------------------------------------------------------------------------------------- trip ids


def assign_trips(events: pd.DataFrame, store: Store) -> pd.DataFrame:
    """Attach each event to the trip that was running when it happened (None between trips)."""
    if events.empty:
        return events
    store.con.register("_ev", events.drop(columns=["trip_id"]))
    out = store.con.execute(
        """
        SELECT e.*, t.trip_id
        FROM _ev e LEFT JOIN trips t
          ON t.vehicle_id = e.vehicle_id AND e.ts BETWEEN t.start_ts AND t.end_ts + INTERVAL 5 SECOND
        """
    ).df()
    store.con.unregister("_ev")
    return out[EVENT_COLUMNS].drop_duplicates("event_id")
