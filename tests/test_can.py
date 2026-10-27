from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from edgecar.protocols.can import LEAF_SIGNALS, CanCodec, read_log, segment_trips, write_log

DBC_DIR = Path(__file__).resolve().parents[1] / "data" / "raw" / "leaf_dbc"
pytestmark = pytest.mark.skipif(not DBC_DIR.exists(), reason="Leaf DBC files not fetched (GPL-3.0, not vendored)")

# half of each signal's resolution: the most a lossless round trip may differ by
HALF_LSB = {
    "speed_kmh": 0.005,
    "batt_current_a": 0.25,
    "batt_voltage_v": 0.25,
    "soc_pct": 0.05,
    "ambient_temp_c": 0.25,
    "hvac_heater_kw": 0.125,
    "hvac_ac_kw": 0.025,
}


def frames(seconds: int, start: str) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    return pd.DataFrame(
        {
            "ts": pd.date_range(start, periods=seconds, freq="s"),
            "speed_kmh": rng.uniform(0, 130, seconds),
            "batt_current_a": rng.uniform(-120, 290, seconds),
            "batt_voltage_v": rng.uniform(330, 395, seconds),
            "soc_pct": rng.uniform(5, 100, seconds),
            "ambient_temp_c": rng.uniform(-20, 40, seconds),
            "hvac_heater_kw": rng.uniform(0, 4.5, seconds),
            "hvac_ac_kw": rng.uniform(0, 3, seconds),
        }
    )


def test_round_trip_is_within_half_a_bit(tmp_path):
    codec = CanCodec(DBC_DIR)
    src = frames(300, "2018-01-07 08:00:00")
    log = tmp_path / "trip.log"
    n = write_log(codec.encode(src), log)
    assert n == 300 * 5  # five Leaf messages per second carry the seven signals
    out = codec.decode(read_log(log)).merge(src, on="ts", suffixes=("", "_src"))
    assert len(out) == 300
    for m in LEAF_SIGNALS:
        err = (out[m.canonical] - out[f"{m.canonical}_src"]).abs().max()
        assert err <= HALF_LSB[m.canonical] + 1e-9, m.canonical


def test_discharge_is_positive_after_decoding(tmp_path):
    codec = CanCodec(DBC_DIR)
    src = frames(3, "2018-01-07 08:00:00").assign(batt_current_a=[100.0, -50.0, 0.0])
    out = codec.decode(codec.encode(src))
    assert list(out["batt_current_a"]) == [100.0, -50.0, 0.0]
    assert out["batt_power_kw"].iloc[0] > 0


def test_unknown_frames_are_ignored():
    codec = CanCodec(DBC_DIR)
    from edgecar.protocols.can import Frame

    assert codec.decode([Frame(0.0, "evcan", 0x7DF, bytes(8))]).empty


def test_trips_are_segmented_by_bus_silence():
    a = frames(60, "2018-01-07 08:00:00")[["ts"]]
    b = frames(60, "2018-01-07 08:10:00")[["ts"]]  # ten minutes of silence in between
    out = segment_trips(pd.concat([a, b], ignore_index=True), "leaf-test")
    assert out["trip_id"].nunique() == 2
    assert out["trip_id"].iloc[0] == "leaf-test:20180107-080000"
