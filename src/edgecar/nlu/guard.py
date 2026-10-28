"""Deterministic checks that let the assistant decline instead of answering a different question.

* ``action_request`` — the question asks the car to *do* something ("turn off lane assist", "clear the fault codes")
  or tries to override the instructions. EdgeCar cannot act: the model can only write a read-only command and the AI
  zone has no transmit path. A clear refusal is the honest answer, not a data listing that shares a keyword.
* ``not_recorded`` — the question is about something the vehicle data does not contain (tyre pressure, video, …).
* ``grounded`` — a model command must be supported by the question's own words. A 1.5B model under a grammar
  always writes *some* command; a metric or event the question never mentions is a guess, and is declined.

The word lists come from the signal catalogue and the metric/event definitions, not from evaluation questions.
"""

from __future__ import annotations

import re


def _stem(word: str) -> str:
    """Drop a final silent "e" so inflections match too ("drive" → driving, driven; "cruise" → cruising)."""
    return word[:-1] if len(word) >= 5 and word.endswith("e") and " " not in word else word


def _words(*stems: str) -> re.Pattern:
    """Any of the stems at the start of a word (``consum`` matches consumption, consuming, …)."""
    stems = tuple(_stem(s) for s in stems)
    # escape word by word: re.escape also escapes spaces, so escaping the whole phrase and then swapping " " for
    # \s+ would leave a literal backslash in every multi-word cue, which then never matches
    return re.compile(r"\b(?:" + "|".join(r"\s+".join(map(re.escape, s.split())) for s in stems) + ")")


def _norm(question: str) -> str:
    return " ".join(question.lower().split())


# --------------------------------------------------------------------------------------------- action requests

# polite openings before the verb: "please", "hey car,", "could you", "I want you to", …
_PREFIX = (
    r"^(?:(?:please|pls|hey|ok|okay|now|car|so)\b[\s,]*)*"
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|i\s+(?:want|need)\s+you\s+to\s+|go\s+ahead\s+and\s+|kindly\s+)?"
)
_ALWAYS = "|".join(
    [
        r"turn\s+(?:\w+\s+)?(?:on|off|up|down)",
        r"switch\s+(?:\w+\s+)?(?:on|off)",
        r"shut\s+(?:off|down)",
        r"power\s+(?:on|off)",
        *[
            "disable",
            "enable",
            "activate",
            "deactivate",
            "unlock",
            "lock",
            "reset",
            "erase",
            "delete",
            "wipe",
            "flash",
            "reboot",
            "restart",
            "honk",
            "unplug",
        ],
        *["precondition", "disengage", "engage", "override", "install", "calibrate", "recalibrate", "defrost"],
    ]
)
# verbs that also start ordinary questions ("lower efficiency?") count only with an object or particle after them
_WITH_OBJECT = "|".join(
    [
        "open",
        "close",
        "start",
        "stop",
        "set",
        "raise",
        "lower",
        "increase",
        "decrease",
        "change",
        "adjust",
        "clear",
        "charge",
        "heat",
        "cool",
        "limit",
        "apply",
        "release",
        "roll",
        "fold",
        "update",
        "send",
        "call",
        "play",
        "book",
        "order",
        "drive",
        "take",
        "navigate",
    ]
)
_OBJECT = "|".join(
    [
        "the",
        "my",
        "a",
        "an",
        "all",
        "it",
        "this",
        "that",
        "these",
        "those",
        "every",
        "any",
        "on",
        "off",
        "up",
        "down",
        "to",
        "me",
        "charging",
        "climate",
        "heating",
        "cooling",
        "engine",
        "car",
        "vehicle",
        "doors?",
        "windows?",
        "trunk",
        "boot",
        "horn",
    ]
)
_CONTROL = re.compile(_PREFIX + rf"(?:(?:{_ALWAYS})\b|(?:{_WITH_OBJECT})\s+(?:{_OBJECT})\b)")
_INJECTION = re.compile(
    r"\b(?:ignore|disregard|forget|override)\b.{0,40}\b(?:instruction|rule|prompt|previous|above|guideline)s?\b"
    r"|\b(?:system\s+prompt|developer\s+mode|jailbreak|hidden\s+instructions|instructions\s+above|your\s+instructions)\b"
    r"|^\s*(?:system|assistant|developer)\s*:|\bpretend\s+(?:you\s+are|to\s+be)\b|\byou\s+are\s+now\b"
    r"|\bi\s+authori[sz]e\s+you\b|\bnew\s+(?:system\s+)?(?:prompt|instructions)\b"
)


def action_request(question: str) -> bool:
    q = _norm(question)
    return bool(_CONTROL.search(q) or _INJECTION.search(q))


# --------------------------------------------------------------------------------------------- not recorded

