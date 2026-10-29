from datetime import datetime

import numpy as np
import pytest
from fastapi.testclient import TestClient

from edgecar import voice
from edgecar.app.server import MAX_AUDIO_BYTES, Zone, create_app, main, plain
from edgecar.privacy import Role


def client(store, **zone_args) -> TestClient:
    zone = Zone(store, slm=None, model_name=None, **zone_args)
    return TestClient(create_app(zone), base_url="http://127.0.0.1")


def test_plain_makes_answers_json_safe():
    out = plain({"t": datetime(2024, 1, 2, 3, 4), "x": np.float64("nan"), "n": np.int64(3), "v": [np.float32(1.5)]})
    assert out == {"t": "2024-01-02T03:04:00", "x": None, "n": 3, "v": [1.5]}


def test_meta_lists_the_stored_vehicles(store):
    meta = client(store).get("/api/meta").json()
    assert [v["id"] for v in meta["vehicles"]] == ["ev-1"]
    assert meta["model"] is None and meta["voice"] == {"listen": False, "speak": False}


def test_ask_returns_the_answer_with_its_evidence(store):
    c = client(store)
    r = c.post("/api/ask", json={"question": "Why has my range dropped over the last two weeks?", "vehicle": "ev-1"})
    assert r.status_code == 200
    ans = r.json()
    assert ans["route"] == "rules" and ans["intent"] == "explain_change"
    assert ans["chart"]["type"] == "waterfall"
    last = store.sql("SELECT max(ts) AS t FROM frames")["t"][0]  # the app's "now" is the latest data
    assert ans["window"][1] == last.isoformat()
    # declined, not answered with a listing that shares a keyword
    r = c.post("/api/ask", json={"question": "Turn off lane keeping assist", "vehicle": "ev-1"}).json()
    assert r["route"] == "declined" and r["chart"] is None


def test_page_is_served_offline_only(store):
    c = client(store)
    r = c.get("/")
    assert r.status_code == 200 and "app.js" in r.text
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "http" not in csp  # nothing may load from another origin
    assert c.post("/api/ask", json={"question": "hi", "vehicle": "nope"}).status_code == 404
    assert c.post("/api/listen", content=b"").status_code == 501  # no speech engine installed in tests


def test_only_this_host_and_origin_may_ask(store):
    q = {"question": "How far did I drive?", "vehicle": "ev-1"}
    rebound = TestClient(create_app(Zone(store, slm=None, model_name=None)), base_url="http://evil.example")
    assert rebound.get("/api/meta").status_code == 400  # DNS rebinding: the Host header names another site
    c = client(store)
    assert c.post("/api/ask", json=q, headers={"Origin": "http://evil.example"}).status_code == 403
    assert c.post("/api/ask", json=q, headers={"Origin": "http://127.0.0.1"}).status_code == 200


def test_the_server_sets_the_widest_view(store):
    c = client(store, max_role=Role.SERVICE)
    assert c.get("/api/meta").json()["roles"] == ["service", "fleet"]
    q = {"question": "How far did I drive?", "vehicle": "ev-1"}
    assert c.post("/api/ask", json={**q, "role": "driver"}).status_code == 403  # a request cannot widen it
    assert c.post("/api/ask", json={**q, "role": "fleet"}).status_code == 200  # but may narrow it


class _Ear:
    can_listen, can_speak = True, False

    def transcribe(self, wav: bytes) -> str:
        samples, _ = voice.read_wav(wav)
        return f"{len(samples)} samples"


def test_listen_takes_short_wav_recordings_only(store):
    c = client(store, voice=_Ear())
    one_second = voice.write_wav(np.zeros(16_000, dtype=np.float32), 16_000)
    assert c.post("/api/listen", content=one_second).json()["text"] == "16000 samples"
    assert c.post("/api/listen", content=b"not audio").status_code == 400
    assert c.post("/api/listen", content=b"x" * (MAX_AUDIO_BYTES + 1)).status_code == 413


def test_serve_refuses_a_wildcard_address():
    with pytest.raises(SystemExit):
        main(["--host", "0.0.0.0", "--role", "fleet", "--no-model", "--no-voice"])
