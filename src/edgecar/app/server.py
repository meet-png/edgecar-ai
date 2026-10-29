"""The in-car app: one page — no build step, no CDN — served by the AI zone itself.

    edgecar serve                      # http://127.0.0.1:8080

A question arrives as text (or as speech, recognised on-device — see ``edgecar.voice``) and the answer comes back
with the evidence the engine used: the chart, the event timeline or table, the route it took (rules or model), the
command it ran and how long it took. The server binds to localhost and the page loads nothing from the network.

Who may see what is decided here, not by the page: ``--role`` sets the most a viewer may see (the owner's car:
driver), and a request can only ask for a narrower view (driver ⊃ service ⊃ fleet). Requests must name this host
(no DNS rebinding) and may not come from another origin.
"""

from __future__ import annotations

import argparse
import math
import threading
import time
import wave
from contextlib import nullcontext
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from edgecar.assistant import Answer, Assistant
from edgecar.nlu.slm import PROFILES, SlmParser, SlmServer, find_server, prompt_style
from edgecar.privacy import Role, redact
from edgecar.query import Query, TimeWindow
from edgecar.store import Store

STATIC = Path(__file__).with_name("static")
EXPOSURE = (Role.FLEET, Role.SERVICE, Role.DRIVER)  # each sees a subset of the next (see edgecar.privacy)
LOCAL_HOSTS = ("127.0.0.1", "localhost")
MAX_AUDIO_BYTES = 400_000  # a 16 kHz 16-bit WAV of the 9 s the recogniser takes is ~290 kB
ROUTE = {"fast": "rules", "slm": "model", "refused": "declined", "unavailable": "declined", "unsupported": "declined"}

# Starting points shown under the input, per demo car: the case study's questions on the car they fit, then others.
SUGGESTIONS: dict[str, list[str]] = {
    "leaf-455": [
        "Why has my driving range dropped over the last two weeks?",
        "When did fuel efficiency show the largest drop?",
        "Is anything unusual this month?",
        "Show my range over the last 3 months",
    ],
    "leaf-010": [
        "Why is my range lower than last month?",
        "When was my efficiency at its worst?",
        "How far did I drive last week?",
    ],
    "i3-tum": [
        "What happened before the battery overheating warning?",
        "Compare energy consumption between city and highway driving.",
        "When did the battery get hottest?",
        "Any fault codes?",
    ],
    "phev-457": [
        "Show all ADAS disengagement events from yesterday.",
        "What happened before the last takeover request?",
        "When was my fuel economy at its worst?",
        "Does cold weather hurt my mileage?",
    ],
}


