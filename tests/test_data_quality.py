"""Data-quality rules found on the real datasets, pinned so they cannot regress."""

import numpy as np
import pandas as pd
import pytest

from edgecar.detect import detect_charging_while_logging
from edgecar.store import Store


def test_charging_inside_a_recording_is_not_driving_energy(tmp_path):
    """Two real TUM i3 recordings keep logging while the car charges; that energy is not 'negative consumption'."""
    s = Store(tmp_path / "t.duckdb")
    s.upsert_vehicle(
        {
            "vehicle_id": "ev-1",
            "name": "Test EV",
            "powertrain": "BEV",
            "battery_kwh_usable": 20.0,
            "location": "test",
            "timezone": "UTC",
            "provenance": "sim:test",
            "source_ref": "unit test",
        }
    )
    n = 600  # 10 min at 60 km/h and 12 kW, then 10 min parked on a 7 kW charger
    s.append_frames(
        pd.DataFrame(
            {
                "ts": pd.date_range("2024-01-01 08:00", periods=2 * n, freq="s"),
                "vehicle_id": "ev-1",
                "trip_id": "ev-1:001",
                "speed_kmh": np.r_[np.full(n, 60.0), np.zeros(n)],
                "batt_power_kw": np.r_[np.full(n, 12.0), np.full(n, -7.0)],
                "soc_pct": np.r_[np.linspace(80, 78, n), np.linspace(78, 82, n)],
            }
        ),
        "sim:test",
    )
    s.rebuild_trips()
    trip = s.sql("SELECT * FROM trips").iloc[0]
    assert trip.distance_km == pytest.approx(10.0)
    assert trip.energy_kwh == pytest.approx(2.0)  # the 1.17 kWh charged is not subtracted
    assert trip.kwh_per_100km == pytest.approx(20.0)

    ev = detect_charging_while_logging(s)
    assert len(ev) == 1
    assert ev.iloc[0]["code"] == "CHARGE_SESSION"
    assert ev.iloc[0]["title"] == "Charged from 78% to 82%"
