"""QNX system logs in ``slog2info`` text form.

Line layout (one record per line)::

    Jul 24 13:11:00.412  thermal_mgr.412  pump_a  3  pwm_fb=0 duty=62% flow=0.0 l/min -> FAULT

date/time, ``process.pid``, buffer name, slog2 severity (0 SHUTDOWN … 7 DEBUG2), message. ``slog2info`` prints no
year, so the reader takes it from the log's context (here: the trip date).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

SEVERITY = {0: "SHUTDOWN", 1: "CRITICAL", 2: "ERROR", 3: "WARNING", 4: "NOTICE", 5: "INFO", 6: "DEBUG1", 7: "DEBUG2"}

_LINE = re.compile(
    r"^(?P<ts>\w{3} \d{2} \d{2}:\d{2}:\d{2}\.\d{3})\s+(?P<proc>[\w-]+)\.(?P<pid>\d+)\s+(?P<buffer>\S+)\s+"
    r"(?P<sev>[0-7])\s+(?P<msg>.*)$"
)


@dataclass(frozen=True)
class LogRecord:
    ts: datetime
    process: str
    pid: int
    buffer: str
    severity: int
    message: str


def format_record(r: LogRecord) -> str:
    stamp = f"{r.ts:%b %d %H:%M:%S}.{r.ts.microsecond // 1000:03d}"
    return f"{stamp}  {r.process}.{r.pid}  {r.buffer}  {r.severity}  {r.message}"


def parse_line(line: str, year: int) -> LogRecord | None:
    m = _LINE.match(line.rstrip("\n"))
    if not m:
        return None
    ts = datetime.strptime(f"{year} {m['ts']}", "%Y %b %d %H:%M:%S.%f")
    return LogRecord(ts, m["proc"], int(m["pid"]), m["buffer"], int(m["sev"]), m["msg"])
