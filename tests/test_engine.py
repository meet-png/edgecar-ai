from datetime import datetime

import pandas as pd
import pytest
from conftest import CAPACITY_KWH, NOW

from edgecar.analytics import Engine
from edgecar.query import Query, TimeWindow


def engine(store) -> Engine:
    return Engine(store, "ev-1", now=NOW)


def test_trip_summary_physics(store):
    t = store.sql("SELECT * FROM trips ORDER BY start_ts")
    assert len(t) == 28
    first, last = t.iloc[0], t.iloc[-1]
    assert first.distance_km == pytest.approx(50 * 1200 / 3600, rel=1e-6)
    assert first.kwh_per_100km == pytest.approx(14.0, rel=1e-6)
    assert last.kwh_per_100km == pytest.approx(20.0, rel=1e-6)
    assert last.heater_kwh == pytest.approx(2.0 * 1200 / 3600, rel=1e-6)


def test_explain_change_recovers_injected_factors(store):
    r = engine(store).run(Query(intent="explain_change", metric="range", window=TimeWindow(kind="last_days", days=14)))
    assert r.facts["consumption_before"] == pytest.approx(14.0, rel=1e-6)
    assert r.facts["consumption_after"] == pytest.approx(20.0, rel=1e-6)
    assert r.facts["range_before_km"] == pytest.approx(CAPACITY_KWH * 100 / 14.0, rel=1e-6)
    assert r.facts["range_after_km"] == pytest.approx(CAPACITY_KWH * 100 / 20.0, rel=1e-6)
    factors = {f["factor"]: f["delta_kwh_100km"] for f in r.facts["factors"]}
    assert factors["Cabin heating and A/C"] == pytest.approx(4.0, abs=0.01)
    assert factors["Colder air, battery and tyres"] == pytest.approx(2.0, abs=0.01)
    assert sum(factors.values()) == pytest.approx(6.0, abs=0.01)
    assert "fell" in r.headline
    # what the answer displays adds up: shown change = shown top factors + "other factors"
    shown_change = round(r.facts["range_after_km"]) - round(r.facts["range_before_km"])
    shown_top = sum(round(f["delta_range_km"]) for f in r.facts["top_factors"])
    assert shown_top + r.facts["other_shown"] == shown_change


def test_compare_by_temperature(store):
    r = engine(store).run(
        Query(intent="compare", metric="energy_consumption", compare_by="temperature", window=TimeWindow(kind="all"))
    )
    seg = {s["segment"]: s["value"] for s in r.facts["segments"]}
    assert seg["0–10 °C"] == pytest.approx(20.0, rel=1e-3)
    assert seg["10–20 °C"] == pytest.approx(14.0, rel=1e-3)


def test_fuel_question_on_bev_is_answered_in_energy_terms(store):
    r = engine(store).run(Query(intent="summary", metric="fuel_efficiency", window=TimeWindow(kind="all")))
    assert r.facts["metric"] == "energy_efficiency"
    assert any("battery-electric" in n for n in r.notes)


def test_list_events_filters_by_kind(store):
    r = engine(store).run(
        Query(intent="list_events", event="adas_disengagement", window=TimeWindow(kind="last_days", days=7))
    )
    assert r.facts["count"] == 1
    assert r.evidence == ["e3"]


def test_events_before_finds_root_cause_and_drops_unrelated_events(store):
    r = engine(store).run(Query(intent="events_before", event="battery_overheat_warning", lookback_min=15))
    assert r.facts["anchor_code"] == "BATT_OVERTEMP_WARNING"
    assert r.evidence == ["e2", "e1"]  # the ADAS event (e3) is unrelated to a thermal warning
    assert r.facts["root_cause_code"] == "P0534"
    assert r.facts["root_cause_lead_min"] == pytest.approx(10.0)
    assert r.details[0].startswith("Likely root cause: DTC P0534 — A/C refrigerant charge loss")


def test_list_events_explains_an_empty_period(store):
    r = engine(store).run(Query(intent="list_events", event="dtc", window=TimeWindow(kind="today")))
    assert r.facts["count"] == 0
    assert r.facts["drove"] is False
    assert "not driven" in r.details[0]
    assert r.evidence == ["e1"]  # points to the most recent matching event instead


def test_status_uses_latest_frame(store):
    r = engine(store).run(Query(intent="status"))
    assert r.facts["soc_pct"] == pytest.approx(80.0, abs=0.01)


def test_root_cause_ignores_unrelated_faults_and_same_subsystem_glitches(store):
    """An earlier unrelated fault (lost CAN communication) and an earlier glitch in the same subsystem are not the
    cause; the most recent relevant fault before the symptom began is."""

    def event(eid, minute, category, code, severity, title):
        return {
            "event_id": eid,
            "ts": datetime(2024, 1, 28, 8, minute),
            "vehicle_id": "ev-1",
            "trip_id": "ev-1:027",
            "category": category,
            "code": code,
            "severity": severity,
            "source": "test",
            "title": title,
            "detail": "{}",
            "provenance": "sim:test",
        }

    store.append_events(
        pd.DataFrame(
            [
                event("d1", 2, "dtc", "U0155", "warning", "DTC U0155 — lost communication with the cluster"),
                event("d2", 3, "system", "QNX_THERMAL_MGR", "warning", "thermal_mgr: sensor glitch, recovered"),
                event("o1", 6, "thermal", "BATT_TEMP_RISE_RATE", "warning", "BMS: temperature rising fast"),
            ]
        )
    )
    r = engine(store).run(Query(intent="events_before", event="battery_overheat_warning", lookback_min=15))
    assert r.facts["root_cause_code"] == "P0534"
    assert "U0155" not in [e["code"] for e in r.table]  # an unrelated fault is left out of the timeline
    assert r.facts["unrelated_left_out"] == 1
