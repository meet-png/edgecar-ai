import pytest

from edgecar.nlu import command, fastpath
from edgecar.nlu.pipeline import complete

BRIEF = [
    ("Why has my driving range reduced over the last two weeks?", "explain_change metric=range window=last_14d"),
    ("When did fuel efficiency show the largest drop?", "extreme metric=fuel_efficiency window=all dir=drop"),
    ("Show all ADAS disengagement events from yesterday.", "list_events window=yesterday event=adas_disengagement"),
    (
        "What happened before the battery overheating warning?",
        "events_before window=all event=battery_overheat_warning",
    ),
    (
        "Compare energy consumption between city and highway driving.",
        "compare metric=energy_consumption window=all by=road_type",
    ),
]


@pytest.mark.parametrize(("question", "expected"), BRIEF)
def test_brief_questions_take_the_fast_path(question, expected):
    r = fastpath.parse(question)
    assert r.query is not None
    assert command.render(r.query) == expected


@pytest.mark.parametrize(
    ("text", "kind", "days"),
    [
        ("over the last two weeks", "last_days", 14),
        ("in the last month", "last_days", 30),
        ("last month", "last_month", None),
        ("yesterday", "yesterday", None),
        ("over the past 3 months", "last_days", 90),
        ("over the past year", "last_days", 365),
        ("this week", "this_week", None),
        ("since the start of the month", "this_month", None),
        ("month-to-date", "this_month", None),
    ],
)
def test_time_windows(text, kind, days):
    w = fastpath.parse_window(f" {text} ")
    assert (w.kind, w.days) == (kind, days)


def test_bare_fuel_means_fuel_not_energy():
    r = fastpath.parse("Am I using less fuel this week compared to the previous week?")
    assert command.render(r.query) == "compare metric=fuel_consumption window=this_week by=previous_period"


@pytest.mark.parametrize("q", ["Play some music", "Tell me a joke"])
def test_out_of_scope_does_not_take_the_fast_path(q):
    assert fastpath.parse(q).query is None


@pytest.mark.parametrize(
    "line",
    [
        "explain_change metric=range window=last_14d",
        "extreme metric=fuel_efficiency window=all dir=drop",
        "list_events window=yesterday event=adas_disengagement",
        "events_before window=all event=battery_overheat_warning lookback=10m",
        "compare metric=energy_consumption window=all by=road_type",
        "anomalies window=this_month",
        "status",
    ],
)
def test_command_round_trip(line):
    assert command.render(command.parse(line)) == line


def test_unsupported_round_trip():
    assert command.parse("unsupported") is None
    assert command.render(None) == "unsupported"


def test_model_format():
    assert command.parse_model("why range") == {"intent": "explain_change", "metric": "range"}
    assert command.parse_model("when energy_efficiency drop") == {
        "intent": "extreme",
        "metric": "energy_efficiency",
        "direction": "drop",
    }
    assert command.parse_model("show dtc") == {"intent": "list_events", "event": "dtc"}
    assert command.parse_model("unsupported") is None
    with pytest.raises(ValueError):
        command.parse_model("why")
    with pytest.raises(ValueError):
        command.parse_model("fly to the moon")


def test_model_grammar_covers_the_vocabulary():
    g = command.model_grammar()
    for verb in command.VERBS:
        assert f'"{verb}"' in g
    for metric in command.METRICS:
        assert f'"{metric}"' in g
    for event in command.EVENTS:
        assert f'"{event}"' in g


def test_time_slots_come_from_code_not_the_model():
    q = complete({"intent": "trend", "metric": "range"}, "graph my range over the past 2 months")
    assert (q.window.kind, q.window.days) == ("last_days", 60)
    q = complete({"intent": "events_before", "event": "dtc"}, "what happened 20 minutes before the fault code")
    assert q.lookback_min == 20


@pytest.mark.parametrize(
    ("question", "reading"),
    [
        ("Am I using less fuel this week compared to the previous week?", "Fuel use · this week vs the week before"),
        ("Show all ADAS disengagement events from yesterday.", "ADAS disengagements · yesterday"),
        ("Compare energy use between city and highway driving.", "Energy use, city vs highway · all recorded data"),
        (
            "When did fuel efficiency show the largest drop?",
            "Day with the biggest drop in fuel economy · all recorded data",
        ),
    ],
)
def test_every_answer_can_say_what_it_understood(question, reading):
    assert command.describe(fastpath.parse(question).query) == reading


def test_every_intent_has_a_reading():
    for intent in command.VERBS.values():
        if intent:
            q = command.parse(f"{intent} metric=range window=last_7d by=temperature dir=max event=dtc")
            assert command.describe(q) and not command.describe(q).startswith(intent)