# Things drivers ask about that this vehicle's data does not contain (outside the signal catalogue).
NOT_RECORDED: dict[str, re.Pattern] = {
    "tyre pressure or tyre wear": _words("tyre pressure", "tire pressure", "tread", "tpms"),
    "brake-pad wear": _words("brake pad", "brake wear", "pad wear"),
    "oil or fluid levels": _words("oil", "coolant level", "washer fluid", "brake fluid", "fluid level"),
    "camera video": _words("dashcam", "dash cam", "footage", "video", "recording of"),
    "wipers, lights or doors": _words("wiper", "headlight", "turn signal", "blinker", "door open", "window open"),
    "media, calls or messages": _words(
        "music", "song", "radio", "playlist", "podcast", "phone call", "text message", "sms", "whatsapp"
    ),
    "who was driving": _words("who was driving", "who drove", "which driver"),
}


def not_recorded(question: str) -> str | None:
    q = _norm(question)
    return next((what for what, pattern in NOT_RECORDED.items() if pattern.search(q)), None)


# --------------------------------------------------------------------------------------------------- grounding

_SLOT_CUES: dict[str, re.Pattern] = {
    # metrics
    "energy_consumption": _words(
        "energy",
        "consum",
        "kwh",
        "power",
        "electric",
        "efficien",
        "battery",
        "drain",
        "burn",
        "use per",
        "using more",
        "usage",
    ),
    "energy_efficiency": _words(
        "efficien", "km per kwh", "km/kwh", "economy", "mileage", "energy", "consum", "battery", "range"
    ),
    "range": _words("range", "how far", "battery", "last as long", "km left", "distance left", "autonomy"),
    "fuel_consumption": _words(
        "fuel", "petrol", "gas", "diesel", "litre", "liter", "mpg", "economy", "mileage", "consum"
    ),
    "fuel_efficiency": _words(
        "fuel", "petrol", "gas", "diesel", "litre", "liter", "mpg", "economy", "mileage", "efficien"
    ),
    "distance": _words("distance", "far", "km", "kilomet", "mile", "drove", "driven", "drive", "travel"),
    "energy_used": _words("energy", "kwh", "electric", "power", "battery", "consum", "use", "used"),
    "hvac_energy": _words("heat", "a/c", "air con", "air-con", "aircon", "climate", "hvac", "cabin", "cool", "defrost"),
    "regen_energy": _words("regen", "recuperat", "recover", "braking energy"),
    "speed": _words("speed", "fast", "km/h", "kmh", "mph", "velocity", "slow"),
    "battery_temp": _words("temp", "hot", "heat", "warm", "cold", "thermal", "overheat", "degree"),
    "soc": _words("battery level", "charge", "soc", "percent", "battery"),
    "ambient_temp": _words(
        "outside", "ambient", "weather", "temp", "cold", "warm", "hot", "freez", "degree", "winter", "summer"
    ),
    "hard_brakes": _words("brak", "harsh stop", "emergency stop", "slam"),
    "trip_count": _words("trip", "journey", "drive", "drove", "outing"),
    # events
    "adas_disengagement": _words(
        "adas",
        "lane",
        "cruise",
        "autopilot",
        "assist",
        "disengag",
        "switch off",
        "switched off",
        "turned off",
        "drop out",
        "dropped out",
        "takeover",
        "take over",
    ),
    "adas_takeover_request": _words(
        "take over", "takeover", "take-over", "hand back", "handover", "hand over", "asked me", "request"
    ),
    "adas_any": _words("adas", "lane", "cruise", "autopilot", "assist", "self-driving", "driver assistance"),
    "battery_overheat_warning": _words(
        "overheat", "over-temp", "over temp", "too hot", "thermal", "battery temp", "hot battery", "battery warning"
    ),
    "dtc": _words(
        "fault", "code", "dtc", "error", "trouble", "diagnos", "check engine", "warning light", "malfunction"
    ),
    "thermal_any": _words("thermal", "temp", "overheat", "hot", "heat"),
    "charging": _words("charg", "plug", "top up", "topped up"),
    "hard_brake": _words("brak", "harsh stop", "emergency stop", "slam"),
    "system": _words("system", "log", "software", "reboot", "crash", "error"),
    "any": _words(
        "event", "happen", "logged", "log", "anything", "everything", "all", "going on", "occur", "timeline", "sequence"
    ),
}
_VERB_CUES: dict[str, re.Pattern] = {
    "anomalies": _words(
        "unusual",
        "abnormal",
        "strange",
        "weird",
        "wrong",
        "odd",
        "anomal",
        "problem",
        "issue",
        "outlier",
        "off with",
        "normal",
        "worr",
        "concern",
        "attention",
        "healthy",
        "alarm",
    ),
    "status": _words(
        "now", "current", "at the moment", "left", "remaining", "status", "battery level", "state of charge", "doing"
    ),
}


def grounded(question: str, fields: dict) -> bool:
    """Does the question mention what the model's command is about?"""
    q = _norm(question)
    if (verb := _VERB_CUES.get(fields["intent"])) is not None and not verb.search(q):
        return False
    slot = fields.get("event") or fields.get("metric")
    return slot is None or slot not in _SLOT_CUES or bool(_SLOT_CUES[slot].search(q))
