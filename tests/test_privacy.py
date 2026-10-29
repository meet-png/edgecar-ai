from datetime import datetime

import duckdb
import pytest
from conftest import NOW

from edgecar.analytics import Engine, Result
from edgecar.privacy import Role, redact
from edgecar.query import Query
from edgecar.store import Store


def test_encrypted_store_is_unreadable_without_the_key(tmp_path):
    path = tmp_path / "vehicle.duckdb"
    s = Store(path, key="k3y-from-secure-element")
    s.upsert_vehicle(
        {
            "vehicle_id": "secret-vin-123",
            "name": "Test",
            "powertrain": "BEV",
            "battery_kwh_usable": 20.0,
            "location": "x",
            "timezone": "UTC",
            "provenance": "sim:test",
            "source_ref": "t",
        }
    )
    s.con.execute("CHECKPOINT")
    s.close()
    assert b"secret-vin-123" not in path.read_bytes()
    with pytest.raises(duckdb.Error):
        duckdb.connect(str(path)).execute("SELECT * FROM vehicles").fetchall()
    with pytest.raises(duckdb.Error):
        Store(path, key="wrong-key", read_only=True)
    s = Store(path, key="k3y-from-secure-element", read_only=True)
    assert s.sql("SELECT vehicle_id FROM vehicles")["vehicle_id"].tolist() == ["secret-vin-123"]


def _timeline() -> Result:
    return Result(
        "list_events",
        "3 events yesterday at 13:25:33.",
        details=[
            "13:11:00 — thermal_mgr: compressor locked out (QNX slog2)",
            "13:19:51 — Late takeover: responded after 2.00 s (SOME/IP 0x1A01 (ADAS))",
            "13:20:00 — Parked at 42.243933, -83.743826",
        ],
        table=[{"time": "13:19:51", "code": "ADAS_DISENGAGE_TAKEOVER_LATE", "title": "Late takeover", "detail": "{}"}],
    )


def test_driver_sees_everything():
    r = redact(_timeline(), Role.DRIVER)
    assert r.details == _timeline().details


def test_fleet_gets_coarse_time_no_location_no_behaviour_no_raw_logs():
    r = redact(_timeline(), Role.FLEET)
    text = " ".join([r.headline, *r.details])
    assert "13:25:33" not in text and "13:xx" in text
    assert "42.243933" not in text
    assert "responded after 2.00 s" not in text
    assert "QNX" not in text
    assert r.table == []


def test_service_keeps_diagnostics_but_not_location_or_behaviour():
    r = redact(_timeline(), Role.SERVICE)
    text = " ".join(r.details)
    assert "(QNX slog2)" in text
    assert "42.243933" not in text
    assert "responded after 2.00 s" not in text


def test_redaction_never_changes_the_numbers(store):
    raw = Engine(store, "ev-1", now=NOW).run(Query(intent="status"))
    assert redact(raw, Role.FLEET).facts["soc_pct"] == raw.facts["soc_pct"]


def _before_overheat() -> Result:
    t = datetime(2024, 3, 5, 13, 19, 51)
    return Result(
        "events_before",
        "Battery overheat at 13:19:51.",
        {"anchor_time": t, "speed_kmh_max": 92.0, "batt_temp_max": 61.0},
        chart={
            "type": "line",
            "x": ["13:19:49", "13:19:50", "13:19:51"],
            "series": {"battery temp (°C)": [58.0, 60.0, 61.0], "speed (km/h)": [90.0, 91.0, 92.0]},
            "behaviour": ["speed (km/h)"],
            "clock": True,
        },
        details=["Battery temperature: 58.0 → 61.0 °C (peak 61.0 °C).", "Speed: 90.0 → 92.0 km/h (peak 92.0 km/h)."],
        window=(datetime(2024, 3, 5, 13, 4, 51), t),
    )


def test_structured_answer_follows_the_policy_too():
    """The app ships facts, chart and window as JSON, so they must be redacted like the text."""
    driver = redact(_before_overheat(), Role.DRIVER)
    assert "speed (km/h)" in driver.chart["series"] and driver.facts["speed_kmh_max"] == 92.0

    service = redact(_before_overheat(), Role.SERVICE)  # diagnostics to the second, but no driving trace
    assert list(service.chart["series"]) == ["battery temp (°C)"]
    assert "speed_kmh_max" not in service.facts and service.facts["batt_temp_max"] == 61.0
    assert not any(d.startswith("Speed") for d in service.details)
    assert service.facts["anchor_time"].second == 51

    fleet = redact(_before_overheat(), Role.FLEET)  # to the hour: no per-second trace, no exact times
    assert fleet.chart is None
    assert fleet.facts["anchor_time"] == datetime(2024, 3, 5, 13)
    assert fleet.window == (datetime(2024, 3, 5, 13), datetime(2024, 3, 5, 14))  # still contains 13:04–13:19
    assert redact(Result("x", "", {"logged": "at 13:19:51"}), Role.FLEET).facts["logged"] == "at 13:xx"
    assert _before_overheat().chart["series"]["speed (km/h)"]  # the original is untouched
