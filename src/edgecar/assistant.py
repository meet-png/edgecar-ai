"""The assistant: question → typed query (rules or SLM) → deterministic analytics → grounded answer."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime

from edgecar.analytics import Engine, Result
from edgecar.nlu import command
from edgecar.nlu.pipeline import understand
from edgecar.nlu.slm import SlmParser
from edgecar.query import Query
from edgecar.store import Store


@dataclass
class Answer:
    question: str
    text: str
    path: str  # "fast" | "slm" | "unsupported" (model declined) | "unparsed"
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


class Assistant:
    def __init__(self, store: Store, vehicle_id: str, slm: SlmParser | None = None, now: datetime | None = None):
        self.engine = Engine(store, vehicle_id, now)
        self.slm = slm

    def ask(self, question: str) -> Answer:
        t0 = time.perf_counter()
        u = understand(question, self.slm)
        timings = dict(u.timings_ms)
        if u.query is None:
            timings["total"] = (time.perf_counter() - t0) * 1000
            return Answer(
                question, UNPARSED_REPLY, u.path, timings_ms=timings, slm_tokens=u.tokens, model_line=u.model_line
            )
        t1 = time.perf_counter()
        result = self.engine.run(u.query)
        timings["engine"] = (time.perf_counter() - t1) * 1000
        timings["total"] = (time.perf_counter() - t0) * 1000
        text = " ".join([*result.notes, result.headline, *result.details])
        return Answer(question, text, u.path, command.render(u.query), u.query, result, timings, u.tokens, u.model_line)
