"""Live ingestion: consume a vehicle's wire traffic message by message and keep the store queryable throughout.

    python -m edgecar.ingest.live --vehicle leaf-455 --days 7

The recorded captures stand in for the live buses: CAN frames (candump log) and SOME/IP notifications (pcap) are
merged into one time-ordered stream, decoded incrementally, and committed in micro-batches of one second of vehicle
time. Trips are cut on bus silence as they happen; only the current trip's summary is recomputed per batch.

Reported: messages and rows ingested per wall-clock second, the real-time factor (vehicle seconds ingested per
wall-clock second), and the commit latency — how long after a second of traffic ends it becomes queryable.
"""

from __future__ import annotations

import argparse
import heapq
import signal
import statistics
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pandas as pd

from edgecar.assistant import Assistant
from edgecar.fleet import VEHICLES
from edgecar.ingest import adapters
from edgecar.protocols import someip
from edgecar.protocols.can import TRIP_GAP_S, CanCodec, Frame, read_log
from edgecar.sim.adas import PROVENANCE as ADAS_PROVENANCE
from edgecar.stats import percentile
from edgecar.store import Store

BATCH_S = 1.0


def merged_stream(can_log: Path, pcap: Path | None, start: float, end: float) -> Iterator[tuple[float, str, object]]:
    """Time-ordered (posix_ts, kind, message) from all captures within [start, end)."""
    can = ((f.ts, "can", f) for f in read_log(can_log) if start <= f.ts < end)
    streams = [can]
    if pcap is not None and pcap.exists():
        streams.append((ts, "someip", data) for ts, data in someip.read_pcap(pcap) if start <= ts < end)
    yield from heapq.merge(*streams, key=lambda x: x[0])


class LiveIngestor:
    def __init__(self, store: Store, codec: CanCodec, vehicle_id: str, provenance: str) -> None:
        self.store, self.codec, self.vehicle_id, self.provenance = store, codec, vehicle_id, provenance
        self.trip_id: str | None = None
        self.last_ts: float | None = None
        self.can_batch: list[Frame] = []
        self.someip_batch: list[tuple[float, bytes]] = []
        self.commit_latency_ms: list[float] = []
        self.rows = 0
        self.events = 0

    def feed(self, ts: float, kind: str, msg) -> None:
        if self.last_ts is None or ts - self.last_ts > TRIP_GAP_S:  # ignition on after bus silence: a new trip
            self.flush()
            self.trip_id = f"{self.vehicle_id}:{pd.to_datetime(ts, unit='s'):%Y%m%d-%H%M%S}"
        if self.can_batch and ts - self.can_batch[0].ts >= BATCH_S:
            self.flush()
        if kind == "can":
            self.can_batch.append(msg)
        else:
            self.someip_batch.append((ts, msg))
        self.last_ts = ts

    def flush(self) -> None:
        if not self.can_batch and not self.someip_batch:
            return
        t0 = time.perf_counter()
        if self.can_batch:
            frames = self.codec.decode(self.can_batch)
            frames.insert(1, "vehicle_id", self.vehicle_id)
            frames.insert(2, "trip_id", self.trip_id)
            self.rows += self.store.append_frames(frames, self.provenance)
            self.store.rebuild_trips([self.trip_id])
        if self.someip_batch:
            ev = adapters.someip_events(self.someip_batch, self.vehicle_id, ADAS_PROVENANCE)
            ev["trip_id"] = self.trip_id
            self.events += self.store.append_events(ev)
        self.commit_latency_ms.append((time.perf_counter() - t0) * 1000)
        self.can_batch, self.someip_batch = [], []


_stopping = False


def _stop(*_) -> None:
    global _stopping
    _stopping = True  # checked between messages, so a stop never interrupts a commit


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vehicle", default="leaf-455", choices=["leaf-455", "leaf-010"])
    ap.add_argument("--wire", type=Path, default=Path("data/interim/wire"))
    ap.add_argument("--dbc", type=Path, default=Path("data/raw/leaf_dbc"))
    ap.add_argument("--days", type=float, default=7.0, help="replay the last N days of the capture")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument(
        "--pace",
        type=float,
        default=0.0,
        help="replay at this multiple of real time while driving (parked time is skipped); 0 = as fast as possible",
    )
    a = ap.parse_args()
    signal.signal(signal.SIGTERM, _stop)  # a supervisor stops the replay; the summary is still printed

    can_log = a.wire / f"{a.vehicle}.can.log"
    last = max(f.ts for f in read_log(can_log))
    start = last - a.days * 86400
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "live.duckdb", threads=a.threads)
        v = VEHICLES[a.vehicle]
        store.upsert_vehicle({**v.__dict__, "provenance": str(v.provenance)})
        ing = LiveIngestor(store, CanCodec(a.dbc), a.vehicle, str(v.provenance))
        messages, t0 = 0, time.perf_counter()
        anchor, prev = (0.0, 0.0), None  # (vehicle time, wall time) at the start of the current drive
        for ts, kind, msg in merged_stream(can_log, a.wire / f"{a.vehicle}.adas.pcap", start, last + 1):
            if _stopping:
                break
            if a.pace:
                if prev is None or ts - prev > TRIP_GAP_S:  # parked: go straight to the next drive
                    anchor = (ts, time.perf_counter())
                elif (wait := anchor[1] + (ts - anchor[0]) / a.pace - time.perf_counter()) > 0:
                    time.sleep(wait)
                prev = ts
            ing.feed(ts, kind, msg)
            messages += 1
        ing.flush()
        wall = time.perf_counter() - t0
        driven_s = store.sql("SELECT sum(duration_min) * 60 AS s FROM trips")["s"][0] or 0.0
        lat = ing.commit_latency_ms or [0.0]
        print(
            f"replayed {driven_s / 3600:.1f} h of {a.vehicle} driving: {messages:,} messages ({ing.rows:,} 1 Hz rows, "
            f"{ing.events:,} ADAS events, {store.sql('SELECT count(*) AS n FROM trips')['n'][0]} trips) "
            f"in {wall:.1f} s on {a.threads} threads"
        )
        print(
            f"throughput: {messages / wall:,.0f} messages/s · real-time factor ×{driven_s / wall:,.1f} "
            f"(driving time ingested per wall-clock second)"
        )
        print(
            f"commit latency per 1 s batch: p50 {statistics.median(lat):.1f} ms · "
            f"p95 {percentile(lat, 95):.1f} ms · max {max(lat):.1f} ms"
        )
        ans = Assistant(store, a.vehicle).ask("How far did I drive today?")
        print(f"query while live → {ans.text}  [{ans.timings_ms['total']:.0f} ms]")
        store.close()


if __name__ == "__main__":
    main()
