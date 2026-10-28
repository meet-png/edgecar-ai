from datetime import datetime

import numpy as np
from fastapi.testclient import TestClient

from edgecar.app.server import Zone, create_app, plain


def client(store) -> TestClient:
    zone = Zone(store, slm=None, model_name=None)
    zone._assistants.clear()
    return TestClient(create_app(zone))


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
