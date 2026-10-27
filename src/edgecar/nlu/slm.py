"""Grammar-constrained small-language-model parser, served by llama.cpp's ``llama-server``.

The model runs in its own process (memory-capped, no network, restartable) and the assistant talks to it over
localhost. It only classifies *meaning* — a question-aligned verb plus a metric/event/direction — in 2–6 tokens;
time windows are extracted deterministically (see ``edgecar.nlu.pipeline``).

The prompt (instructions + few-shot chat turns) is identical for every request, so llama-server keeps its KV cache
(``cache_prompt``) and each question only costs its own ~20 tokens of prompt processing.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from edgecar.nlu import command

INSTRUCTIONS = """Classify a question about the driver's own vehicle into one short command.

total M      one number over a period: how much, how far, how many, average
trend M      how something changed over time: trend, graph, plot, getting better or worse
when M D     which day something peaked or dropped most. D = max, min, drop (fell most), rise (jumped most)
compare M B  B = road_type (city vs highway), temperature (cold vs warm), previous_period (vs before)
why M        why something changed
show E       list events of one kind
before E     what happened before, or led up to, an event
unusual      anything abnormal, strange or wrong
now          the car's current state: battery level now, range left now
unsupported  anything not about this vehicle's recorded data: music, navigation, weather forecast, calls, jokes

M = energy_consumption (kWh per 100 km), energy_efficiency (km per kWh), range, fuel_consumption (litres per
100 km), fuel_efficiency (km per litre, mileage, fuel economy), distance, energy_used, hvac_energy (heating and
air conditioning), regen_energy, speed, battery_temp, soc (battery level), ambient_temp (outside temperature),
hard_brakes, trip_count. For compare, M is one of the four consumption/efficiency metrics.
E = adas_disengagement (lane keeping or adaptive cruise switched off), adas_takeover_request, adas_any,
battery_overheat_warning, dtc (fault/trouble/error codes), thermal_any, charging, hard_brake, system, any."""

# Few-shot examples as chat turns. None of these appear in the evaluation sets.
EXAMPLES: tuple[tuple[str, str], ...] = (
    ("my range used to be better, what changed", "why range"),
    ("why is the car burning through the battery faster", "why energy_consumption"),
    ("what made my efficiency worse recently", "why energy_efficiency"),
    ("why is my petrol consumption up", "why fuel_consumption"),
    ("on which day did my efficiency fall the most", "when energy_efficiency drop"),
    ("when did the battery get hottest", "when battery_temp max"),
    ("the day with the lowest outside temperature", "when ambient_temp min"),
    ("when did my energy use per km jump the most", "when energy_consumption rise"),
    ("which day did I cover the longest distance", "when distance max"),
    ("when was my fuel economy at its worst", "when fuel_efficiency min"),
    ("highway or city, where do I use more energy", "compare energy_consumption road_type"),
    ("does cold weather hurt my mileage", "compare fuel_efficiency temperature"),
    ("am I using more energy than before", "compare energy_consumption previous_period"),
    ("is town driving more efficient than motorway driving", "compare energy_efficiency road_type"),
    ("list every time lane assist switched off", "show adas_disengagement"),
    ("did the car ask me to take over", "show adas_takeover_request"),
    ("any fault codes", "show dtc"),
    ("when did I charge recently", "show charging"),
    ("show me my harsh stops", "show hard_brake"),
    ("were there any thermal alerts", "show thermal_any"),
    ("what did the driver assistance do", "show adas_any"),
    ("show everything that was logged", "show any"),
    ("walk me through what happened before the overheating alert", "before battery_overheat_warning"),
    ("what led up to that thermal warning", "before thermal_any"),
    ("what was the car doing just before cruise control disengaged", "before adas_disengagement"),
    ("what happened before the error code was stored", "before dtc"),
    ("is anything off with the car", "unusual"),
    ("any weird behaviour lately", "unusual"),
    ("how is my battery temperature changing", "trend battery_temp"),
    ("graph my energy use per km", "trend energy_consumption"),
    ("is my range improving week by week", "trend range"),
    ("how many kilometres did I do", "total distance"),
    ("total energy recovered by regen", "total regen_energy"),
    ("what was my average speed", "total speed"),
    ("how much energy did the heater take", "total hvac_energy"),
    ("how many trips have I made", "total trip_count"),
    ("how much charge do I have left", "now"),
    ("what's the battery at right now", "now"),
    ("order me a pizza", "unsupported"),
    ("turn up the volume", "unsupported"),
)


_SENTINEL = "␞question␞"  # placeholder used once to split the formatted prompt around the question


def messages(question: str) -> list[dict]:
    msgs = [{"role": "system", "content": INSTRUCTIONS}]
    for q, a in EXAMPLES:
        msgs += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
    return [*msgs, {"role": "user", "content": question}]


@dataclass
class ParseOutput:
    line: str
    prompt_tokens: int
    cached_tokens: int
    generated_tokens: int
    prompt_ms: float
    generate_ms: float
    total_ms: float


class SlmParser:
    """Client for a running ``llama-server``; see ``SlmServer`` to start one."""

    def __init__(self, url: str = "http://127.0.0.1:8089", timeout_s: float = 30.0) -> None:
        self.url = url.rstrip("/")
        self.timeout_s = timeout_s
        self._grammar = command.model_grammar()
        self._frame: tuple[str, str] | None = None  # (prefix, suffix) in the served model's own chat template

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(
            f"{self.url}{path}", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
            return json.load(r)

    def prompt(self, question: str) -> str:
        """The full prompt in the model's chat template (fetched once from the server, then reused)."""
        if self._frame is None:
            formatted = self._post("/apply-template", {"messages": messages(_SENTINEL)})["prompt"]
            prefix, suffix = formatted.split(_SENTINEL)
            self._frame = (prefix, suffix)
        prefix, suffix = self._frame
        return prefix + question.strip() + suffix

    def parse(self, question: str) -> ParseOutput:
        body = {
            "prompt": self.prompt(question),
            "grammar": self._grammar,
            "n_predict": 16,
            "temperature": 0.0,
            "cache_prompt": True,
            "stop": ["\n"],
        }
        t0 = time.perf_counter()
        out = self._post("/completion", body)
        total = (time.perf_counter() - t0) * 1000
        tm = out.get("timings", {})
        return ParseOutput(
            line=out.get("content", "").strip(),
            prompt_tokens=int(tm.get("prompt_n", 0)),
            cached_tokens=int(out.get("tokens_cached", 0)),
            generated_tokens=int(tm.get("predicted_n", 0)),
            prompt_ms=float(tm.get("prompt_ms", 0.0)),
            generate_ms=float(tm.get("predicted_ms", 0.0)),
            total_ms=total,
        )

    def warm_up(self) -> ParseOutput:
        """Process the shared prompt prefix once so later requests reuse its KV cache."""
        return self.parse("how far did I drive")


