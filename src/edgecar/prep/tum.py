"""TUM "Battery and Heating Data in Real Driving Cycles" (BMW i3, 60 Ah) → canonical 1 Hz frames.

Each ``Trip*.csv`` is one trip sampled at 10 Hz (``;``-separated, Latin-1). Category A trips were recorded in
summer 2019 with a reduced channel set, category B in winter 2019/20 with all channels. Trip start times come
from ``Overview.xlsx``. Battery current is negative while discharging; the canonical convention is the opposite.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from edgecar.prep.resample import despike, sanitize, to_1hz

# Normalised source header → canonical signal. Headers differ slightly between files (e.g. "Velocity [km/h]]]").
_MAP = {
    "velocity [km/h]": "speed_kmh",
    "elevation [m]": "elevation_m",
    "throttle [%]": "throttle_pct",
    "motor torque [nm]": "motor_torque_nm",
    "longitudinal acceleration [m/s^2]": "accel_mps2",
    "regenerative braking signal": "regen_active",
    "battery voltage [v]": "batt_voltage_v",
    "battery current [a]": "batt_current_a",
    "battery temperature [°c]": "batt_temp_c",
    "max. battery temperature [°c]": "batt_temp_max_c",
    "soc [%]": "soc_pct",
    "heating power can [kw]": "hvac_heater_kw",
    "aircon power [kw]": "hvac_ac_kw",
    "ambient temperature [°c]": "ambient_temp_c",
    "cabin temperature sensor [°c]": "cabin_temp_c",
}


def _norm(header: str) -> str:
    h = header.strip().lower()
    return re.sub(r"\]+$", "]", h)  # collapse "]]]" typos


def read_overview(tum_dir: Path) -> pd.DataFrame:
    ov = pd.read_excel(Path(tum_dir) / "Overview.xlsx")
    ov = ov[ov["Trip"].astype(str).str.fullmatch(r"Trip[AB]\d+")].copy()

    def parse(s: str) -> pd.Timestamp:
        y, mo, d, h, mi, se = (int(x) for x in re.findall(r"\d+", str(s))[:6])
        return pd.Timestamp(year=y, month=mo, day=d, hour=h, minute=mi, second=se)

    ov["start"] = ov["Date"].map(parse)
    return ov[["Trip", "start", "Route/Area", "Weather"]].rename(columns={"Route/Area": "route", "Weather": "weather"})


def to_canonical(tum_dir: Path, vehicle_id: str = "i3-tum") -> pd.DataFrame:
    tum_dir = Path(tum_dir)
    overview = read_overview(tum_dir).set_index("Trip")
    frames = []
    for trip_name, row in overview.iterrows():
        path = tum_dir / f"{trip_name}.csv"
        if not path.exists():
            continue
        raw = pd.read_csv(path, sep=";", encoding="latin-1")
        cols = {c: _MAP[_norm(c)] for c in raw.columns if _norm(c) in _MAP}
        sig = raw[list(cols)].rename(columns=cols)
        sig = sig.loc[:, ~sig.columns.duplicated()]
        sig["batt_current_a"] = -sig["batt_current_a"]  # canonical: positive = discharging
        sig["batt_power_kw"] = sig["batt_voltage_v"] * sig["batt_current_a"] / 1000.0
        sig.insert(0, "ts", row["start"] + pd.to_timedelta(raw["Time [s]"], unit="s"))
        f = to_1hz(despike(sanitize(sig)))
        f.insert(1, "vehicle_id", vehicle_id)
        f.insert(2, "trip_id", f"{vehicle_id}:{trip_name[4:]}")  # e.g. i3-tum:A01
        frames.append(f)
    return drop_overlapping_trips(pd.concat(frames, ignore_index=True))


def drop_overlapping_trips(frames: pd.DataFrame) -> pd.DataFrame:
    """One vehicle cannot drive two trips at once: drop any trip that starts before the previous one ended.

    In ``Overview.xlsx``, A30 and A31 share a start time and A25's recording runs past the next trip's start — start
    times that cannot all be right. The later trip of each overlapping pair is excluded rather than re-timed by guess.
    """
    spans = frames.groupby("trip_id")["ts"].agg(["min", "max"]).sort_values("min")
    keep, last_end, dropped = [], pd.Timestamp.min, []
    for trip_id, span in spans.iterrows():
        if span["min"] <= last_end:
            dropped.append(trip_id)
            continue
        keep.append(trip_id)
        last_end = span["max"]
    if dropped:
        print(
            f"  TUM: excluded {len(dropped)} trip(s) whose start time overlaps the previous trip: {', '.join(dropped)}"
        )
    return frames[frames["trip_id"].isin(keep)].sort_values("ts").reset_index(drop=True)
