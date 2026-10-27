"""Build the on-device store from the fetched public datasets — every source through its wire protocol.

    python -m edgecar.prep.build --data data --out data/edgecar.duckdb

==================  ==========================================================================================
Nissan Leaf × 2     real VED signals → Leaf CAN frames (real community DBC) → candump log → decoder
BMW i3              real TUM signals → CAN FD frames (EdgeCar reference DBC) + Android VHAL properties → decoders
PHEV                real VED signals, stored directly (no public DBC for this vehicle)
ADAS (all cars)     simulated on real speed profiles → SOME/IP notifications (pcap) → decoder
Overheat scenario   derived from a real i3 trip → QNX slog2 · UDS/ISO-TP · CAN FD · SOME/IP · VHAL → decoders
==================  ==========================================================================================

Every capture is kept under ``data/interim/wire/`` (the CAN logs open in standard CAN tools, the pcaps in Wireshark).
"""

from __future__ import annotations

import argparse
import time
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from edgecar.detect import detect_all
from edgecar.fleet import VEHICLES
from edgecar.ingest import adapters
from edgecar.prep import tum, ved
from edgecar.protocols import someip, vhal
from edgecar.protocols.can import (
    I3_FD_DBC_FILES,
    I3_FD_SIGNALS,
    REFERENCE_DBC_DIR,
    CanCodec,
    read_log,
    segment_trips,
    write_log,
)
from edgecar.protocols.slog2 import parse_line
from edgecar.sim import adas, thermal
from edgecar.store import Store

VED_VEHICLES = {"leaf-455": 455, "leaf-010": 10, "phev-457": 457}
CAN_REPLAY = {"leaf-455", "leaf-010"}  # vehicles with a real DBC: their data reaches the store as decoded CAN frames


def _max_error(decoded: pd.DataFrame, source: pd.DataFrame, columns: list[str]) -> str:
    """Largest |decoded − source| per signal; a value lost or invented in transit is reported, never hidden."""
    both = decoded.merge(source, on="ts", suffixes=("", "_src"))
    parts = []
    for c in columns:
        if c not in both or f"{c}_src" not in both:
            continue
        mismatch = int((both[c].isna() != both[f"{c}_src"].isna()).sum())
        err = float((both[c] - both[f"{c}_src"]).abs().max())
        parts.append(f"{c} {err:.3g}" + (f" (+{mismatch} missing/extra)" if mismatch else ""))
    return ", ".join(parts)


def replay_over_can(codec: CanCodec, frames: pd.DataFrame, vehicle_id: str, wire: Path) -> pd.DataFrame:
    """Leaf: real signals → Leaf CAN frames → candump log → decode → trips from bus activity (+ GNSS by time)."""
    log = wire / f"{vehicle_id}.can.log"
    t0 = time.perf_counter()
    n = write_log(codec.encode(frames), log)
    decoded = segment_trips(codec.decode(read_log(log)), vehicle_id)
    decoded = decoded.merge(frames[["ts", "lat_deg", "lon_deg"]].dropna(), on="ts", how="left")
    errs = _max_error(decoded, frames, [m.canonical for m in codec.signals])
    print(
        f"  {vehicle_id:9s} CAN    {n:>9,} frames → {log.name} ({log.stat().st_size / 1e6:.1f} MB), "
        f"{time.perf_counter() - t0:.1f} s; {decoded['trip_id'].nunique()} trips from bus activity; "
        f"max |decoded − source|: {errs}"
    )
    return decoded


def replay_over_canfd_vhal(
    codec: CanCodec, frames: pd.DataFrame, tag: str, wire: Path, extra_vhal: list[vhal.PropValue] = ()
) -> tuple[pd.DataFrame, list[vhal.PropValue]]:
    """i3: battery/powertrain signals → CAN FD frames; speed and outside temperature → Android VHAL properties."""
    t0 = time.perf_counter()
    can_log, vhal_log = wire / f"{tag}.canfd.log", wire / f"{tag}.vhal.jsonl"
    n_can = write_log(codec.encode(frames), can_log)
    ts_ns = frames["ts"].astype("datetime64[ns]").astype("int64").to_numpy()
    props = [
        vhal.PropValue(int(t), vhal.PERF_VEHICLE_SPEED, float(v) / 3.6)
        for t, v in zip(ts_ns, frames["speed_kmh"], strict=True)
        if pd.notna(v)
    ]
    props += [
        vhal.PropValue(int(t), vhal.ENV_OUTSIDE_TEMPERATURE, float(v))
        for t, v in zip(ts_ns, frames["ambient_temp_c"], strict=True)
        if pd.notna(v)
    ]
    n_vhal = vhal.write_jsonl([*props, *extra_vhal], vhal_log)
    decoded_vhal = list(vhal.read_jsonl(vhal_log))
    decoded = codec.decode(read_log(can_log)).merge(adapters.vhal_frames(decoded_vhal), on="ts", how="outer")
    decoded = decoded.sort_values("ts").reset_index(drop=True)
    cols = [m.canonical for m in codec.signals if m.canonical in frames] + ["speed_kmh", "ambient_temp_c"]
    print(
        f"  {tag:9s} CAN FD {n_can:>9,} frames + VHAL {n_vhal:,} property events, {time.perf_counter() - t0:.1f} s; "
        f"max |decoded − source|: {_max_error(decoded, frames, cols)}"
    )
    return decoded, decoded_vhal


