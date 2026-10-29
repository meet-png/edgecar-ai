import json

from edgecar.bench.significance import kind_of_error, report


def row(q, pred, gold, ok=False, path="slm", i="t-x-1"):
    return {"id": i, "q": q, "pred": pred, "gold": [gold], "ok": ok, "path": path}


def test_errors_are_named_by_the_nearest_accepted_answer():
    assert kind_of_error(row("q", "unsupported", "summary metric=distance window=last_7d")) == (
        "declined an answerable question"
    )
    assert kind_of_error(row("q", "anomalies window=last_30d", "unsupported")) == "answered what it should decline"
    assert kind_of_error(row("q", "summary metric=speed window=last_7d", "summary metric=distance window=last_7d")) == (
        "wrong metric"
    )
    assert kind_of_error(
        row("q", "summary metric=distance window=last_7d", "summary metric=distance window=today")
    ) == ("wrong time window (read by code, not the model)")
    assert kind_of_error(
        row("q", "trend metric=range window=last_30d", "explain_change metric=range window=last_14d")
    ) == ("wrong question type")


def test_report_pairs_models_on_the_same_questions(tmp_path):
    def write(name, oks):
        rows = [row(f"q{i}", "unsupported", "unsupported", ok, i=f"{name[:6]}-{i}") for i, ok in enumerate(oks)]
        (tmp_path / f"{name}.json").write_text(json.dumps({"rows": rows}))

    for prefix in ("test-hybrid", "refusal_test-hybrid", "test-slm"):
        write(f"{prefix}-qwen2.5-1.5b-fewshot", [True, False, True])
        write(f"{prefix}-edgecar-1.5b-q4_k_m", [True, True, True])
    text = report(tmp_path)
    assert "6/6 = **100.0%**" in text and "| 2 · 0 | 0.50 |" in text  # pooled 2 × 3: wins 2, losses 0, p = 0.5
