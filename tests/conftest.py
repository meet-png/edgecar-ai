from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from edgecar.store import Store

NOW = datetime(2024, 1, 29)
CAPACITY_KWH = 20.0


@pytest.fixture
def store(tmp_path) -> Store:
    """A synthetic EV with exactly known physics.

    28 daily 20-minute trips at a steady 50 km/h. Days 0–13 are mild (15 °C, heater off, 7 kW traction);
    days 14–27 are cold (0 °C, heater 2 kW, traction 8 kW). Expected consumption: 14 → 20 kWh/100 km, of which
    heating explains +4 and colder traction +2.
    """
    s = Store(tmp_path / "test.duckdb")
    s.upsert_vehicle(
        {
            "vehicle_id": "ev-1",
            "name": "Test EV",
            "powertrain": "BEV",
            "battery_kwh_usable": CAPACITY_KWH,
            "location": "test",
            "timezone": "UTC",
            "provenance": "sim:test",
            "source_ref": "unit test",
        }
    )
    frames = []
    for day in range(28):
        cold = day >= 14
        n = 1200
        frames.append(
            pd.DataFrame(
                {
                    "ts": pd.date_range(datetime(2024, 1, 1, 8) + timedelta(days=day), periods=n, freq="s"),
                    "vehicle_id": "ev-1",
                    "trip_id": f"ev-1:{day:03d}",
                    "speed_kmh": np.full(n, 50.0),
                    "batt_power_kw": np.full(n, 10.0 if cold else 7.0),
                    "hvac_heater_kw": np.full(n, 2.0 if cold else 0.0),
                    "hvac_ac_kw": np.zeros(n),
                    "ambient_temp_c": np.full(n, 0.0 if cold else 15.0),
                    "soc_pct": np.linspace(90, 80, n),
                }
            )
        )
    s.append_frames(pd.concat(frames, ignore_index=True), "sim:test")
    s.rebuild_trips()
    events = pd.DataFrame(
        [
            {
                "event_id": "e1",
                "ts": datetime(2024, 1, 28, 8, 5),
                "vehicle_id": "ev-1",
                "trip_id": "ev-1:027",
                "category": "dtc",
                "code": "P0534",
                "severity": "warning",
                "source": "UDS",
                "title": "DTC P0534 — A/C refrigerant charge loss",
                "detail": "{}",
                "provenance": "sim:test",
            },
            {
                "event_id": "e2",
                "ts": datetime(2024, 1, 28, 8, 15),
                "vehicle_id": "ev-1",
                "trip_id": "ev-1:027",
                "category": "thermal",
                "code": "BATT_OVERTEMP_WARNING",
                "severity": "critical",
                "source": "BMS",
                "title": "Battery overheating warning",
                "detail": "{}",
                "provenance": "sim:test",
            },
            {
                "event_id": "e3",
                "ts": datetime(2024, 1, 28, 8, 1),
                "vehicle_id": "ev-1",
                "trip_id": "ev-1:027",
                "category": "adas",
                "code": "ADAS_DISENGAGE_LANE_LOST",
                "severity": "warning",
                "source": "ADAS",
                "title": "Disengaged: lane markings not detected",
                "detail": "{}",
                "provenance": "sim:test",
            },
        ]
    )
    s.append_events(events)
    return s
