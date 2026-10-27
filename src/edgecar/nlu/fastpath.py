"""Deterministic fast path: rules that parse the common question shapes without a model.

The rules return a confidence. A question is answered on the fast path only when the intent is recognised *and*
every slot that intent needs is filled; otherwise it goes to the language model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from edgecar.query import Query, TimeWindow

# Keys are in priority order: the first key with a matching phrase wins, so "fuel efficiency" is checked before
# "efficiency" and "range" before "heating" ("why is my range lower — is it the heating?").
METRIC_PHRASES: dict[str, tuple[str, ...]] = {
    "fuel_efficiency": ("fuel efficiency", "fuel economy", "mileage", "mpg", "km/l", "km per litre", "km per liter"),
    "fuel_consumption": (
        "fuel consumption",
        "fuel use",
        "fuel usage",
        "l/100",
        "litres per 100",
        "liters per 100",
        "petrol consumption",
        "gas consumption",
        "petrol use",
    ),
    "range": ("driving range", "range", "how far can i", "km left", "miles left", "kilometres left"),
    "energy_efficiency": ("energy efficiency", "efficiency", "km/kwh", "km per kwh", "efficient"),
    "energy_consumption": (
        "energy consumption",
        "power consumption",
        "energy usage",
        "energy use",
        "consumption",
        "kwh/100",
        "kwh per 100",
        "consum",
    ),
    "battery_temp": (
        "battery temperature",
        "battery temp",
        "pack temperature",
        "battery heat",
        "battery hot",
        "cell temperature",
        "battery get hot",
        "battery get warm",
        "battery warm",
        "battery overheat",
    ),
    "hvac_energy": ("heating", "heater", "air conditioning", "a/c", "climate control", "hvac"),
    "regen_energy": ("regenerative", "regen", "recovered energy", "energy recovered"),
    "hard_brakes": ("hard brak", "harsh brak", "sudden brak", "emergency brak", "braked hard"),
    "trip_count": ("how many trips", "number of trips", "trips did i"),
    "energy_used": ("energy used", "kwh used", "how much energy", "kwh did"),
    "soc": ("battery level", "state of charge", "charge level", "battery percentage", "soc", "how much charge"),
    "ambient_temp": ("outside temperature", "ambient temperature", "how cold", "how hot", "weather"),
    "speed": ("average speed", "how fast", "speed"),
    "distance": ("how far did", "distance", "kilometres", "kilometers", "how many km", "how many miles", "driven"),
}

EVENT_PHRASES: dict[str, tuple[str, ...]] = {
    "battery_overheat_warning": (
        "overheat",
        "over-heat",
        "over temperature",
        "over-temperature",
        "overtemperature",
        "too hot",
        "battery temperature warning",
    ),
    "adas_takeover_request": ("takeover request", "take-over request", "take over request", "takeover warning"),
    "adas_disengagement": (
        "disengag",
        "dropped out",
        "turned itself off",
        "switched off",
        "switch off",
        "turned off",
        "turn off",
        "cut out",
        "shut off",
    ),
    "dtc": ("dtc", "trouble code", "fault code", "error code", "diagnostic", "check engine", "malfunction"),
    "charging": ("charging", "charge session", "charged", "plugged in"),
    "hard_brake": ("hard brak", "harsh brak", "sudden brak", "emergency brak"),
    "adas_any": ("adas", "driver assist", "lane keep", "adaptive cruise", "autopilot", "cruise control"),
    "thermal_any": ("thermal", "temperature warning", "temperature alert", "heat warning"),
    "system": ("system log", "qnx", "reboot", "software error", "crash"),
}

_NUM_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "couple of": 2,
    "couple": 2,
    "few": 3,
    "several": 3,
}
_UNIT_DAYS = {"day": 1, "week": 7, "fortnight": 14, "month": 30, "year": 365}

_LAST_N = re.compile(
    r"\b(?:last|past|previous|recent)\s+(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|couple of|couple|"
    r"few|several)\s+(day|week|fortnight|month|year)s?\b"
)


def parse_window(text: str) -> TimeWindow | None:
    if m := _LAST_N.search(text):
        n = int(m.group(1)) if m.group(1).isdigit() else _NUM_WORDS[m.group(1)]
        return TimeWindow(kind="last_days", days=min(366, n * _UNIT_DAYS[m.group(2)]))
    if re.search(r"\b(?:last|past) (?:fortnight|two weeks)\b", text):
        return TimeWindow(kind="last_days", days=14)
    # "in the last month" is a rolling 30 days; a bare "last month" is the previous calendar month.
    if m := re.search(r"\b(?:in|over|during|for|within|throughout) the (?:last|past) (day|week|month|year)\b", text):
        return TimeWindow(kind="last_days", days=_UNIT_DAYS[m.group(1)])
    if m := re.search(r"\b(?:past|last) (year|12 months)\b", text):
        return TimeWindow(kind="last_days", days=365)
    rules = [
        (r"\b(?:today|this morning|this afternoon|this evening|tonight)\b", "today"),
        (r"\byesterday\b", "yesterday"),
        (r"\bthis week\b", "this_week"),
        (r"\b(?:last|previous) week\b", "last_week"),
        (r"\bthis month\b", "this_month"),
        (r"\b(?:last|previous) month\b", "last_month"),
        (
            r"\b(?:ever|all time|all-time|overall|so far|since (?:i|we) (?:bought|got)|whole history|entire history|"
            r"all (?:my )?(?:history|data|trips))\b",
            "all",
        ),
    ]
    for pattern, kind in rules:
        if re.search(pattern, text):
            return TimeWindow(kind=kind)
    if m := re.search(r"\b(?:last|past) (day|week|month)\b", text):
        return TimeWindow(kind="last_days", days=_UNIT_DAYS[m.group(1)])
    return None


def _first(text: str, table: dict[str, tuple[str, ...]]) -> str | None:
    """First key (in table order = priority) with a phrase starting at a word boundary in ``text``.

    Phrases may be stems ("consum", "disengag"), so only the start is anchored.
    """
    for key, phrases in table.items():
        if any(re.search(r"\b" + re.escape(p), text) for p in phrases):
            return key
    return None


# Default time window per intent when the question gives none.
DEFAULT_WINDOW = {
    "explain_change": TimeWindow(kind="last_days", days=14),
    "extreme": TimeWindow(kind="all"),
    "compare": TimeWindow(kind="all"),
    "list_events": TimeWindow(kind="last_days", days=7),
    "events_before": TimeWindow(kind="all"),
    "anomalies": TimeWindow(kind="last_days", days=30),
    "trend": TimeWindow(kind="last_days", days=30),
    "summary": TimeWindow(kind="last_days", days=7),
    "status": TimeWindow(kind="today"),
}

_DROP = r"(?:drop|dropp|declin|decreas|reduc|fell|fall|lower|worse|worst|deteriorat|went down|shrunk|shrink|less)"
_RISE = r"(?:rise|rose|increas|went up|higher|grew|jump|spike|more)"


@dataclass
class FastResult:
    query: Query | None
    confidence: float
    rule: str
    missing: list[str] = field(default_factory=list)


def parse(question: str) -> FastResult:
    t = " " + re.sub(r"\s+", " ", question.lower()).strip() + " "
    metric = _first(t, METRIC_PHRASES)
    if re.search(r"\bbattery\b", t) and re.search(r"\b(?:hot|hotter|hottest|warm|warmer|heat|temp)", t):
        metric = "battery_temp"  # "how hot did the battery get" — word order defeats the phrase table
    event = _first(t, EVENT_PHRASES)
    window = parse_window(t)

    intent, rule = None, ""
    if event and re.search(
        r"\b(?:before|prior to|leading up to|led (?:up )?to|lead (?:up )?to|preceded|precede|"
        r"caused|triggered|leading to)\b",
        t,
    ):
        intent, rule = "events_before", "before+event"
    elif re.search(r"\bwhy\b|\bwhat(?:'s| is)? caus|\breason\b|\bexplain\b|\bhow come\b", t) and metric:
        intent, rule = "explain_change", "why+metric"
    elif re.search(r"\b(?:when|which day|what day|which trip|what date)\b", t) and re.search(
        r"\b(?:largest|biggest|most|worst|best|highest|lowest|peak|max(?:imum)?|min(?:imum)?|steepest|sharpest)\b", t
    ):
        intent, rule = "extreme", "when+superlative"
    elif re.search(
        r"\bcompare\b|\bcomparison\b|\bvs\.?\b|\bversus\b|\bdifference between\b|\bcompared\b|"
        r"\b(?:city|urban|town) (?:and|or|vs) (?:highway|motorway|freeway)\b|"
        r"\b(?:highway|motorway|freeway) (?:and|or|vs) (?:city|urban|town)\b|"
        r"\bwhen (?:it'?s|it is) (?:freezing|cold|hot|warm)\b|\bin (?:the )?(?:cold|heat|winter|summer)\b",
        t,
    ):
        intent, rule = "compare", "compare"
    elif re.search(
        r"\banomal|\bunusual\b|\babnormal\b|\bstrange\b|\bweird\b|\boutlier|\banything wrong\b|"
        r"\bany (?:issues|problems)\b|\bsomething wrong\b",
        t,
    ):
        intent, rule = "anomalies", "anomaly-words"
    elif event and re.search(
        r"\b(?:show|list|any|all|were there|was there|did (?:i|we|it|the car) (?:have|get)|"
        r"how many|give me|display|find|what)\b",
        t,
    ):
        intent, rule = "list_events", "list+event"
    elif metric and re.search(
        r"\btrend\b|\bover time\b|\bhow (?:has|did|have) .* (?:change|evolve|develop)|"
        r"\bgraph\b|\bplot\b|\bchart\b|\bhistory of\b|\bprogress",
        t,
    ):
        intent, rule = "trend", "trend-words"
    elif not window and (
        re.search(r"\b(?:right now|currently|at the moment)\b", t)
        or re.search(r"\bcurrent (?:state|status|battery|charge|range|soc)\b", t)
        or re.search(r"\b(?:what(?:'s| is)|how much) (?:my |the )?(?:battery|charge|range|soc)\b(?! (?:temp|curr))", t)
    ):
        intent, rule = "status", "status-words"
    elif metric and re.search(
        r"\bhow (?:much|far|many|fast|hot|cold)\b|\btotal\b|\baverage\b|\bmean\b|"
        r"\bwhat (?:was|is|were) (?:my|the)\b",
        t,
    ):
        intent, rule = "summary", "summary-words"

    if intent is None:
        return FastResult(None, 0.0, "no-intent")

    fields: dict = {"intent": intent, "window": window or DEFAULT_WINDOW[intent]}
    missing: list[str] = []
    if intent in ("explain_change", "extreme", "trend", "summary"):
        fields["metric"] = metric
        if not metric:
            missing.append("metric")
    if intent == "compare":
        fields["metric"] = (
            metric
            if metric in ("energy_consumption", "energy_efficiency", "fuel_consumption", "fuel_efficiency")
            else "energy_consumption"
        )
        if re.search(r"\b(?:city|urban|town|highway|motorway|freeway|road type)\b", t):
            fields["compare_by"] = "road_type"
        elif re.search(r"\b(?:cold|freezing|warm|hot|heat|temperature|winter|summer|weather|season)\b", t):
            fields["compare_by"] = "temperature"
        elif re.search(r"\b(?:last|previous|before|earlier) (?:week|month|period)\b|\bthan before\b", t):
            fields["compare_by"] = "previous_period"
        else:
            missing.append("compare_by")
    if intent == "extreme":
        if re.search(_DROP, t):
            fields["direction"] = "drop"
        elif re.search(_RISE, t):
            fields["direction"] = "rise"
        elif re.search(r"\b(?:highest|peak|max|maximum|best)\b", t):
            fields["direction"] = "max"
        elif re.search(r"\b(?:lowest|min|minimum)\b", t):
            fields["direction"] = "min"
        else:
            missing.append("direction")
    if intent == "explain_change" and fields.get("metric") not in (
        "range",
        "energy_consumption",
        "energy_efficiency",
        "fuel_consumption",
        "fuel_efficiency",
    ):
        missing.append("metric")
    if intent in ("list_events", "events_before"):
        fields["event"] = event or "any"
        if not event:
            missing.append("event")
    if missing:
        return FastResult(None, 0.3, rule, missing)
    return FastResult(Query(**fields), 0.9, rule)
