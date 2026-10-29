from edgecar.prep import fetch


def test_fetch_all_keeps_the_other_targets(monkeypatch):
    """`fetch all tuned` must fetch the tuned models too, not only what "all" stands for."""
    seen: list[str] = []
    monkeypatch.setattr(fetch, "fetch_ved", lambda *a: seen.append("ved"))
    monkeypatch.setattr(fetch, "fetch_pinned", lambda client, url, files, out: seen.append(out.name))
    monkeypatch.setattr(fetch, "fetch_td2d", lambda *a: seen.append("td2d"))
    monkeypatch.setattr(fetch, "download", lambda client, url, dest, digest: seen.append(dest.name))
    monkeypatch.setattr("sys.argv", ["fetch", "all", "tuned"])
    fetch.main()
    assert "edgecar-0.5b-q4_k_m.gguf" in seen and "edgecar-1.5b-q4_k_m.gguf" in seen and "ved" in seen
