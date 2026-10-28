# PROGRESS — EdgeCar AI

The working log for this project. **Read this first at the start of every session.** It records what is
done, what was decided and why, and what is next. Newest session at the top of the log.

---

## 1. Project in one paragraph

EdgeCar AI is an offline, on-device assistant for software-defined vehicles. It ingests vehicle data
(CAN/CAN-FD, SOME/IP, Android VHAL, QNX logs, ADAS events, diagnostics), keeps a year of searchable
history on the vehicle, and answers natural-language questions ("Why has my range dropped over the last two
weeks?") in under 2 s on ~2 GB RAM / 2 ARM cores. Deterministic rules answer the common questions; a small
language model turns the rest into a read-only typed query; a deterministic analytics engine computes every number.

Built for **Tata Elxsi TELIPORT Season 4 — Case Study 5** ("Natural Language Vehicle Information using
On-Device SLM/LLM"), and maintained as a portfolio project. Public: https://github.com/meet-png/edgecar-ai

## 2. Deadlines

Team **"Sidemen"** (registered, confirmed): Meet Kabra + Aditya Mishra, JECRC University. Meet builds solo.
The registration page shows the ongoing round deadline as **31 Oct 2026** — treat that as the hard deadline.

| Round | What | Window |
|---|---|---|
| R1 — Idea | **1 content slide** (+ title slide) in the official template | 23 Sep → **31 Oct (portal) / 1 Nov 2026, 23:59 IST** |
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

## 4. Key decisions (details in `docs/adr/`; deployment in `docs/ARCHITECTURE.md`)

| # | Decision | Why |
|---|---|---|
| ADR-0001 | SLM is a **semantic parser**, not an oracle: NL → typed read-only query → deterministic analytics. | SLMs invent numbers; 2 s on 2 ARM cores allows only a few generated tokens. |
| ADR-0002 | **Real data first.** Replay real datasets as protocol traffic; simulate only what has no public source, with ground truth. | Accuracy can be *measured*. |
| ADR-0003 | **One embedded DuckDB file**: 1 Hz `frames` → `frames_1min` → `trips` + `events`. | Window aggregations dominate; one car-year = 5.5 MB, queries 3–22 ms. |
| ADR-0004 | **Simulation on top of real trips, never altering them.** Fault must fit the vehicle (i3: refrigerant-cooled → P0534 → P0C32 → P0A7E). | Ground truth without contaminating real data. |
| ADR-0005 | **Hybrid NLU:** rules → Qwen2.5-1.5B writes a 2–6-token verb command under a GBNF grammar → windows by code; `edge` llama-server profile. | 95.7 % held-out (x86), 94.3 % on ARM; best of 6 models. |
| ADR-0006 | **Every source through its wire protocol**; read-only by construction; UDS decoded passively from the gateway's own polls. | Data integration is real, not CSV rows. |
| ADR-0007 | **Root cause = fault-propagation map + causal order**, measured on 200 random scenarios with decoys. | 197/200 vs 34/200 for "earliest fault". |
| ADR-0008 | **Decline instead of guessing**: action guard, not-recorded catalogue, grounding check. | Refusal 29/40 → 40/40 with no loss in accuracy. |

## 5. Datasets (full registry in `docs/DATASETS.md`; `python -m edgecar.prep.fetch all`)

| Dataset | Real? | Status | Used for |
|---|---|---|---|
| VED (Ann Arbor, Nov 2017–Nov 2018) | Real | ✅ fetched by script (2 × 7z from GitHub) | Leaf 455 / 010 year-long EV energy + range; PHEV 457 fuel |
| TUM BMW i3 (70 trip CSVs) | Real | ✅ manual (IEEE login) | Battery temperature, heating, summer vs winter; base of fault scenarios |
| Nissan Leaf DBC (dalathegreat, GPL) | Real | ✅ fetched, SHA-pinned | Real CAN encoding |
| BATON benchmark tables (GitHub LFS, CC BY-NC) | Real | ✅ fetched, SHA-pinned | ADAS episode / re-engagement timing |
| TD2D (Zenodo, CC BY 4.0, CARLA) | Simulated | ✅ 500-row table ships in `sim/calibration/` | Takeover reaction time + outcome |
| NHTSA SGO L2 incidents | Real | ✅ | ADAS incident priors / wording |
| ADAS-TO, OpenLKA, CA DMV | — | ❌ gated / dead / L3+ only | — |
| DTC / SOME/IP / VHAL / QNX logs | — | Simulated to spec on real trips | — |

## 6. Prior art that shaped the design

- Mercedes-Benz R&D, *Optimizing Small Language Models for In-Vehicle Function-Calling* (arXiv:2501.02342):
  pruned Phi-3 to 1.8B, < 2 GB RAM, ~11 tok/s on a 7-core ARM head unit, 0.86–0.88 function-calling accuracy
  after fine-tuning — validates "small model + structured output" and points to LoRA as the next step.
- Arm, *Building an on-device multimodal assistant for automobiles*: pre-computing the system-prompt KV cache
  gave a ~2× end-to-end speed-up — adopted via llama-server prompt caching, and now saved to disk for ignition.

## 7. Environment

Windows 11, PowerShell + Git Bash. Python 3.11.9, Node 24, git, gh (logged in as `meet-png`, commits use the
GitHub no-reply email). **No Docker, no WSL, no uv.** PyPI downloads are flaky here — use
`pip install --default-timeout=120`. Windows `tar` (bsdtar 3.8) extracts `.7z`. Run analysis scripts with
`python -P` (not `-I`: `-I` hides user site-packages where pandas deps live). ARM measurements run in CI on GitHub's
free Arm64 runner (public repo): `gh workflow run arm-bench.yml`, results via `gh run download <id> -n arm-bench`.

## 8. Open items / blockers

- [x] Repo public (2026-10-28), topics set. Old history still contains removed competitor notes and a 6 MB VED extract
      — rewrite only if Meet asks (force-push).
- [ ] Meet: review `build/EdgeCar_TELIPORT_R1.pptx` (+ PDF) and upload on the portal **before 31 Oct**.
- [ ] Optional: a 2-minute demo recording (screen capture of `edgecar brief`) linked from the README.
- [ ] Teammate/outsiders: write a fresh paraphrase + refusal question set (the current sets are author-written).

## 9. Next steps (R2, if shortlisted: 14 Nov → 13 Dec)

1. Fresh evaluation sets written by people outside the project (paraphrases, refusals, ambiguous follow-ups).
2. Measure on a Cortex-A76-class core (Pi 5 / Graviton2): estimate is 2–3 s+ for the model path; levers: weight
   repacking (1.6× prompt speed measured, +~0.6 GB), Qwen2.5-0.5B for the entry tier, LoRA fine-tuning.
3. IVI-style UI with push-to-talk (on-device ASR), evidence cards; service-engineer timeline + export.
4. DuckDB FTS over log text; camera perception metadata (object lists, visibility flags) + clip references.
5. Weighted fault-propagation links (FMEA likelihoods); more fault families (charging, ADAS sensor degradation).
6. 10-slide R2 deck; demo video.

## How to reproduce the numbers

```bash
python -m edgecar.prep.fetch all                    # public datasets + model, SHA-256 pinned (TUM i3: manual)
python -m edgecar.prep.build                        # data/edgecar.duckdb
python -m pytest -q                                 # 82 tests
python -m edgecar.bench.nlu_eval --questions eval/test.jsonl --mode hybrid \
       --model models/qwen2.5-1.5b-instruct-q4_k_m.gguf          # held-out accuracy (also refusal_test.jsonl)
python -m edgecar.bench.e2e --threads 2                           # latency + memory, all 192 questions
python -m edgecar.bench.coldstart --threads 2                     # ignition: rules / model, prefix computed vs restored
python -m edgecar.bench.rootcause --seed 1000 --n 200             # root cause on random fault scenarios
python -m edgecar.bench.storage                                   # tier sizes, retention, query latency over a year
edgecar brief                                                     # the five brief questions, route + latency
```

## Verified results (for the slide)

**ARM** (Neoverse-N2, 2 cores, whole AI zone in a 1,792 MB cgroup, live ingestion running; CI run 40682528585,
files in `eval/results/arm/`): rules p50 5.7 ms / p95 16 ms; model p50 0.92 s / p95 1.35 s / max 1.35 s; 100 % of
192 < 2 s; peak 1,577 MB (anon 330 MB) · 0 OOM; cold start: rules 0.65 s, model ready 0.77 s restored vs 56.7 s
computed; held-out 94.3 %; live commit p50 40 ms; llama-bench 2 threads: pp 34 tok/s (repack 55–61), tg 15 (18).

**Accuracy:** held-out 95.7 % (x86; 92.9 % on the first run with the decline checks, see ADR-0008); dev 93.4 %;
refusal test 40/40 (baseline 29/40); refusal dev 19/20.

**Root cause:** 197/200 random scenarios (earliest-fault rule 34/200, map only 161/200); events recovered 2,409/2,409.

**Brief questions** (`eval/results/brief.json`, x86 2 threads): all five on the rules path, 5–16 ms.
- Range (Leaf 455, last 14 days to 4 Nov 2018): **161 → 151 km**; consumption 13.3 → 14.1 kWh/100 km; colder air,
  battery and tyres ≈ −7 km; cabin heating ≈ −5 km; other factors offset ≈ +2 km (less stop-and-go; driving style).
  The answer now adds up (the slide used to show −7 and −5 only).
- Worst efficiency day (Leaf 455): Sun 7 Jan 2018, −14.2 °C, 2.77 vs typical 5.93 km/kWh (−53 %).
- ADAS disengagements yesterday (PHEV 457): 7 events.
- Overheat (i3, Wed 24 Jul 2019): root cause **P0534 A/C refrigerant charge loss**, first logged by QNX slog2 at
  13:11:00, 14.5 min before the warning → P0C32 (BMS) → power derate (SOME/IP) → warning 13:25:33 (VHAL); 32 → 55 °C.
- City vs highway (i3): 9.2 vs 17.7 kWh/100 km.

**Storage:** one real car-year (Leaf 455, 52 weeks, 297 trips, 48 h of driving) = 5.5 MB (1 Hz 3.9, 1-min 0.8,
trips+events 0.8); 2.2 MB under the retention policy; questions over the year 3.5–22 ms.

**Six-model comparison** (x86, before the decline checks): Qwen2.5-1.5B 85.4 % model-only / 95.7 % hybrid; next best
SmolLM2-1.7B 50.5 % / Gemma-3-1B 85.7 % hybrid. Licences: Qwen2.5-1.5B Apache-2.0; Qwen2.5-3B research-only.

---

## R1 external review (2026-10-28) — checklist

An independent reviewer (another model, judging as a panel would) scored the R1 slide ~44/70 and listed 16 issues
plus slide-design notes. What was done:

| # | Issue | Status |
|---|---|---|
| 1 | x86 numbers under "Measured, not claimed" | ✅ ARM64 in CI: 2 cores, 1.75 GB cgroup, rules vs model path, cold start |
| 2 | Range breakdown doesn't add up (−7 −5 ≠ −10) | ✅ engine reports the offsetting +2 km; answer and slide add up |
| 3 | P0C73 can't explain an i3 battery overheat | ✅ fault chain P0534 → P0C32 → P0A7E (refrigerant-cooled pack) |
| 4 | "Listen-only" vs UDS | ✅ passive decoding of the gateway's polls; gateway-side read-only poll list otherwise |
| 5 | Root cause on one self-injected scenario | ✅ 200 random scenarios with distractors + decoys: 197/200 |
| 6 | Query accuracy ≠ answer accuracy; refusals; who wrote the questions | ✅ refusal set 40/40 (29/40 before); authorship disclosed. Answer-level eval vs independent ground truth: R2 |
| 7 | RAM figure leaves out OS, ingest, DB | ✅ whole AI zone 1.58 GB in a 1.75 GB cgroup, 256 MB left for the OS |
| 8 | Hardware, OS, hypervisor, FFI | ✅ docs/ARCHITECTURE.md §1, slide |
| 9 | Simulation missing from the architecture | ✅ replay & fault-injection block feeding the same interfaces |
| 10 | Camera feeds | ✅ design: perception metadata + clip references, no raw video (ARCHITECTURE §2) |
| 11 | Retention period | ✅ measured tiers, retention policy, power-loss test, erasure |
| 12 | Time sync, internal links, search, no vector DB | ✅ ARCHITECTURE §2–3 |
| 13 | User interaction design | ✅ driver / service / fleet / validation surfaces (ARCHITECTURE §6, slide) |
| 14 | Validation teams, production rollout | ✅ ARCHITECTURE §6, §9 |
| 15 | Security specifics | ✅ HSM/TEE key, secure boot, signed OTA incl. model, read-only by construction, DPDP/GDPR erasure |
| 16 | Model comparison + scaling tiers + licences | ✅ README table with licences/sizes; tiers in ARCHITECTURE §10 |
| — | Slide design (text size, hierarchy, contrast, fonts, links, QR, title) | ✅ redesigned (see session log) |
| — | Demo video + QR | ⏳ optional — needs a screen recording |

---

## Session log

### 2026-10-28 — Session 3: acting on an external review of the R1 slide
- Verified the review's claims before acting: P0C73 = motor-electronics coolant pump (inverter loop); the i3's pack is
  cooled directly by refrigerant; P0C32 = battery pack cooling system performance; P0534 = A/C refrigerant charge loss;
  GitHub Arm64 runners are free for public repos (Cobalt 100 = Neoverse-N2); AI HAT+ 2 lists Qwen2.5-1.5B; Qwen2.5-3B
  is research-licensed.
- Fixed: range answer adds up (offsetting factors reported); overheat fault chain; **a real data bug** found while
  testing the brief questions on every car — two TUM recordings include a charging session, giving −17.42 km/kWh;
  standstill negative power is now charging, excluded from driving energy and reported as a charging event.
- Cross-platform bug: TD2D glob order differs by OS → packaged, sorted TD2D table.
- Built: `prep.fetch` (pinned, TD2D via HTTP range with 429 back-off), build without the gated i3 data, prompt-prefix
  KV save/restore (ignition 57 s → 0.8 s on ARM), `bench.coldstart`, Arm64 CI workflow (cgroup, page cache dropped,
  live ingestion at real-time pace), decline checks (`nlu/guard.py`), random fault scenarios + `bench.rootcause`,
  fault-propagation map (`diagnosis.py`), `bench.storage`, `Store.wipe`, crash test, the `edgecar` CLI (pyproject
  declared it but `cli.py` was missing), ARCHITECTURE.md, ADR-0007/0008.
- Honest notes kept in the docs: refusal checks designed after seeing baseline failures; held-out set exposed two
  vocabulary gaps (92.9 % → 95.7 % after generic fixes); ARM held-out 94.3 % (one answer flips between CPUs).

### 2026-10-28 — Session 2: closing gaps before the slide
- Gap audit against the brief → all closed except ARM hardware and a UI.
- Protocols on the wire (ADR-0006); data quality (TUM overlaps, plausibility ranges); encoder bugs caught by tests.
- Encryption at rest + privacy roles; live ingestion; six-model comparison; R1 slide v2; repo made public.

### 2026-10-27 — Session 1: brief, research, data acquisition
- Read the brief and template; researched prior art; downloaded and profiled VED, TUM, Leaf DBCs, BATON, TD2D, NHTSA.
- Built the schema, converters, DuckDB store, detectors, typed query, analytics engine, ADAS simulator, NLU (rules +
  llama-server SLM), eval harness (dev 122, held-out 70) and the e2e benchmark.
- Findings: Qwen2.5-0.5B few-shot too weak; JSON output invites copying/arithmetic errors → verb format + code
  windows; weight repacking costs ~630 MB private RAM → `--no-repack` edge profile.
