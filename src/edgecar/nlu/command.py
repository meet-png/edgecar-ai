"""The compact command language the language model writes, and its GBNF grammar.

A query is written as one line, for example::

    explain_change metric=range window=last_14d
    list_events window=yesterday event=adas_disengagement
    compare metric=energy_consumption window=all by=road_type

This is roughly half the tokens of the equivalent JSON, which matters on two ARM cores where every generated
token costs ~80 ms. The grammar is generated from the ``Literal`` types in ``edgecar.query`` and gives every
intent exactly its own slots — ``extreme`` must name a direction, ``list_events`` an event — so the model can only
produce commands the engine can execute. ``unsupported`` lets it decline questions that are not about vehicle data.
"""

from __future__ import annotations

import re
from typing import get_args

from edgecar.query import EventKind, Intent, Metric, Query, TimeWindow

UNSUPPORTED = "unsupported"

INTENTS: tuple[str, ...] = get_args(Intent)
METRICS: tuple[str, ...] = get_args(Metric)
EVENTS: tuple[str, ...] = get_args(EventKind)
COMPARE_BY: tuple[str, ...] = get_args(Query.model_fields["compare_by"].annotation.__args__[0])
DIRECTIONS: tuple[str, ...] = get_args(Query.model_fields["direction"].annotation.__args__[0])
WINDOW_KINDS: tuple[str, ...] = tuple(
    k for k in get_args(TimeWindow.model_fields["kind"].annotation) if k != "last_days"
)
RATIO_METRICS: tuple[str, ...] = ("energy_consumption", "energy_efficiency", "fuel_consumption", "fuel_efficiency")
EXPLAINABLE_METRICS: tuple[str, ...] = ("range", *RATIO_METRICS)

# Slots per intent, in the order they are written. A trailing "?" marks an optional slot.
SLOTS: dict[str, tuple[str, ...]] = {
    "summary": ("metric", "window"),
    "trend": ("metric", "window"),
    "extreme": ("metric", "window", "dir"),
    "compare": ("cmp_metric", "window", "by"),
    "explain_change": ("exp_metric", "window"),
    "list_events": ("window", "event"),
    "events_before": ("window", "event", "lookback?"),
    "anomalies": ("window",),
    "status": (),
}


def _alt(values: tuple[str, ...]) -> str:
    return " | ".join(f'"{v}"' for v in sorted(values, key=len, reverse=True))


def grammar() -> str:
    """GBNF grammar accepted by llama.cpp's ``grammar`` parameter."""
    rules = [f"root ::= {' | '.join(f'{i}-cmd' for i in (*SLOTS, UNSUPPORTED)).replace('_', '-')}"]
    for intent, slots in SLOTS.items():
        body = " ".join(f"{s[:-1].replace('_', '-')}?" if s.endswith("?") else s.replace("_", "-") for s in slots)
        rules.append(f'{intent.replace("_", "-")}-cmd ::= "{intent}" {body}'.rstrip())
    rules += [
        f'{UNSUPPORTED}-cmd ::= "{UNSUPPORTED}"',
        f'metric ::= " metric=" ({_alt(METRICS)})',
        f'cmp-metric ::= " metric=" ({_alt(RATIO_METRICS)})',
        f'exp-metric ::= " metric=" ({_alt(EXPLAINABLE_METRICS)})',
        f'window ::= " window=" ({_alt(WINDOW_KINDS)} | "last_" num "d")',
        f'by ::= " by=" ({_alt(COMPARE_BY)})',
        f'dir ::= " dir=" ({_alt(DIRECTIONS)})',
        f'event ::= " event=" ({_alt(EVENTS)})',
        'lookback ::= " lookback=" num "m"',
        "num ::= [1-9] [0-9]? [0-9]?",
    ]
    return "\n".join(rules)


_ARG = re.compile(r"(metric|window|by|dir|event|lookback)=(\S+)")


def parse(line: str) -> Query | None:
    """Parse one command line into a validated ``Query``; ``None`` for ``unsupported``.

    Raises ``ValueError`` on anything malformed.
    """
    parts = line.strip().split(maxsplit=1)
    if parts and parts[0] == UNSUPPORTED:
        return None
    if not parts or parts[0] not in INTENTS:
        raise ValueError(f"unknown intent in {line!r}")
    fields: dict = {"intent": parts[0]}
    for key, value in _ARG.findall(parts[1] if len(parts) > 1 else ""):
        if key == "metric":
            fields["metric"] = value
        elif key == "window":
            m = re.fullmatch(r"last_(\d+)d", value)
            fields["window"] = TimeWindow(kind="last_days", days=int(m.group(1))) if m else TimeWindow(kind=value)
        elif key == "by":
            fields["compare_by"] = value
        elif key == "dir":
            fields["direction"] = value
        elif key == "event":
            fields["event"] = value
        elif key == "lookback":
            fields["lookback_min"] = int(value.rstrip("m"))
    return Query(**fields)


