"""The assistant: question → typed query (rules or SLM) → deterministic analytics → grounded answer."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime

from edgecar.analytics import Engine, Result
from edgecar.nlu import command
from edgecar.nlu.pipeline import understand
from edgecar.nlu.slm import SlmParser
from edgecar.privacy import Role, redact
from edgecar.query import Query
from edgecar.store import Store


@dataclass
class Answer:
    question: str
    text: str
    path: str  # "fast" | "slm" | declined: "refused" | "unavailable" | "unsupported" | "unparsed"
    command: str | None = None
    query: Query | None = None
    result: Result | None = None
    timings_ms: dict[str, float] = field(default_factory=dict)
    slm_tokens: dict[str, int] = field(default_factory=dict)
    model_line: str | None = None


UNPARSED_REPLY = (
    "I can only answer questions about this vehicle's recorded data — range, energy or fuel use, battery temperature, "
    "charging, ADAS events or fault codes. For example: “Why has my range dropped over the last two weeks?”"
)
REFUSED_REPLY = (
    "I can't control the car or change its settings: I only read its recorded data and have no way to send commands "
    "to the vehicle. I can tell you what happened — for example, which fault codes are stored."
)
UNAVAILABLE_REPLY = (
    "This car's data doesn't include {what}, so I can't answer that. I can help with range, energy or fuel use, "
    "battery temperature, charging, ADAS events and fault codes."
)


class Assistant:
    def __init__(
        self,
        store: Store,
        vehicle_id: str,
        slm: SlmParser | None = None,
        now: datetime | None = None,
        role: Role | str = Role.DRIVER,
    ):
        self.engine = Engine(store, vehicle_id, now)
        self.slm = slm
        self.role = Role(role)

    def ask(self, question: str) -> Answer:
        t0 = time.perf_counter()
        u = understand(question, self.slm)
        timings = dict(u.timings_ms)
        if u.query is None:
            timings["total"] = (time.perf_counter() - t0) * 1000
            reply = {"refused": REFUSED_REPLY, "unavailable": UNAVAILABLE_REPLY.format(what=u.note)}
            return Answer(
                question,
                reply.get(u.path, UNPARSED_REPLY),
                u.path,
                timings_ms=timings,
                slm_tokens=u.tokens,
                model_line=u.model_line,
            )
        t1 = time.perf_counter()
        result = redact(self.engine.run(u.query), self.role)
        timings["engine"] = (time.perf_counter() - t1) * 1000
        timings["total"] = (time.perf_counter() - t0) * 1000
        text = " ".join([*result.notes, result.headline, *result.details])
        return Answer(question, text, u.path, command.render(u.query), u.query, result, timings, u.tokens, u.model_line)