def build(data_dir: Path, out: Path) -> None:
    t0 = time.perf_counter()
    if out.exists():
        out.unlink()
    wire = data_dir / "interim" / "wire"
    wire.mkdir(parents=True, exist_ok=True)
    store = Store(out, threads=4, memory_limit="2GB")
    for v in VEHICLES.values():
        store.upsert_vehicle(asdict(v) | {"provenance": str(v.provenance)})

    print("signals:")
    cache = data_dir / "interim" / "ved_selected.csv.gz"
    if cache.exists():
        raw = pd.read_csv(cache)
    else:
        raw = ved.read_raw(data_dir / "raw" / "ved", VED_VEHICLES.values())
        raw.to_csv(cache, index=False, compression="gzip")
    leaf_dbc = data_dir / "raw" / "leaf_dbc"
    leaf_codec = CanCodec(leaf_dbc) if leaf_dbc.exists() else None
    for vehicle_id, veh in VED_VEHICLES.items():
        frames = ved.to_canonical(raw[raw["VehId"] == veh], vehicle_id)
        if leaf_codec is not None and vehicle_id in CAN_REPLAY:
            frames = replay_over_can(leaf_codec, frames, vehicle_id, wire)
        else:
            print(f"  {vehicle_id:9s} direct (no public DBC for this vehicle)")
        store.append_frames(frames, str(VEHICLES[vehicle_id].provenance))

    fd_codec = CanCodec(REFERENCE_DBC_DIR, I3_FD_DBC_FILES, I3_FD_SIGNALS)
    i3 = tum.to_canonical(data_dir / "raw" / "tum_bmw_i3")
    decoded, _ = replay_over_canfd_vhal(fd_codec, i3, "i3-tum", wire)
    store.append_frames(segment_trips(decoded, "i3-tum"), str(VEHICLES["i3-tum"].provenance))

    scenario = thermal.battery_overheat(i3[i3["trip_id"] == thermal.BASE_TRIP])
    sc_frames, sc_vhal = replay_over_canfd_vhal(fd_codec, scenario.frames, "scenario", wire, scenario.vhal_values)
    sc_frames = segment_trips(sc_frames, "i3-tum").assign(trip_id=thermal.SCENARIO_TRIP)
    store.append_frames(sc_frames, thermal.PROVENANCE)

    print(f"trips: {store.rebuild_trips():,}   minute roll-ups: {store.rebuild_rollups():,}")

    print("events:")
    print(f"  detected from real signals (charging, hard braking, battery temperature): {detect_all(store):,}")

    # overheat scenario: every event comes out of a protocol decoder
    qnx_log, diag_log, sc_pcap = (
        wire / "scenario.qnx.log",
        wire / "scenario.diag.can.log",
        wire / "scenario.someip.pcap",
    )
    qnx_log.write_text("\n".join(scenario.slog2_lines) + "\n", encoding="utf-8")
    write_log(scenario.diag_frames, diag_log)
    someip.write_pcap(scenario.someip_records, sc_pcap)
    year = thermal.START.year
    sc_events = pd.concat(
        [
            adapters.slog2_events(
                (r for r in (parse_line(x, year) for x in qnx_log.read_text().splitlines()) if r),
                "i3-tum",
                thermal.PROVENANCE,
            ),
            adapters.uds_events(read_log(diag_log), "i3-tum", thermal.PROVENANCE),
            adapters.someip_events(someip.read_pcap(sc_pcap), "i3-tum", thermal.PROVENANCE),
            adapters.vhal_events(sc_vhal, "i3-tum", thermal.PROVENANCE),
            adapters.canfd_thermal_events(sc_frames, "i3-tum", thermal.PROVENANCE),
        ],
        ignore_index=True,
    )
    store.append_events(adapters.assign_trips(sc_events, store))
    got, want = set(sc_events["code"]), {code for _, code in scenario.truth}
    print(
        f"  overheat scenario: {len(sc_events)} events decoded from QNX slog2, UDS/ISO-TP, SOME/IP, VHAL, CAN FD; "
        f"ground truth recovered {len(want & got)}/{len(want)}"
        + (f" — MISSING {sorted(want - got)}" if want - got else "")
    )

    baton, td2d = data_dir / "raw" / "baton", data_dir / "raw" / "td2d"
    if (baton / "takeover_events_or.csv").exists() and td2d.exists():
        cal = adas.load_calibration(baton, td2d)
        total = 0
        for vehicle_id in VEHICLES:
            changes = adas.simulate(store, cal, vehicle_id)
            pcap = wire / f"{vehicle_id}.adas.pcap"
            adas.to_pcap(changes, pcap)
            ev = adapters.assign_trips(
                adapters.someip_events(someip.read_pcap(pcap), vehicle_id, adas.PROVENANCE), store
            )
            store.append_events(ev)
            assert len(ev) == len(changes), f"{vehicle_id}: {len(changes)} ADAS changes but {len(ev)} decoded"
            total += len(ev)
        print(f"  ADAS (SOME/IP pcap, BATON timing, TD2D takeovers): {total:,} events, all decoded from the captures")

    store.con.execute("CHECKPOINT")
    store.close()
    print(f"store: {out} ({out.stat().st_size / 1e6:.1f} MB) in {time.perf_counter() - t0:.1f} s")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", type=Path, default=Path("data"))
    p.add_argument("--out", type=Path, default=Path("data/edgecar.duckdb"))
    a = p.parse_args()
    build(a.data, a.out)


if __name__ == "__main__":
    main()
