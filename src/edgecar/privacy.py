"""Who may see what. Answers are redacted per role *after* analytics, so the same deterministic numbers serve
everyone and only the exposure differs.

=============  ========  ==================  ====================  =========================================
Role           Location  Driving behaviour   Diagnostics & logs    Time resolution
=============  ========  ==================  ====================  =========================================
driver         yes       yes                 yes                   to the second
service        no        no                  yes (full detail)     to the second
fleet          no        aggregates only     codes, no raw logs    to the hour
=============  ========  ==================  ====================  =========================================

"Driving behaviour" means individual hard-braking events and takeover reaction times — personal data under
GDPR / India's DPDP Act. Fleet operators get counts and rates, not per-event detail, unless the driver consents.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from enum import StrEnum

from edgecar.analytics import Result


class Role(StrEnum):
    DRIVER = "driver"
    SERVICE = "service"
    FLEET = "fleet"


@dataclass(frozen=True)
class Policy:
    location: bool
    behaviour: bool
    raw_logs: bool
    hour_resolution: bool


POLICIES = {
    Role.DRIVER: Policy(location=True, behaviour=True, raw_logs=True, hour_resolution=False),
    Role.SERVICE: Policy(location=False, behaviour=False, raw_logs=True, hour_resolution=False),
    Role.FLEET: Policy(location=False, behaviour=False, raw_logs=False, hour_resolution=True),
}

BEHAVIOUR_CODES = (
    "HARD_BRAKE",
    "ADAS_DISENGAGE_TAKEOVER",
    "ADAS_DISENGAGE_TAKEOVER_LATE",
    "ADAS_DISENGAGE_DRIVER_BRAKE",
)
_CLOCK = re.compile(r"\b(\d{2}):\d{2}:\d{2}\b")
_REACTION = re.compile(r"responded after \d+\.\d+ s")
_LATLON = re.compile(r"\b-?\d{1,3}\.\d{4,}\s*,\s*-?\d{1,3}\.\d{4,}\b")
_RAW_LOG_QUOTE = re.compile(r" \([a-z_]+: [^)]*\)")  # a quoted raw log line, e.g. " (thermal_mgr: pwm_fb=0 …)"


def redact(result: Result, role: Role | str, consent_behaviour: bool = False) -> Result:
    if Role(role) == Role.DRIVER and not consent_behaviour:
        return result  # the owner sees everything: nothing to redact, nothing to copy
    policy = POLICIES[Role(role)]
    # every step below rebinds a field instead of mutating it, so a shallow copy keeps the original intact
    r = dataclasses.replace(result)
    hide_behaviour = not policy.behaviour and not consent_behaviour

    def line(text: str) -> str:
        if not policy.location:
            text = _LATLON.sub("[location withheld]", text)
        if hide_behaviour:
            text = _REACTION.sub("responded [withheld]", text)
        if policy.hour_resolution:
            text = _CLOCK.sub(r"\1:xx", text)
        return text

    if r.table and hide_behaviour:
        r.table = [row for row in r.table if row.get("code") not in BEHAVIOUR_CODES]
    if r.table and not policy.raw_logs:
        r.table = [{k: v for k, v in row.items() if k not in ("detail",)} for row in r.table]
    if r.table and policy.hour_resolution:
        r.table = [{k: (line(v) if isinstance(v, str) else v) for k, v in row.items()} for row in r.table]
    if hide_behaviour and r.intent == "list_events":
        kept = []
        for d in r.details:
            if any(word in d for word in ("Hard braking", "driver braked", "responded after")):
                continue
            kept.append(d)
        r.details = kept
    r.headline = line(r.headline)
    r.details = [line(d) for d in r.details]
    r.facts = {k: v for k, v in r.facts.items() if policy.location or k not in ("lat", "lon", "location")}
    if not policy.raw_logs:
        r.details = [_RAW_LOG_QUOTE.sub("", d) for d in r.details if "(QNX slog2)" not in d]
    r.notes = [*r.notes, f"Shown with the {Role(role).value} privacy policy."] if Role(role) != Role.DRIVER else r.notes
    return r
