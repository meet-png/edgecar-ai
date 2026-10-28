"""Sample a cgroup's memory every 200 ms until stopped; write the peaks and the limit counters as JSON.

    python scripts/ci/cgroup_sampler.py /sys/fs/cgroup/<scope> out.json

``memory.peak`` is the kernel's own high-water mark (anonymous memory + page cache + kernel memory). The sampler
adds the split, because page cache — for example the memory-mapped model weights — can be reclaimed under the
limit, while anonymous memory cannot; and the whole machine's use (MemTotal − MemAvailable), operating system
included, which is the number that matters on a real 2 GiB device.
"""

import json
import signal
import sys
import time
from pathlib import Path

cg, out = Path(sys.argv[1]), Path(sys.argv[2])
running = True


def stop(*_):
    global running
    running = False


signal.signal(signal.SIGTERM, stop)


def read(name: str) -> str:
    return (cg / name).read_text().strip()


def stat() -> dict[str, int]:
    return {k: int(v) for k, v in (line.split() for line in read("memory.stat").splitlines())}


def meminfo() -> dict[str, int]:
    return {line.split(":")[0]: int(line.split()[1]) * 1024 for line in Path("/proc/meminfo").read_text().splitlines()}


peak = {"current": 0, "anon": 0, "file": 0}
system_peak = 0
while running:
    s = stat()
    m = meminfo()
    system_peak = max(system_peak, m["MemTotal"] - m["MemAvailable"])
    peak = {
        "current": max(peak["current"], int(read("memory.current"))),
        "anon": max(peak["anon"], s["anon"]),
        "file": max(peak["file"], s["file"]),
    }
    time.sleep(0.2)

limit = read("memory.max")
report = {
    "memory_max_mb": None if limit == "max" else round(int(limit) / 2**20),
    "memory_peak_mb": round(int(read("memory.peak")) / 2**20) if (cg / "memory.peak").exists() else None,
    "sampled_peak_mb": {k: round(v / 2**20) for k, v in peak.items()},
    "memory_events": {k: int(v) for k, v in (line.split() for line in read("memory.events").splitlines())},
    "cpus": read("cpuset.cpus.effective") if (cg / "cpuset.cpus.effective").exists() else None,
    "system_mem_total_mb": round(meminfo()["MemTotal"] / 2**20),
    "system_used_peak_mb": round(system_peak / 2**20),  # the whole machine, OS included
}
out.write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