def plain(x: Any) -> Any:
    """JSON-safe copy: dates → ISO strings, numpy/pandas scalars → Python numbers, NaN → null."""
    if isinstance(x, dict):
        return {str(k): plain(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [plain(v) for v in x]
    if isinstance(x, pd.Timestamp | datetime | date):
        return x.isoformat()
    if isinstance(x, np.generic):
        x = x.item()
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if x is None or isinstance(x, str | int | float | bool):
        return x
    return str(x)


def answer_json(ans: Answer) -> dict:
    r = ans.result
    return plain(
        {
            "question": ans.question,
            "text": ans.text,
            "path": ans.path,
            "route": ROUTE.get(ans.path, ans.path),
            "ms": round(ans.timings_ms.get("total", 0.0), 1),
            "timings": {k: round(v, 1) for k, v in ans.timings_ms.items()},
            "command": ans.command,
            "model_line": ans.model_line,
            "intent": r.intent if r else None,
            "headline": r.headline if r else ans.text,
            "details": r.details if r else [],
            "notes": r.notes if r else [],
            "facts": r.facts if r else {},
            "chart": r.chart if r else None,
            "table": r.table if r else None,
            "window": list(r.window) if r and r.window else None,
            "evidence": len(r.evidence) if r else 0,
        }
    )


class Ask(BaseModel):
    question: str
    vehicle: str = "leaf-455"
    role: Role = Role.DRIVER


class Speak(BaseModel):
    text: str


class Zone:
    """Everything the page talks to: the store, the model, optional speech, one lock (one driver, one question at a
    time — the model serves one request at once anyway)."""

    def __init__(
        self, store: Store, slm: SlmParser | None, model_name: str | None, voice=None, max_role: Role = Role.DRIVER
    ) -> None:
        self.store, self.slm, self.model_name, self.voice = store, slm, model_name, voice
        self.roles = [r for r in reversed(EXPOSURE) if EXPOSURE.index(r) <= EXPOSURE.index(max_role)]
        self.lock = threading.Lock()
        self._assistants: dict[tuple[str, Role], Assistant] = {}

    def assistant(self, vehicle: str, role: Role) -> Assistant:
        if role not in self.roles:
            raise HTTPException(403, f"this display may show at most the {self.roles[0].value} view")
        key = (vehicle, role)
        if key not in self._assistants:
            if vehicle not in {v["id"] for v in self.vehicles()}:
                raise HTTPException(404, f"unknown vehicle {vehicle!r}")
            self._assistants[key] = Assistant(self.store, vehicle, self.slm, role=role)
        return self._assistants[key]

    def vehicles(self) -> list[dict]:
        """The vehicles in the store that have data, with the span of their recordings."""
        rows = self.store.sql(
            """SELECT v.*, f.first, f.last FROM vehicles v
               JOIN (SELECT vehicle_id, min(ts) AS first, max(ts) AS last FROM frames GROUP BY 1) f USING (vehicle_id)
               ORDER BY v.vehicle_id"""
        )
        order = list(SUGGESTIONS)  # the demo cars first, in a sensible order
        out = [
            {
                "id": r.vehicle_id,
                "name": r.name,
                "powertrain": r.powertrain,
                "location": r.location,
                "source": r.source_ref,
                "provenance": str(r.provenance),
                "first": r.first,
                "last": r.last,
                "suggestions": SUGGESTIONS.get(r.vehicle_id, SUGGESTIONS["leaf-010"]),
            }
            for r in rows.itertuples()
        ]
        out.sort(key=lambda v: order.index(v["id"]) if v["id"] in order else len(order))
        return plain(out)


def create_app(zone: Zone, hosts: tuple[str, ...] = LOCAL_HOSTS) -> FastAPI:
    app = FastAPI(title="EdgeCar AI", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.middleware("http")
    async def same_origin_only(request: Request, call_next):
        # A page from elsewhere may not post questions or audio here (the browser sends Origin on such requests).
        origin = request.headers.get("origin")
        if request.method != "GET" and origin and origin.split("://", 1)[-1] != request.headers.get("host"):
            return Response("cross-origin request refused", status_code=403)
        return await call_next(request)

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(hosts))  # runs before the origin check

    @app.middleware("http")
    async def offline_headers(request: Request, call_next):
        response = await call_next(request)
        # The page may load nothing from anywhere else — what the vehicle would enforce anyway.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; style-src 'self'; script-src 'self'"
        )
        return response

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/meta")
    def meta() -> dict:
        with zone.lock:
            vehicles = zone.vehicles()
        return {
            "vehicles": vehicles,
            "roles": [r.value for r in zone.roles],
            "model": zone.model_name,
            "voice": {
                "listen": bool(zone.voice and zone.voice.can_listen),
                "speak": bool(zone.voice and zone.voice.can_speak),
            },
        }

    @app.post("/api/ask")
    def ask(req: Ask) -> dict:
        question = req.question.strip()
        if not question:
            raise HTTPException(400, "empty question")
        with zone.lock:
            ans = zone.assistant(req.vehicle, req.role).ask(question[:500])
        return answer_json(ans)

    @app.get("/api/status")
    def status(vehicle: str = "leaf-455") -> dict:
        role = zone.roles[0]  # the widest view this display allows
        with zone.lock:
            res = redact(
                zone.assistant(vehicle, role).engine.run(Query(intent="status", window=TimeWindow(kind="today"))), role
            )
        return plain({"headline": res.headline, "facts": res.facts})

    @app.post("/api/listen")
    async def listen(request: Request) -> dict:
        if not (zone.voice and zone.voice.can_listen):
            raise HTTPException(501, "speech recognition is not installed")
        if int(request.headers.get("content-length") or 0) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "recording too long")
        audio = bytearray()
        async for chunk in request.stream():  # chunked uploads carry no length: count as they arrive
            audio += chunk
            if len(audio) > MAX_AUDIO_BYTES:
                raise HTTPException(413, "recording too long")
        t0 = time.perf_counter()
        try:
            with zone.lock:
                text = zone.voice.transcribe(bytes(audio))
        except (wave.Error, ValueError, EOFError) as e:
            raise HTTPException(400, f"not a 16-bit PCM WAV recording: {e}") from None
        return {"text": text, "ms": round((time.perf_counter() - t0) * 1000, 1)}

    @app.post("/api/speak")
    def speak(req: Speak) -> Response:
        if not (zone.voice and zone.voice.can_speak):
            raise HTTPException(501, "speech synthesis is not installed")
        with zone.lock:
            wav = zone.voice.speak(req.text[:600])
        return Response(wav, media_type="audio/wav")

    return app


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", type=Path, default=Path("data/edgecar.duckdb"))
    ap.add_argument("--model", type=Path, default=Path("models/qwen2.5-1.5b-instruct-q4_k_m.gguf"))
    ap.add_argument("--server", type=Path, default=None)
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--no-model", action="store_true", help="rules only")
    ap.add_argument("--no-voice", action="store_true")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument(
        "--role",
        type=Role,
        choices=list(Role),
        default=None,
        help="the most this display may show: driver (the owner's own car, the default on localhost), service or fleet",
    )
    a = ap.parse_args(argv)
    if a.host in ("0.0.0.0", "::"):  # requests must name this host, and none names a wildcard
        ap.error(f"--host {a.host}: bind the address viewers will use, e.g. --host 192.168.1.20")
    if a.role is None:
        if a.host not in LOCAL_HOSTS:
            ap.error(f"--host {a.host} is reachable from other machines: say which view it may show with --role")
        a.role = Role.DRIVER

    store = Store(a.store, read_only=True, threads=a.threads)
    server = find_server(a.server)
    use_model = not a.no_model and server is not None and a.model.exists()
    ctx = (
        SlmServer(server, a.model, threads=a.threads, cache_dir=Path("data/cache"), **PROFILES["edge"])
        if use_model
        else nullcontext(None)
    )
    voice = None
    if not a.no_voice:
        from edgecar import voice as voice_mod

        voice = voice_mod.load()

    with ctx as srv:
        slm = SlmParser(srv.url, style=prompt_style(a.model)) if srv else None
        if slm:
            print(f"model: {a.model.name} — prompt prefix {slm.ready(a.model)}")
        else:
            print("model: none — rules only")
        zone = Zone(store, slm, a.model.name if slm else None, voice, max_role=a.role)
        app = create_app(zone, hosts=tuple(dict.fromkeys((*LOCAL_HOSTS, a.host))))
        print(f"EdgeCar AI on http://{a.host}:{a.port}")
        uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
