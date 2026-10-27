"""Fault scenario: coolant-pump failure + a degrading cell group → battery overheating warning.

A scenario is a *derived trip*: a real trip's signals replayed on a new date with a failure applied on top. It is
stored as its own trip with ``sim:scenario`` provenance and is excluded from energy statistics, so it never
distorts the real data.

Physics. After the pump fails, the hottest cell group heats by Joule losses on the trip's **real** current draw and
loses heat only passively::

    dT/dt = I² · R_fault / (m·c)  −  (T − T_base) / τ

On the real trip alone the hottest sensor never exceeds 33 °C, and a pump failure with a healthy cell group
(R ≈ 0.02–0.04 Ω) would only reach 35–38 °C — not enough to warn. The scenario therefore models a cell group whose
resistance has risen to 0.15 Ω (an ageing or internally damaged group, a known precursor of thermal events).

The simulator emits only **wire traffic**, each item on the protocol that would carry it in a real vehicle:

=====================  ==========================================================================================
QNX ``slog2``          thermal manager logs the pump fault (and routine INFO lines the adapter filters out)
UDS over ISO-TP        gateway polls ``ReadDTCInformation``; VCU reports P0C73, later P0C73 + P0A93 (multi-frame);
                       BMS reports P0A7E
CAN FD 0x3A0           ``BMS_Status_FD.ThermalWarning`` rises to 1 (fast rise) and 3 (over-temperature)
SOME/IP 0x2B01         thermal management announces a drive-power limit
VHAL 0x21400101        vendor property: overheating warning shown on the cluster
=====================  ==========================================================================================

Diagnostic codes are SAE J2012: P0C73 (motor-electronics coolant pump "A" control), P0A93 (inverter "A" cooling
system performance — the inverter shares the coolant loop), P0A7E (hybrid/EV battery pack "A" over temperature).
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


@dataclass
class ScenarioTraffic:
    frames: pd.DataFrame  # i3 signals (+ thermal_warning) → CAN FD and VHAL
    slog2_lines: list[str] = field(default_factory=list)
    diag_frames: list[Frame] = field(default_factory=list)
    someip_records: list[tuple[float, bytes]] = field(default_factory=list)
    vhal_values: list[vhal.PropValue] = field(default_factory=list)
    truth: list[tuple[pd.Timestamp, str]] = field(default_factory=list)  # ground-truth (time, event code)


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


def battery_overheat(base: pd.DataFrame) -> ScenarioTraffic:
    """Build the scenario from the base trip's canonical 1 Hz frames."""
    base = base.sort_values("ts").reset_index(drop=True)
    f = base.copy()
    f["ts"] = f["ts"] + (START - f["ts"].iloc[0])
    f["trip_id"] = SCENARIO_TRIP

    i2 = f["batt_current_a"].fillna(0).to_numpy() ** 2
    t_base = base["batt_temp_max_c"].ffill().to_numpy()
    temp = np.empty(len(f))
    temp[0] = t_base[0]
    for k in range(1, len(f)):
        heat = i2[k] * R_FAULT_OHM / MC_J_PER_K if k >= FAIL_AFTER_S else 0.0
        temp[k] = temp[k - 1] + heat - (temp[k - 1] - t_base[k]) / PASSIVE_TAU_S
    f["batt_temp_max_c"] = temp
    f["batt_temp_c"] = base["batt_temp_c"].to_numpy() + 0.25 * (temp - t_base)  # one hot group lifts the average

    rate = pd.Series(temp).diff(60).to_numpy()  # °C per minute
    idx = np.arange(len(f))
    k_rise = int(np.flatnonzero((rate >= RISE_RATE_C_PER_MIN) & (idx > FAIL_AFTER_S))[0])
    k_derate = int(np.flatnonzero(temp >= DERATE_C)[0])
    k_warn = int(np.flatnonzero(temp >= WARN_C)[0])
    level = np.zeros(len(f), dtype=int)
    level[k_rise:] = 1
    level[k_warn:] = 3
    f["thermal_warning"] = level

    ts = f["ts"]
    t_fail, t_derate, t_warn = ts[FAIL_AFTER_S], ts[k_derate], ts[k_warn]
    tr = ScenarioTraffic(frames=f)

    # QNX system log: routine pump telemetry every minute, then the fault and the BMS over-temperature report
    for k in range(0, FAIL_AFTER_S, 60):
        tr.slog2_lines.append(
            format_record(LogRecord(ts[k].to_pydatetime(), "thermal_mgr", 412, "pump_a", 5, "duty=58% flow=11.8 l/min"))
        )
    tr.slog2_lines += [
        format_record(
            LogRecord(
                (t_fail + pd.Timedelta(milliseconds=412)).to_pydatetime(),
                "thermal_mgr",
                412,
                "pump_a",
                3,
                "pwm_fb=0 duty=62% flow=0.0 l/min -> FAULT",
            )
        ),
        format_record(
            LogRecord(
                (t_derate + pd.Timedelta(milliseconds=50)).to_pydatetime(),
                "thermal_mgr",
                412,
                "policy",
                4,
                f"derating drive power to {POWER_LIMIT_KW} kW",
            )
        ),
        format_record(
            LogRecord(
                (t_warn + pd.Timedelta(milliseconds=100)).to_pydatetime(),
                "bms_thermal",
                377,
                "cellgrp",
                1,
                f"cell group 7 over temperature {temp[k_warn]:.1f} C",
            )
        ),
    ]
    # UDS: the gateway polls after each new fault indication
    tr.diag_frames += _uds_poll(
        t_fail + pd.Timedelta(seconds=1), "VCU", [uds.DtcRecord("P0C73", 0x00, STATUS_CONFIRMED)]
    )
    tr.diag_frames += _uds_poll(
        t_warn + pd.Timedelta(seconds=1),
        "VCU",
        [uds.DtcRecord("P0C73", 0x00, STATUS_CONFIRMED), uds.DtcRecord("P0A93", 0x00, STATUS_PENDING)],
    )
    tr.diag_frames += _uds_poll(
        t_warn + pd.Timedelta(seconds=1.2), "BMS", [uds.DtcRecord("P0A7E", 0x00, STATUS_CONFIRMED)]
    )
    # SOME/IP: thermal management announces the power limit
    payload = someip.THERMAL_PAYLOAD.pack(POWER_LIMIT_KW, round(temp[k_derate] * 10), 1)
    tr.someip_records.append(
        (_posix(t_derate), someip.encode(someip.Message(someip.THERMAL_SERVICE, someip.THERMAL_LIMIT_EVENT, payload)))
    )
    # VHAL: the cluster shows the warning
    tr.vhal_values.append(
        vhal.PropValue((t_warn + pd.Timedelta(milliseconds=200)).value, vhal.VENDOR_BATTERY_THERMAL_WARNING, 1)
    )

    tr.truth = sorted(
        [
            (t_fail, "QNX_THERMAL_MGR"),
            (t_fail, "P0C73"),
            (ts[k_rise], "BATT_TEMP_RISE_RATE"),
            (t_derate, "BATT_POWER_DERATE"),
            (t_warn, "BATT_OVERTEMP_FLAG"),
            (t_warn, "QNX_BMS_THERMAL"),
            (t_warn, "BATT_OVERTEMP_WARNING"),
            (t_warn, "P0A93"),
            (t_warn, "P0A7E"),
        ]
    )
    return tr
