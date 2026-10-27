from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from edgecar.ingest import adapters
from edgecar.protocols import slog2, someip, uds, vhal
from edgecar.protocols.can import (
    I3_FD_DBC_FILES,
    I3_FD_SIGNALS,
    REFERENCE_DBC_DIR,
    CanCodec,
    read_log,
    write_log,
)
from edgecar.sim import adas, thermal

# ------------------------------------------------------------------------------------------------- UDS


@pytest.mark.parametrize(
    ("code", "raw"),
    [("P0C73", "0c7300"), ("P0A7E", "0a7e00"), ("U0100", "c10000"), ("C0750", "475000"), ("B1234", "923400")],
)
def test_dtc_encoding_follows_sae_j2012(code, raw):
    assert uds.dtc_to_bytes(code).hex() == raw
    assert uds.bytes_to_dtc(bytes.fromhex(raw)) == (code, 0)


def test_status_byte_flags():
    assert uds.status_flags(0x2F) == [
        "testFailed",
        "testFailedThisOperationCycle",
        "pendingDTC",
        "confirmedDTC",
        "testFailedSinceLastClear",
    ]


@pytest.mark.parametrize("n_dtcs", [1, 2, 5])
def test_report_survives_isotp_segmentation(n_dtcs):
    dtcs = [uds.DtcRecord(c, 0, 0x2F) for c in ["P0C73", "P0A93", "P0A7E", "U0100", "C0750"][:n_dtcs]]
    frames = uds.isotp_frames(uds.encode_report(dtcs))
    assert all(len(f) == 8 for f in frames)
    assert len(frames) == (1 if n_dtcs == 1 else 1 + -(-(3 + 4 * n_dtcs - 6) // 7))
    r = uds.IsoTpReassembler()
    out = [p for f in frames if (p := r.feed(f)) is not None]
    assert uds.decode_report(out[0]) == dtcs


def test_isotp_drops_a_message_with_a_missing_frame():
    frames = uds.isotp_frames(uds.encode_report([uds.DtcRecord("P0C73", 0, 0x2F)] * 4))
    r = uds.IsoTpReassembler()
    assert [r.feed(f) for f in frames[:1] + frames[2:]] == [None] * (len(frames) - 1)


# ----------------------------------------------------------------------------------------------- slog2


def test_slog2_round_trip_and_rejects_garbage():
    rec = slog2.LogRecord(
        datetime(2019, 7, 24, 13, 11, 0, 412000), "thermal_mgr", 412, "pump_a", 3, "flow=0.0 -> FAULT"
    )
    assert slog2.parse_line(slog2.format_record(rec), 2019) == rec
    assert slog2.parse_line("not a log line", 2019) is None


# --------------------------------------------------------------------------------------------- SOME/IP


def test_someip_header_round_trip_and_length_check():
    m = someip.Message(0x1A01, 0x8001, b"\x01\x00\x27\x10\x00\x00\x05", session_id=7)
    data = someip.encode(m)
    assert data[:8].hex() == "1a0180010000000f"  # service, event, length = 8 + payload
    assert someip.decode(data) == m
    with pytest.raises(ValueError):
        someip.decode(data[:-1])


def test_pcap_round_trip(tmp_path):
    msgs = [(1563973861.5 + i, someip.encode(someip.Message(0x2B01, 0x8001, bytes([i])))) for i in range(3)]
    path = tmp_path / "x.pcap"
    assert someip.write_pcap(msgs, path) == 3
    back = list(someip.read_pcap(path))
    assert [d for _, d in back] == [d for _, d in msgs]
    assert back[0][0] == pytest.approx(1563973861.5)


# ------------------------------------------------------------------------------------------------ VHAL


def test_vhal_property_ids_match_aosp_composition():
    system, global_area, float_t, int32_t = 0x10000000, 0x01000000, 0x00600000, 0x00400000
    assert 0x0207 + system + global_area + float_t == vhal.PERF_VEHICLE_SPEED
    assert 0x0703 + system + global_area + float_t == vhal.ENV_OUTSIDE_TEMPERATURE
    assert 0x1011 + system + global_area + int32_t == vhal.CRUISE_CONTROL_STATE
    assert 0x1009 + system + global_area + int32_t == vhal.LANE_KEEP_ASSIST_STATE


def test_vhal_json_round_trip_and_unit_conversion():
    v = vhal.PropValue(1_563_973_500_000_000_000, vhal.PERF_VEHICLE_SPEED, 25.0)
    assert vhal.PropValue.from_json(v.to_json()) == v
    df = adapters.vhal_frames([v])
    assert df["speed_kmh"].iloc[0] == pytest.approx(90.0)  # 25 m/s


# ---------------------------------------------------------------------------------------------- CAN FD


def test_canfd_round_trip_keeps_enums_flags_and_gaps(tmp_path):
    codec = CanCodec(REFERENCE_DBC_DIR, I3_FD_DBC_FILES, I3_FD_SIGNALS)
    src = pd.DataFrame(
        {
            "ts": pd.date_range("2019-07-24 13:00", periods=4, freq="s"),
            "batt_voltage_v": [371.8, 360.0, np.nan, 355.5],
            "batt_current_a": [-15.8, 120.4, 80.0, 295.0],
            "soc_pct": [65.0, 64.9, 64.8, 64.7],
            "batt_temp_max_c": [33.0, 41.5, 50.0, 55.5],
            "thermal_warning": [0, 1, 1, 3],
            "regen_active": [0, 1, 0, 1],
        }
    )
    log = tmp_path / "fd.log"
    write_log(codec.encode(src), log)
    assert "##0" in log.read_text().splitlines()[0]  # CAN FD line format
    out = codec.decode(read_log(log))
    assert list(out["thermal_warning"]) == [0, 1, 1, 3]  # 3 = OverTemp, not "not available"
    assert list(out["regen_active"]) == [0, 1, 0, 1]
    assert np.isnan(out["batt_voltage_v"].iloc[2])  # a gap stays a gap
    assert out["batt_current_a"].tolist() == pytest.approx([-15.8, 120.4, 80.0, 295.0], abs=0.025)


# ------------------------------------------------------------------------------------- end-to-end scenario


def test_adas_change_survives_someip(tmp_path):
    c = adas.AdasChange(pd.Timestamp("2018-01-07 08:00:01.250"), adas.DISENGAGED, "takeover_late", 101.5, 1830, -4)
    path = tmp_path / "adas.pcap"
    adas.to_pcap([c], path)
    ev = adapters.someip_events(someip.read_pcap(path), "leaf-455", "sim:test")
    assert ev["code"].tolist() == ["ADAS_DISENGAGE_TAKEOVER_LATE"]
    assert ev["severity"].iloc[0] == "critical"
    assert "1.83 s" in ev["title"].iloc[0]


def test_overheat_scenario_is_fully_recovered_from_the_wire(tmp_path):
    n = 1800
    base = pd.DataFrame(
        {
            "ts": pd.date_range("2019-07-01 13:00", periods=n, freq="s"),
            "trip_id": thermal.BASE_TRIP,
            "batt_current_a": np.full(n, 60.0),
            "batt_temp_max_c": np.full(n, 31.0),
            "batt_temp_c": np.full(n, 30.0),
            "speed_kmh": np.full(n, 100.0),
            "ambient_temp_c": np.full(n, 30.0),
        }
    )
    sc = thermal.battery_overheat(base)
    codec = CanCodec(REFERENCE_DBC_DIR, I3_FD_DBC_FILES, I3_FD_SIGNALS)
    write_log(codec.encode(sc.frames), tmp_path / "fd.log")
    write_log(sc.diag_frames, tmp_path / "diag.log")
    someip.write_pcap(sc.someip_records, tmp_path / "s.pcap")
    year = thermal.START.year
    events = pd.concat(
        [
            adapters.slog2_events([r for x in sc.slog2_lines if (r := slog2.parse_line(x, year))], "i3", "t"),
            adapters.uds_events(read_log(tmp_path / "diag.log"), "i3", "t"),
            adapters.someip_events(someip.read_pcap(tmp_path / "s.pcap"), "i3", "t"),
            adapters.vhal_events(sc.vhal_values, "i3", "t"),
            adapters.canfd_thermal_events(codec.decode(read_log(tmp_path / "fd.log")), "i3", "t"),
        ]
    )
    assert set(events["code"]) == {code for _, code in sc.truth}
    assert len(events) == len(sc.truth)  # nothing duplicated (P0C73 is reported twice on the wire, once as an event)


def test_out_of_range_values_saturate_instead_of_failing():
    codec = CanCodec(REFERENCE_DBC_DIR, I3_FD_DBC_FILES, I3_FD_SIGNALS)
    src = pd.DataFrame({"ts": pd.date_range("2019-07-24", periods=2, freq="s"), "batt_temp_max_c": [120.0, -60.0]})
    out = codec.decode(codec.encode(src))
    # CellTempMax is 8 bits at 0.5 °C from −40 °C; the top raw value is reserved for "not available"
    assert out["batt_temp_max_c"].tolist() == [87.0, -40.0]
