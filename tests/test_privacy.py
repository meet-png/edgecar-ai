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
