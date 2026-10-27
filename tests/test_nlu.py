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
    ],
)
def test_time_windows(text, kind, days):
    w = fastpath.parse_window(f" {text} ")
    assert (w.kind, w.days) == (kind, days)


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
