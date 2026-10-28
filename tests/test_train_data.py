from collections import Counter
from pathlib import Path

from edgecar.nlu import command
from edgecar.nlu.slm import messages, prompt_style
from edgecar.train import data

ROOT = Path(__file__).resolve().parents[1]


def test_seeds_parse_and_every_command_is_valid():
    seeds = data.read_seeds()
    assert len(seeds) > 400
    for _, target in seeds:
        data.check_target(target)


def test_training_set_covers_the_command_space_and_never_overlaps_eval():
    rows, stats = data.build(seed=7, root=ROOT)
    verbs = Counter(r["a"].split()[0] for r in rows)
    assert set(verbs) == set(command.VERB_ARGS)  # every verb, including unsupported
    assert stats["distinct_commands"] >= 100
    filt = data.OverlapFilter(root=ROOT)
    assert not any(filt.hit(r["q"]) for r in rows)
    assert len({r["q"].lower() for r in rows}) == len(rows)


def test_overlap_filter_catches_near_copies():
    filt = data.OverlapFilter(root=ROOT)
    assert filt.hit("what happened before the battery overheating warning") == "eval/questions.jsonl"
    assert filt.hit("WHAT HAPPENED BEFORE THE BATTERY OVERHEATING WARNING?!") is not None
    assert filt.hit("how many litres of washer fluid are left in the reservoir right now") is None


def test_tuned_models_get_the_short_prompt():
    assert prompt_style(Path("models/edgecar-0.5b-q4_k_m.gguf")) == "tuned"
    assert prompt_style(Path("models/qwen2.5-1.5b-instruct-q4_k_m.gguf")) == "fewshot"
    tuned = messages("why did my range drop", "tuned")
    assert [m["role"] for m in tuned] == ["system", "user"]
    assert len(messages("why did my range drop")) > 40  # few-shot: instructions + example turns
