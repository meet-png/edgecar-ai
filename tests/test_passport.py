import json
import re
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from edgecar import passport
from edgecar.analytics import battery


def drive(trip: str, start: datetime, amps: float, soc_from: float, soc_to: float, seconds: int = 3600) -> pd.DataFrame:
    ts = [start + timedelta(seconds=s) for s in range(seconds)]
    return pd.DataFrame(
        {
            "ts": pd.to_datetime(ts),
            "trip_id": trip,
            "soc_pct": np.linspace(soc_from, soc_to, seconds),
            "batt_current_a": amps,
            "batt_voltage_v": 360.0,
            "batt_temp_c": 20.0,
        }
    )


def test_capacity_is_charge_over_state_of_charge_used():
    # 20 A for an hour moves 20 Ah; 80 % → 50 % is 30 % of the pack, so the pack holds 66.7 Ah
    (s,) = battery.stretches(drive("t1", datetime(2024, 1, 1), 20.0, 80, 50))
    assert s.capacity_ah == pytest.approx(66.67, abs=0.05)


def test_short_drives_are_not_measured():
    assert battery.stretches(drive("t1", datetime(2024, 1, 1), 5.0, 80, 75)) == []


def test_health_trend_and_interval():
    t0 = datetime(2024, 1, 1)
    # a pack losing 5 % a year: 60 Ah at the start, measured on 12 drives over a year
    frames = pd.concat(
        drive(f"t{k}", t0 + timedelta(days=30 * k), 60 * (1 - 0.05 * 30 * k / 365) * 0.3, 80, 50) for k in range(12)
    )
    h = battery.estimate(battery.stretches(frames), rated_ah=60.0)
    assert h.n == 12 and h.soh_pct == pytest.approx(97.5, abs=1.0)
    assert h.trend_pct_per_year == pytest.approx(-5.1, abs=0.5)
    assert h.capacity_ah_ci[0] <= h.capacity_ah <= h.capacity_ah_ci[1]


@pytest.fixture
def signed(store):
    key = passport.Ed25519PrivateKey.generate()
    return passport.sign(passport.build(store, "ev-1"), key), key


def test_a_signed_report_verifies_and_a_changed_one_does_not(signed):
    s, key = signed
    assert passport.verify(s, passport.public_raw(key))
    changed = dict(s, payload=s["payload"].replace('"schema"', '"schemA"'))
    assert not passport.verify(changed, passport.public_raw(key))


def test_another_key_is_not_trusted(signed):
    s, _ = signed
    other = passport.Ed25519PrivateKey.generate()
    resigned = passport.sign(passport.report_of(s), other)
    assert passport.verify(resigned)  # internally consistent …
    assert not passport.verify(resigned, passport.public_raw(signed[1]))  # … but not from this vehicle


def test_the_qr_code_carries_the_whole_signed_report(signed):
    s, key = signed
    text = passport.to_qr_text(s)
    assert text.startswith(passport.VERIFY_URL + "#ECBP1:")
    assert passport.from_qr_text(text) == s and passport.verify(passport.from_qr_text(text), passport.public_raw(key))


def test_the_report_holds_no_trip_time_or_place(signed):
    payload = signed[0]["payload"]
    assert not re.search(r"\d{2}:\d{2}", payload)  # no time of day
    assert not re.search(r"lat|lon|trip", payload, re.I)
    report = json.loads(payload)
    assert set(report) == {
        "schema", "vehicle", "period", "verdict", "health", "wear", "method", "limits", "inputs_sha256", "software",
        "issued",
    }  # fmt: skip


@pytest.mark.parametrize(
    "q", ["Is my battery healthy?", "How much has the battery degraded?", "What's my battery's state of health?"]
)
def test_battery_health_questions_take_the_rules_path(q):
    from edgecar.nlu import fastpath

    assert fastpath.parse(q).query.intent == "battery_health"


def test_battery_health_answer_and_passport_from_the_app(store, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from edgecar.app import server

    monkeypatch.setattr(server, "PASSPORT_KEY", tmp_path / "key.pem")
    c = TestClient(server.create_app(server.Zone(store, None, None)), base_url="http://127.0.0.1")
    ans = c.post("/api/ask", json={"question": "Is my battery healthy?", "vehicle": "ev-1"}).json()
    assert ans["intent"] == "battery_health" and ans["route"] == "rules" and ans["facts"]["verdict"]
    assert ans["reading"] == "Battery health, from every long drive recorded"
    p = c.post("/api/passport", json={"vehicle": "ev-1"}).json()
    assert len(p["modules"]) == len(p["modules"][0]) >= 21 and set("".join(p["modules"])) <= {"0", "1"}
    assert (tmp_path / "key.pem").exists()