# ---------------------------------------------------------------------------------------------------------------
# Model format. The model only decides *meaning*: a verb aligned with how people ask ("why", "when", "show", …) plus
# the metric/event/direction. Time windows and minute counts are extracted by deterministic code instead, because
# small models are unreliable at arithmetic ("2 months" → last_24d) and copy surface words ("show … events" →
# events_before) when command names overlap the question's wording.
# ---------------------------------------------------------------------------------------------------------------

VERBS: dict[str, str | None] = {
    "total": "summary",
    "trend": "trend",
    "when": "extreme",
    "compare": "compare",
    "why": "explain_change",
    "show": "list_events",
    "before": "events_before",
    "unusual": "anomalies",
    "now": "status",
    "unsupported": None,
}
VERB_OF: dict[str, str] = {intent: verb for verb, intent in VERBS.items() if intent}

# Positional arguments per verb.
VERB_ARGS: dict[str, tuple[str, ...]] = {
    "total": ("metric",),
    "trend": ("metric",),
    "when": ("metric", "dir"),
    "compare": ("cmp-metric", "by"),
    "why": ("exp-metric",),
    "show": ("event",),
    "before": ("event",),
    "unusual": (),
    "now": (),
    "unsupported": (),
}


def model_grammar() -> str:
    rules = [f"root ::= {' | '.join(f'{v}-cmd' for v in VERB_ARGS)}"]
    for verb, args in VERB_ARGS.items():
        rules.append(f'{verb}-cmd ::= "{verb}"' + "".join(f' " " {a}' for a in args))
    rules += [
        f"metric ::= {_alt(METRICS)}",
        f"cmp-metric ::= {_alt(RATIO_METRICS)}",
        f"exp-metric ::= {_alt(EXPLAINABLE_METRICS)}",
        f"by ::= {_alt(COMPARE_BY)}",
        f"dir ::= {_alt(DIRECTIONS)}",
        f"event ::= {_alt(EVENTS)}",
    ]
    return "\n".join(rules)


def parse_model(line: str) -> dict | None:
    """Model line → partial query fields (no window); ``None`` when the model declined.

    Raises ``ValueError`` on anything malformed.
    """
    parts = line.strip().split()
    if not parts or parts[0] not in VERBS:
        raise ValueError(f"unknown verb in {line!r}")
    verb, values = parts[0], parts[1:]
    if VERBS[verb] is None:
        return None
    names = VERB_ARGS[verb]
    if len(values) != len(names):
        raise ValueError(f"{verb!r} takes {len(names)} argument(s): {line!r}")
    key = {
        "metric": "metric",
        "cmp-metric": "metric",
        "exp-metric": "metric",
        "by": "compare_by",
        "dir": "direction",
        "event": "event",
    }
    return {"intent": VERBS[verb], **{key[n]: v for n, v in zip(names, values, strict=True)}}


def render_model(q: Query | None) -> str:
    """Model-format line for a query (used to write few-shot examples)."""
    if q is None:
        return "unsupported"
    verb = VERB_OF[q.intent]
    field = {
        "metric": q.metric,
        "cmp-metric": q.metric,
        "exp-metric": q.metric,
        "by": q.compare_by,
        "dir": q.direction,
        "event": q.event,
    }
    return " ".join([verb, *(field[a] for a in VERB_ARGS[verb])])


def render(q: Query | None) -> str:
    """Inverse of ``parse``: the canonical command line for a query (``unsupported`` for ``None``)."""
    if q is None:
        return UNSUPPORTED
    out = [q.intent]
    if q.metric and q.intent not in ("list_events", "events_before", "anomalies", "status"):
        out.append(f"metric={q.metric}")
    if q.intent != "status":
        w = q.window
        out.append(f"window={f'last_{w.days or 14}d' if w.kind == 'last_days' else w.kind}")
    if q.compare_by:
        out.append(f"by={q.compare_by}")
    if q.direction:
        out.append(f"dir={q.direction}")
    if q.event and q.intent in ("list_events", "events_before"):
        out.append(f"event={q.event}")
    if q.lookback_min and q.intent == "events_before":
        out.append(f"lookback={q.lookback_min}m")
    return " ".join(out)
