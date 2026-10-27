"""The demo vehicles. Each one is a real vehicle from a public dataset, kept separate (never stitched together)."""

from __future__ import annotations

from dataclasses import dataclass

from edgecar.schema import Provenance


@dataclass(frozen=True)
class Vehicle:
    vehicle_id: str
    name: str
    powertrain: str  # "BEV" or "PHEV"
    battery_kwh_usable: float | None
    location: str
    timezone: str
    provenance: Provenance
    source_ref: str


VEHICLES: dict[str, Vehicle] = {
    v.vehicle_id: v
    for v in [
        Vehicle(
            "leaf-455",
            "Nissan Leaf (2013, 24 kWh)",
            "BEV",
            21.3,
            "Ann Arbor, Michigan, USA",
            "America/Detroit",
            Provenance.REAL_VED,
            "VED VehId 455",
        ),
        Vehicle(
            "leaf-010",
            "Nissan Leaf (2013, 24 kWh)",
            "BEV",
            21.3,
            "Ann Arbor, Michigan, USA",
            "America/Detroit",
            Provenance.REAL_VED,
            "VED VehId 10",
        ),
        Vehicle(
            "phev-457",
            "Plug-in hybrid sedan (2.0 L, CVT)",
            "PHEV",
            None,
            "Ann Arbor, Michigan, USA",
            "America/Detroit",
            Provenance.REAL_VED,
            "VED VehId 457",
        ),
        Vehicle(
            "i3-tum",
            "BMW i3 (60 Ah)",
            "BEV",
            18.8,
            "Munich, Germany",
            "Europe/Berlin",
            Provenance.REAL_TUM,
            "TUM 'Battery and Heating Data in Real Driving Cycles'",
        ),
    ]
}
