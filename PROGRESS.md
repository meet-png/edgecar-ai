# PROGRESS — EdgeCar AI

The working log for this project. **Read this first at the start of every session.** It records what is
done, what was decided and why, and what is next. Newest session at the top of the log.

---

## 1. Project in one paragraph

EdgeCar AI is an offline, on-device assistant for software-defined vehicles. It ingests vehicle data
(CAN/CAN-FD, SOME/IP, Android VHAL, QNX logs, ADAS events, diagnostics), keeps weeks of searchable
history on the vehicle, and answers natural-language questions ("Why has my range dropped over the last two
weeks?") in under 2 s on ~2 GB RAM / 2 ARM cores. A small language model translates the question into a
structured query; a deterministic analytics engine computes every number; the model only phrases the answer.

Built for **Tata Elxsi TELIPORT Season 4 — Case Study 5** ("Natural Language Vehicle Information using
On-Device SLM/LLM"), and maintained as a portfolio project.

## 2. Deadlines

| Round | What | Window |
|---|---|---|
| R1 — Idea | **1 content slide** (+ title slide) in the official template | 23 Sep → **1 Nov 2026, 23:59 IST** |
| R2 — Detailed | 10-slide PPT + prototype/multimedia (shortlisted teams; PPO/PPI chance) | 14 Nov → 13 Dec 2026 |
| R3 — Finale | Present to Tata Elxsi senior leaders | TBA |

Template: `Teliport S4_Submission Template.pptx` (16:9; slide 1 = case study no. + team details, slide 2 = content).

## 3. Brief constraints (non-negotiable)

- No cloud dependency.
- Runs on ~2 GB RAM, ~2 ARM A5x cores (Pi 4/5, QEMU, Cuttlefish, Graviton all accepted — **no physical board required**).
- < 2 s response for common queries.
- Several weeks of history searchable.
- Safety-critical functions isolated from the AI.

Judged on: architecture completeness, AI capability (NL understanding + accuracy), data integration (multiple
protocols), search & analytics (events, trends, anomalies, causes), edge performance (memory, latency,
scalability), privacy & security, innovation & practicality.

## 4. Key decisions (details in `docs/adr/`)

| # | Decision | Why |
|---|---|---|
| ADR-0001 | SLM is a **semantic parser**, not an oracle. NL → typed query (grammar-constrained JSON) → deterministic analytics → SLM phrases the facts. Fast-path intent cache bypasses the SLM for common questions. | 2 s budget on 2 ARM cores allows only ~20–40 generated tokens; SLMs hallucinate numbers. |
| ADR-0002 | **Real data first.** Replay real vehicle datasets as genuine protocol traffic; simulate only what has no public real source, with injected ground-truth scenarios. | Accuracy can be *measured* against known truth; differentiates from the competitor. |
| ADR-0003 | **One embedded DuckDB file**, tiers: 1 Hz `frames` → `frames_1min` roll-ups → `trips` + `events` (lifetime). Memory/threads capped. | Window aggregations dominate; columnar + pre-computed trips = 2–13 ms queries. |
| ADR-0004 | **Simulation sits on top of real trips, never alters them.** ADAS from real speed profiles + TD2D takeover stats; fault scenarios as labelled derived trips; seeded; `sim:*` provenance. | Ground truth for events without contaminating real energy/thermal data. |

## 5. Datasets (full registry in `docs/DATASETS.md`)

| Dataset | Real? | Status | Used for |
|---|---|---|---|
| VED (Ann Arbor, 383 vehicles, Nov 2017–Nov 2018) | Real | ✅ downloaded + extracted (54 weekly CSVs, 3.2 GB) | EV range/energy over a full year (Leaf **VehId 455**: 293 trips/191 days; VehId 10: 201 trips), PHEV fuel (VehId 457/550/569 have Fuel Rate), city vs highway |
| TUM BMW i3 (72 trips, Jun 2019–Feb 2020, 10 Hz) | Real | ✅ downloaded (70 trip CSVs: 32 summer "A", 38 winter "B") | Battery temperature, heating power, summer-vs-winter range drop |
| Nissan Leaf DBC (dalathegreat) | Real | ✅ downloaded (GPL-3.0 → fetched, never committed) | Encoding replayed signals as real CAN frames |
| ADAS-TO longitudinal (HF, MIT) | Real | ⛔ gated (manual approval) — Meet to request | ADAS takeovers |
| BATON-Sample (HF, CC BY-NC 4.0, 43 routes) | Real | ⛔ gated (auto-approve) — needs Meet's HF login/token | ADAS engage/disengage + lead radar + takeover labels |
| TD2D (Zenodo, CC BY 4.0, CARLA L2 takeovers) | Simulated (CARLA) | ✅ 500 takeover files fetched via HTTP range requests (not the 16.7 GB zip) | Takeover reaction time (median 948 ms) + outcome (36 % fail) for ADAS sim |
| NHTSA SGO Level-2 ADAS incident CSV | Real | ✅ downloaded (2,574 incidents) | ADAS incident priors / wording |
| CA DMV disengagement reports | Real | ❌ L3+ only; pages 404 | — |
| OpenLKA (Dropbox) | Real | ❌ Dropbox links return error page | — |
| ADAS-TO full / BATON full | Real | ❌ manual gate + 40 GB+ | — |
| DTC / SOME/IP / VHAL / QNX logs | — | No open real time-series exists → simulate to spec | — |

## 6. Competitor watch

`github.com/Reshma-1717/VehicleGPT` targets the same case study (49 commits, React UI, all adapters, SQLite
tiers, "deterministic engine + LLM explains"). Gaps per its README: LLM not in the benchmark loop
(12–47 ms / 40 MB RSS are impossible with a model loaded), synthetic-only data, no NL-understanding eval.
**Our edge:** real datasets, end-to-end latency/RSS *with the model loaded*, paraphrase eval with an accuracy %,
fast path. Do not use the name "VehicleGPT".

## 7. Environment

Windows 11, PowerShell + Git Bash. Python 3.11.9, Node 24, git, gh (logged in as `meet-png`, commits use the
GitHub no-reply email). **No Docker, no WSL, no uv.** PyPI downloads are flaky here — use
`pip install --default-timeout=120`. Windows `tar` (bsdtar 3.8) extracts `.7z`. Run analysis scripts with
`python -P` (not `-I`: `-I` hides user site-packages where pandas deps live).

## 8. Open items / blockers

- [ ] Meet: request ADAS-TO_longitudinal access on HF (manual approval).
- [ ] Meet: accept BATON-Sample terms on HF; save a **read** token to `C:\Users\ACER\.cache\huggingface\token`
      (never paste tokens in chat) so scripts can fetch CSVs only.
- [ ] Meet: team details for the title slide (name, college, email) — needed before R1 submission.
- [ ] Optional (R2): install WSL2 + Docker Desktop for the 2 GB / 2-CPU container benchmark.

- [ ] **Meet: approve llama.cpp download** (official `ggml-org/llama.cpp` release `b11577`, Windows CPU build +
      Ubuntu ARM64 build). Auto mode blocked downloading/running the external binary; needed for the SLM parser.

## 9. Next steps

1. Wire ADAS sim + battery-overheat scenario (base: real hot-day trip `i3-tum:A07`) into the build.
2. NLU: fast-path parser (no model) → SLM parser via `llama-server` + JSON-schema-constrained decoding
   (Qwen2.5-0.5B Q4_K_M downloaded to `models/`).
3. Protocol layer: Leaf DBC CAN encode/decode round-trip; SOME/IP, VHAL, QNX slog2, UDS codecs.
4. Eval set (~150 paraphrased questions with ground truth) + latency/RSS benchmark at 2 threads.
5. API + car-style dashboard; then R1 slide.

## Verified results so far (for the slide)

- Leaf (VED 455), last 14 days to 4 Nov 2018: range 161 → 151 km (−6 %); consumption 13.2 → 14.1 kWh/100 km;
  top factors: colder air/battery/tyres (+0.6 kWh/100 km ≈ 7 km) and heating (+0.4 ≈ 5 km); 6.9 °C vs 10.8 °C.
- Leaf worst efficiency day: Sun 7 Jan 2018, −14.2 °C, 2.77 vs typical 5.93 km/kWh (−53 %); heating
  16.9 vs 9.6 kWh/100 km.
- Leaf, all year: below 0 °C 21.2 kWh/100 km vs 10–20 °C 11.6 (+83 %); city 16.4 vs highway 15.3 kWh/100 km.
- i3: highway 17.7 vs city 9.2 kWh/100 km; below 0 °C 19.7 vs 10–20 °C 10.1 kWh/100 km.
- Store: 4 vehicles, 540 k frames, 814 trips, 1,176 detected events → 36 MB; build 20 s; queries 2–13 ms.

---

## Session log

### 2026-10-27 — Session 1: brief, research, data acquisition
- Read the case-study PDF and the R1 submission template; confirmed R1 = one content slide due 1 Nov.
- Researched prior art: competitor repo, Mercedes-Benz in-vehicle SLM paper (arXiv 2501.02342: pruned
  Phi-3 1.8B, <2 GB RAM, ~11 tok/s on 7-core ARM head unit), Arm on-device assistant blog (system-prompt KV
  cache precompute ≈ 2× latency win), Pi 5 llama.cpp numbers (0.5B ≈ 17–20 tok/s, 1.5B ≈ 10–15 tok/s).
- Downloaded + profiled VED, TUM BMW i3, Leaf DBCs. Found ADAS-TO/BATON gated, OpenLKA Dropbox dead.
- Created repo skeleton, this log, dataset registry, first two ADRs. Pushed to private GitHub repo.
- Fetched TD2D takeover files (500) via exact HTTP range requests; NHTSA SGO ADAS CSV.
- Built: canonical schema, fleet, VED/TUM converters (1 Hz resample, despike), DuckDB store with trips/roll-ups,
  real-signal event detectors (charging, hard braking, battery temperature), typed `Query`, analytics engine
  (summary/trend/extreme/compare/explain_change/list_events/events_before/anomalies/status), ADAS simulator.
- Data-quality fixes found by testing: TUM A10 max-battery-temp spike (60.9 °C) removed by despike;
  sparse windows fall back to "last 8 trips vs 8 before"; PHEV fuel answers now report electric-only share.
- Gotchas: pip to files.pythonhosted.org stalls (IPv6) but completes; Windows console needs
  `PYTHONIOENCODING=utf-8`; pandas NaN must be converted to SQL NULL on insert.
