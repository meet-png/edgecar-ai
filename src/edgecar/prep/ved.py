"""Vehicle Energy Dataset (VED, University of Michigan) → canonical 1 Hz frames.

VED stores one row per OBD-II sample. ``DayNum`` is the trip's start as a fractional day count where 1.0 is
2017-11-01 00:00 local time, and ``Timestamp(ms)`` is the offset within the trip. Battery current is negative
while discharging; the canonical convention is the opposite.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pandas as pd

from edgecar.prep.resample import despike, to_1hz

VED_EPOCH = pd.Timestamp("2017-11-01 00:00:00")

_COLUMNS = [
    "DayNum",
    "VehId",
    "Trip",
    "Timestamp(ms)",
    "Latitude[deg]",
    "Longitude[deg]",
    "Vehicle Speed[km/h]",
    "Engine RPM[RPM]",
    "OAT[DegC]",
    "Fuel Rate[L/hr]",
    "Air Conditioning Power[kW]",
    "Air Conditioning Power[Watts]",
    "Heater Power[Watts]",
    "HV Battery Current[A]",
    "HV Battery SOC[%]",
    "HV Battery Voltage[V]",
]


def read_raw(ved_dir: Path, veh_ids: Iterable[int]) -> pd.DataFrame:
    """Read the rows of the given VED vehicles from all weekly CSV files."""
    wanted = set(veh_ids)
    parts = []
    for f in sorted(Path(ved_dir).glob("VED_*_week.csv")):
        df = pd.read_csv(f, usecols=_COLUMNS)
        parts.append(df[df["VehId"].isin(wanted)])
    return pd.concat(parts, ignore_index=True)


def to_canonical(raw: pd.DataFrame, vehicle_id: str) -> pd.DataFrame:
    """Map raw VED rows of a single vehicle to canonical signals at 1 Hz."""
    ts = VED_EPOCH + pd.to_timedelta(raw["DayNum"] - 1.0, unit="D") + pd.to_timedelta(raw["Timestamp(ms)"], unit="ms")
    current = -raw["HV Battery Current[A]"]  # canonical: positive = discharging
    voltage = raw["HV Battery Voltage[V]"]
    ac_kw = raw["Air Conditioning Power[kW]"].fillna(raw["Air Conditioning Power[Watts]"] / 1000.0)
    sig = pd.DataFrame(
        {
            "ts": ts.dt.round("ms"),
            "trip": raw["Trip"].to_numpy(),
            "speed_kmh": raw["Vehicle Speed[km/h]"],
            "soc_pct": raw["HV Battery SOC[%]"],
            "batt_voltage_v": voltage,
            "batt_current_a": current,
            "batt_power_kw": voltage * current / 1000.0,
            "ambient_temp_c": raw["OAT[DegC]"],
            "hvac_heater_kw": raw["Heater Power[Watts]"] / 1000.0,
            "hvac_ac_kw": ac_kw,
            "lat_deg": raw["Latitude[deg]"],
            "lon_deg": raw["Longitude[deg]"],
            "fuel_rate_lph": raw["Fuel Rate[L/hr]"],
            "engine_rpm": raw["Engine RPM[RPM]"],
        }
    )
    sig = sig.dropna(axis=1, how="all")
    frames = []
    for trip_no, trip in sig.groupby("trip", sort=True):
        f = to_1hz(despike(trip.drop(columns="trip").sort_values("ts")))
        f.insert(1, "vehicle_id", vehicle_id)
        f.insert(2, "trip_id", f"{vehicle_id}:{int(trip_no):05d}")
        frames.append(f)
    out = pd.concat(frames, ignore_index=True)
    # Physical sanity bounds; values outside are sensor glitches.
    out.loc[~out["speed_kmh"].between(0, 250), "speed_kmh"] = np.nan
    if "soc_pct" in out:
        out.loc[~out["soc_pct"].between(0, 100), "soc_pct"] = np.nan
    return out.sort_values(["ts"]).reset_index(drop=True)
