"""Question → ``Query``: rules first, then the model for meaning, with deterministic time extraction.

Shared by the assistant and the evaluation harness, so what is measured is exactly what runs.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from edgecar.nlu import command, fastpath, guard
from edgecar.nlu.slm import SlmParser
from edgecar.query import Query

_LOOKBACK = re.compile(r"\b(\d{1,3})\s*(?:min|mins|minute|minutes)\b")


@dataclass
class Understanding:
    query: Query | None
    # "fast" | "slm" | declined: "refused" (asked to act) · "unavailable" (not in the vehicle data) ·
    # "unsupported" (the model declined, or its command was not grounded in the question) · "unparsed"
    path: str
    model_line: str | None = None
    timings_ms: dict[str, float] = field(default_factory=dict)
    tokens: dict[str, int] = field(default_factory=dict)
    note: str | None = None  # what is not recorded, for "unavailable"


def complete(fields: dict, question: str) -> Query:
    """Add the slots the model never writes: time window and look-back, both read deterministically."""
    text = " " + re.sub(r"\s+", " ", question.lower()).strip() + " "
    window = fastpath.parse_window(text) or fastpath.DEFAULT_WINDOW[fields["intent"]]
    extra: dict = {"window": window}
    if fields["intent"] == "events_before" and (m := _LOOKBACK.search(text)):
        extra["lookback_min"] = int(m.group(1))
    return Query(**fields, **extra)


def understand(question: str, slm: SlmParser | None, *, use_fast: bool = True, use_guard: bool = True) -> Understanding:
    """``use_guard=False`` switches the decline checks off, before and after the model — for ablations only."""
    t0 = time.perf_counter()
    if use_guard and guard.action_request(question):
        return Understanding(None, "refused", timings_ms={"fastpath": (time.perf_counter() - t0) * 1000})
    if use_guard and (missing := guard.not_recorded(question)) is not None:
        return Understanding(
            None, "unavailable", timings_ms={"fastpath": (time.perf_counter() - t0) * 1000}, note=missing
        )
    fast = fastpath.parse(question) if use_fast else fastpath.FastResult(None, 0.0, "disabled")
    timings = {"fastpath": (time.perf_counter() - t0) * 1000}
    if fast.query is not None:
        q = fast.query
        if q.intent == "events_before" and (m := _LOOKBACK.search(question.lower())):
            q = q.model_copy(update={"lookback_min": int(m.group(1))})
        return Understanding(q, "fast", timings_ms=timings)
    if slm is None:
        return Understanding(None, "unparsed", timings_ms=timings)
    out = slm.parse(question)
    timings |= {"slm_total": out.total_ms, "slm_prompt": out.prompt_ms, "slm_generate": out.generate_ms}
    tokens = {"prompt": out.prompt_tokens, "cached": out.cached_tokens, "generated": out.generated_tokens}
    try:
        fields = command.parse_model(out.line)
    except ValueError:
        return Understanding(None, "unparsed", out.line, timings, tokens)
    if fields is None or (use_guard and not guard.grounded(question, fields)):
        return Understanding(None, "unsupported", out.line, timings, tokens)
    return Understanding(complete(fields, question), "slm", out.line, timings, tokens)