# Server profiles. "edge" is the 2 GB target: no weight repacking (weights stay a read-only memory map the OS can
# share and page, instead of a ~630 MB private copy), a context just large enough for the cached prompt, smaller
# batches, and no extra prompt cache in RAM.
PROFILES: dict[str, dict] = {
    "default": {"ctx": 4096, "extra_args": ()},
    "edge": {"ctx": 2048, "extra_args": ("--no-repack", "-b", "512", "-ub", "256", "-cram", "0")},
}


class SlmServer:
    """Starts and stops a local ``llama-server`` bound to 127.0.0.1 only."""

    def __init__(
        self,
        binary: Path,
        model: Path,
        *,
        port: int = 8089,
        threads: int = 2,
        ctx: int = 4096,
        extra_args: tuple[str, ...] = (),
    ) -> None:
        self.args = [
            str(binary),
            "-m",
            str(model),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "-t",
            str(threads),
            "-tb",
            str(threads),
            "-c",
            str(ctx),
            "-np",
            "1",
            "--no-webui",
            "--log-disable",
            *extra_args,
        ]
        self.url = f"http://127.0.0.1:{port}"
        self.proc: subprocess.Popen | None = None

    def start(self, wait_s: float = 60.0) -> None:
        self.proc = subprocess.Popen(self.args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + wait_s
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{self.url}/health", timeout=2) as r:
                    if json.load(r).get("status") == "ok":
                        return
            except OSError:
                pass
            if self.proc.poll() is not None:
                raise RuntimeError(f"llama-server exited with code {self.proc.returncode}")
            time.sleep(0.25)
        raise TimeoutError("llama-server did not become healthy")

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def __enter__(self) -> SlmServer:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
