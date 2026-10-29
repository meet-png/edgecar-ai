from edgecar.bench.calibration import at_threshold, auroc, choose_threshold


def test_auroc():
    assert auroc([(0.9, True), (0.8, True), (0.2, False)]) == 1.0
    assert auroc([(0.5, True), (0.5, False)]) == 0.5


def test_threshold_is_the_widest_that_keeps_errors_rare():
    pairs = [(0.99, True)] * 150 + [(0.4, False), (0.6, True), (0.3, True)]
    t = choose_threshold(pairs, 0.01)
    r = at_threshold(pairs, t)
    assert r["wrong_answered"] / r["answered"] <= 0.01 and r["answered"] >= 151
    assert at_threshold(pairs, 0.0)["coverage"] == 1.0
