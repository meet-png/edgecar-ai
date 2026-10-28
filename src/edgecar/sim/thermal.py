"""Fault scenario: loss of battery cooling + a degrading cell group → battery overheating warning.

A scenario is a *derived trip*: a real trip's signals replayed on a new date with a failure applied on top. It is
stored as its own trip with ``sim:scenario`` provenance and is excluded from energy statistics, so it never
distorts the real data.

The fault matches the vehicle. The BMW i3 cools its high-voltage battery **directly with refrigerant** from the A/C
circuit (no liquid loop), so the failure that removes its active cooling is a refrigerant charge loss: the low-side
pressure falls below the compressor's minimum, the electric compressor is locked out, and both cabin A/C and battery
cooling stop. (A coolant-pump fault would not explain it — on the i3 the pump serves the drive electronics.)

Physics. Once cooling is lost, the hottest cell group heats by Joule losses on the trip's **real** current draw and
loses heat only passively::

    dT/dt = I² · R_fault / (m·c)  −  (T − T_base) / τ

On the real trip alone the hottest sensor never exceeds 33 °C, and losing cooling with a healthy cell group
(R ≈ 0.02–0.04 Ω) would only reach 35–38 °C — not enough to warn. The scenario therefore models a cell group whose
resistance has risen to 0.15 Ω (an ageing or internally damaged group, a known precursor of thermal events).

The simulator emits only **wire traffic**, each item on the protocol that would carry it in a real vehicle:

=====================  ==========================================================================================
QNX ``slog2``          thermal manager logs the compressor lock-out (and routine INFO lines the adapter filters out)
UDS over ISO-TP        the gateway's own ``ReadDTCInformation`` polls, decoded passively: VCU reports P0534; BMS
                       reports P0C32 (pending), later P0C32 + P0A7E (multi-frame)
CAN FD 0x3A0           ``BMS_Status_FD.ThermalWarning`` rises to 1 (fast rise) and 3 (over-temperature)
SOME/IP 0x2B01         thermal management announces a drive-power limit
VHAL 0x21400101        vendor property: overheating warning shown on the cluster
=====================  ==========================================================================================

Diagnostic codes are SAE J2012: P0534 (A/C refrigerant charge loss), P0C32 (hybrid/EV battery pack cooling system
performance), P0A7E (hybrid/EV battery pack over temperature).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from edgecar.protocols import someip, uds, vhal
from edgecar.protocols.can import Frame
from edgecar.protocols.slog2 import LogRecord, format_record

BASE_TRIP = "i3-tum:A07"  # real summer trip: 44 km, 33.5 °C outside, 1 Jul 2019
SCENARIO_TRIP = "i3-tum:S01"
START = pd.Timestamp("2019-07-24 13:05:00")
FAIL_AFTER_S = 360
R_FAULT_OHM = 0.15
MC_J_PER_K = 20_000.0  # thermal mass of the affected cell group
PASSIVE_TAU_S = 1_800.0
RISE_RATE_C_PER_MIN = 0.8
DERATE_C, WARN_C = 50.0, 55.0
POWER_LIMIT_KW = 40
PROVENANCE = "sim:scenario/battery_overheat (base i3-tum:A07)"

STATUS_CONFIRMED = 0x2F  # testFailed | thisCycle | pending | confirmed | sinceLastClear
STATUS_PENDING = 0x07  # testFailed | thisCycle | pending
ACTIVE_TAU_S = 400.0  # cooling still working: heat leaves the cell group faster
HEALTHY_GROUP_OHM = 0.035


@dataclass(frozen=True)
class Fault:
    """What fails, when, and how hard. The defaults are the demo scenario in the store.

    ``refrigerant_loss``  refrigerant charge lost → compressor locked out → no battery cooling (P0534, then P0C32)
    ``valve_stuck``       battery refrigerant valve stuck closed → no battery cooling, cabin A/C still works (no DTC
                          for the valve itself; the thermal manager logs it, the BMS later sets P0C32)
    ``cell_degradation``  a cell group's resistance jumps (internal damage); cooling works but cannot keep up (P0A7F)
    """

    kind: str = "refrigerant_loss"
    after_s: int = FAIL_AFTER_S
    r_ohm: float = R_FAULT_OHM
    cell_group: int = 7


KINDS = ("refrigerant_loss", "valve_stuck", "cell_degradation")
DEMO_FAULT = Fault()


@dataclass
class ScenarioTraffic:
    frames: pd.DataFrame  # i3 signals (+ thermal_warning) → CAN FD and VHAL
    slog2_lines: list[str] = field(default_factory=list)
    diag_frames: list[Frame] = field(default_factory=list)
    someip_records: list[tuple[float, bytes]] = field(default_factory=list)
    vhal_values: list[vhal.PropValue] = field(default_factory=list)
    truth: list[tuple[pd.Timestamp, str]] = field(default_factory=list)  # ground-truth (time, event code)
    fault_time: pd.Timestamp | None = None  # when the root cause happened
    fault_dtc: str | None = None  # the diagnostic code that names it, if the fault has one


def _posix(ts: pd.Timestamp) -> float:
    return ts.value / 1e9


def _uds_poll(at: pd.Timestamp, ecu: str, dtcs: list[uds.DtcRecord]) -> list[Frame]:
    """The gateway's ReadDTCInformation request and the ECU's (possibly multi-frame) response."""
    req_id, resp_id = uds.ECU_ADDRESSES[ecu]
    t = _posix(at)
    request = bytes([0x03, uds.SID_READ_DTC, uds.SUB_BY_STATUS_MASK, 0xFF]) + b"\xaa" * 4
    out = [Frame(t, "diag", req_id, request)]
    response = uds.isotp_frames(uds.encode_report(dtcs))
    out.append(Frame(t + 0.010, "diag", resp_id, response[0]))
    if len(response) > 1:  # multi-frame: the requester grants flow control, then consecutive frames follow
        out.append(Frame(t + 0.012, "diag", req_id, uds.FLOW_CONTROL_CTS))
        out += [Frame(t + 0.014 + 0.001 * k, "diag", resp_id, f) for k, f in enumerate(response[1:])]
    return out


def _log(at: pd.Timestamp, process: str, pid: int, buffer: str, severity: int, message: str) -> str:
    return format_record(LogRecord(at.to_pydatetime(), process, pid, buffer, severity, message))


def battery_overheat(
    base: pd.DataFrame, fault: Fault = DEMO_FAULT, trip_id: str = SCENARIO_TRIP, start: pd.Timestamp = START
) -> ScenarioTraffic:
    """Build a scenario from a real trip's canonical 1 Hz frames. Raises ``ValueError`` if, on this trip, the fault
    never heats the battery to the warning threshold (the caller then picks another trip or fault)."""
    base = base.sort_values("ts").reset_index(drop=True)
    f = base.copy()
    f["ts"] = f["ts"] + (start - f["ts"].iloc[0])
    f["trip_id"] = trip_id
    k_fail = fault.after_s
    if k_fail >= len(f) - 120:
        raise ValueError("the trip ends too soon after the fault")

    i2 = f["batt_current_a"].fillna(0).to_numpy() ** 2
    t_base = base["batt_temp_max_c"].ffill().bfill().to_numpy()
    tau = ACTIVE_TAU_S if fault.kind == "cell_degradation" else PASSIVE_TAU_S  # is the pack still being cooled?
    temp = np.empty(len(f))
    temp[0] = t_base[0]
    for k in range(1, len(f)):
        heat = i2[k] * fault.r_ohm / MC_J_PER_K if k >= k_fail else 0.0
        temp[k] = temp[k - 1] + heat - (temp[k - 1] - t_base[k]) / tau
    f["batt_temp_max_c"] = temp
    f["batt_temp_c"] = base["batt_temp_c"].to_numpy() + 0.25 * (temp - t_base)  # one hot group lifts the average
    if fault.kind == "refrigerant_loss" and "hvac_ac_kw" in f:
        f.loc[k_fail:, "hvac_ac_kw"] = 0.0  # the compressor is locked out: no cabin A/C either

    rate = pd.Series(temp).diff(60).to_numpy()  # °C per minute
    idx = np.arange(len(f))
    rising, derate, warn = (rate >= RISE_RATE_C_PER_MIN) & (idx > k_fail), temp >= DERATE_C, temp >= WARN_C
    if not (rising.any() and derate.any() and warn.any()):
        raise ValueError("the battery never reaches the warning threshold on this trip")
    k_rise, k_derate, k_warn = (int(np.flatnonzero(x)[0]) for x in (rising, derate, warn))
    level = np.zeros(len(f), dtype=int)
    level[k_rise:] = 1
    level[k_warn:] = 3
    f["thermal_warning"] = level

    ts = f["ts"]
    t_fail, t_rise, t_derate, t_warn = ts[k_fail], ts[k_rise], ts[k_derate], ts[k_warn]
    group = fault.cell_group
    tr = ScenarioTraffic(frames=f, fault_time=t_fail)

    # QNX system log: routine refrigerant-circuit telemetry every minute, then the fault and the BMS report
    routine = "p_low=2.9 bar p_high=12.1 bar batt_valve=open"
    for k in range(0, k_fail, 60):
        tr.slog2_lines.append(_log(ts[k], "thermal_mgr", 412, "refrig", 5, routine))
    at_fail = t_fail + pd.Timedelta(milliseconds=412)
    tr.slog2_lines.append(
        {
            "refrigerant_loss": _log(
                at_fail,
                "thermal_mgr",
                412,
                "refrig",
                3,
                "p_low=0.9 bar < 1.5 bar min: compressor locked out, battery cooling unavailable -> FAULT",
            ),
            "valve_stuck": _log(
                at_fail,
                "thermal_mgr",
                412,
                "refrig",
                3,
                "batt_valve cmd=open fb=closed: battery cooling blocked -> FAULT",
            ),
            "cell_degradation": _log(
                at_fail,
                "bms_thermal",
                377,
                "cellgrp",
                3,
                f"cell group {group} resistance {fault.r_ohm * 1000:.0f} mOhm "
                f"({fault.r_ohm / HEALTHY_GROUP_OHM:.1f}x pack median) -> WARN",
            ),
        }[fault.kind]
    )
    tr.slog2_lines += [
        _log(
            t_derate + pd.Timedelta(milliseconds=50),
            "thermal_mgr",
            412,
            "policy",
            4,
            f"derating drive power to {POWER_LIMIT_KW} kW",
        ),
        _log(
            t_warn + pd.Timedelta(milliseconds=100),
            "bms_thermal",
            377,
            "cellgrp",
            1,
            f"cell group {group} over temperature {temp[k_warn]:.1f} C",
        ),
    ]
    # UDS: the gateway polls after each new fault indication (EdgeCar only listens to these exchanges)
    confirmed = [uds.DtcRecord("P0A7E", 0x00, STATUS_CONFIRMED)]
    if fault.kind == "refrigerant_loss":
        tr.fault_dtc = "P0534"
        tr.diag_frames += _uds_poll(
            t_fail + pd.Timedelta(seconds=1), "VCU", [uds.DtcRecord("P0534", 0, STATUS_CONFIRMED)]
        )
    if fault.kind == "cell_degradation":
        tr.fault_dtc = "P0A7F"
        tr.diag_frames += _uds_poll(
            t_fail + pd.Timedelta(seconds=1), "BMS", [uds.DtcRecord("P0A7F", 0, STATUS_CONFIRMED)]
        )
        confirmed.insert(0, uds.DtcRecord("P0A7F", 0x00, STATUS_CONFIRMED))
    else:  # cooling lost: the BMS sees the pack warm up despite its cooling request
        tr.diag_frames += _uds_poll(
            t_rise + pd.Timedelta(seconds=1), "BMS", [uds.DtcRecord("P0C32", 0, STATUS_PENDING)]
        )
        confirmed.insert(0, uds.DtcRecord("P0C32", 0x00, STATUS_CONFIRMED))
    if fault.kind == "refrigerant_loss":
        tr.diag_frames += _uds_poll(
            t_warn + pd.Timedelta(seconds=1), "VCU", [uds.DtcRecord("P0534", 0, STATUS_CONFIRMED)]
        )
    tr.diag_frames += _uds_poll(t_warn + pd.Timedelta(seconds=1.2), "BMS", confirmed)
    # SOME/IP: thermal management announces the power limit
    payload = someip.THERMAL_PAYLOAD.pack(POWER_LIMIT_KW, round(temp[k_derate] * 10), 1)
    tr.someip_records.append(
        (_posix(t_derate), someip.encode(someip.Message(someip.THERMAL_SERVICE, someip.THERMAL_LIMIT_EVENT, payload)))
    )
    # VHAL: the cluster shows the warning
    tr.vhal_values.append(
        vhal.PropValue((t_warn + pd.Timedelta(milliseconds=200)).value, vhal.VENDOR_BATTERY_THERMAL_WARNING, 1)
    )

    first = {
        "refrigerant_loss": "QNX_THERMAL_MGR",
        "valve_stuck": "QNX_THERMAL_MGR",
        "cell_degradation": "QNX_BMS_THERMAL",
    }
    truth = [
        (t_fail, first[fault.kind]),
        (t_rise, "BATT_TEMP_RISE_RATE"),
        (t_derate, "BATT_POWER_DERATE"),
        (t_warn, "BATT_OVERTEMP_FLAG"),
        (t_warn, "QNX_BMS_THERMAL"),
        (t_warn, "BATT_OVERTEMP_WARNING"),
        (t_warn, "P0A7E"),
    ]
    if tr.fault_dtc:
        truth.append((t_fail, tr.fault_dtc))
    if fault.kind != "cell_degradation":
        truth.append((t_rise, "P0C32"))
    tr.truth = sorted(truth)
    return tr
