"""The store on a vehicle: sudden power loss and the driver's right to erase their data."""

import subprocess
import sys
import textwrap

from edgecar.store import Store


def test_committed_batches_survive_a_hard_kill(tmp_path):
    """Ignition off without a clean shutdown: every batch committed before the kill is there after a restart."""
    db = tmp_path / "crash.duckdb"
    writer = textwrap.dedent(
        f"""
        import pandas as pd
        from edgecar.store import Store
        s = Store({str(db)!r})
        for i in range(100_000):
            ts = pd.date_range("2024-01-01", periods=60, freq="s") + pd.Timedelta(minutes=i)
            s.append_frames(pd.DataFrame({{"ts": ts, "vehicle_id": "v", "trip_id": "t", "speed_kmh": 1.0}}), "test")
            print(i, flush=True)  # this one-second batch is committed
        """
    )
    p = subprocess.Popen([sys.executable, "-c", writer], stdout=subprocess.PIPE, text=True)
    committed = -1
    for line in p.stdout:
        committed = int(line)
        if committed >= 25:
            break
    p.kill()  # no clean shutdown, no checkpoint
    p.wait()
    s = Store(db)  # opening replays the write-ahead log
    assert s.sql("SELECT count(*) AS n FROM frames")["n"][0] >= 60 * (committed + 1)


def test_wipe_erases_everything_about_the_vehicle(store):
    store.rebuild_rollups()
    assert store.wipe("ev-1") > 0
    for table in ("frames", "frames_1min", "trips", "events"):
        assert store.sql(f"SELECT count(*) AS n FROM {table} WHERE vehicle_id = 'ev-1'")["n"][0] == 0
