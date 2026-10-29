import random

from edgecar.bench.robustness import filler, number_words, plain, spoken, summarise, typos, variant


def test_number_words():
    assert [number_words(n) for n in (0, 7, 15, 30, 42, 100, 365)] == [
        "zero",
        "seven",
        "fifteen",
        "thirty",
        "forty two",
        "one hundred",
        "three hundred and sixty five",
    ]


def test_spoken_reads_numbers_and_units_like_a_recogniser():
    assert spoken("Why is my kWh/100 km so high over the past 10 days?") == (
        "why is my kilowatt hours per hundred kilometers so high over the past ten days"
    )
    assert plain("Any DTCs, yesterday?!") == "any dtcs yesterday"


def test_perturbations_are_deterministic_per_question():
    items = [{"id": "t-1", "q": "What happened before the battery overheating warning?", "gold": ["x"]}]
    assert variant(items, "typos", typos) == variant(items, "typos", typos)
    assert variant(items, "typos", typos)[0]["gold"] == ["x"]  # only the wording changes
    assert filler("Any fault codes?", random.Random(1)).endswith(("any fault codes", "please", "thanks"))


def test_summary_counts_questions_lost_and_gained():
    clean = {"rows": [{"id": i, "ok": ok} for i, ok in enumerate([True, True, False])]}
    other = {"rows": [{"id": i, "ok": ok} for i, ok in enumerate([True, False, True])]}
    assert summarise(clean, other) == {"k": 2, "n": 3, "lost": 1, "gained": 1, "p": 1.0}
