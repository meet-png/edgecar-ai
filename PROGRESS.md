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
| ADR-0008 | **Decline instead of guessing**: action guard, not-recorded catalogue, grounding check. | Refusal 29/40 → 39/40 (40/40 before a cue-matching bug was fixed, see the ADR). |
| ADR-0009 | **Our own command model**: LoRA on Qwen2.5-0.5B/1.5B, synthetic data checked for overlap with every eval set, GGUF. | 0.5B: 2.2× faster in 2.3× less memory, within one held-out question; 1.5B: most accurate (95.7 %, refusals 60/60). |
| ADR-0010 | **In-car app + on-device speech**: one CSP-locked page, evidence cards, driver/service/fleet views, Moonshine + Piper. | A demo people can see and use; nothing leaves the machine. |

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
AWS: Meet's account (free plan, $100 credits, region ap-southeast-2). AWS CLI v2.39 installed (winget); credentials
come from `aws login` (browser sign-in, temporary credentials, no access keys). Graviton runs use a throwaway
t4g.small + key pair + IP-restricted security group, all deleted afterwards (`scripts/graviton/run.sh`).
Laptop: i7-13620H (10 cores), 32 GB RAM, **RTX 3050 Laptop 6 GB** (driver 555.99, CUDA 12.5). Fine-tuning runs in a
separate venv `.venv-train` (torch 2.13.0+cu126, transformers 5.21, peft 0.21; `pip install -e .`), base weights in
`models/hf/` (Qwen2.5-0.5B/1.5B-Instruct, HF revisions recorded in the download log), outputs in `models/tuned/`.
The 1.5B needs `--grad-ckpt` to stay inside 6 GB (otherwise Windows spills to shared memory, ~5× slower). Speech:
`pip install sherpa-onnx==1.13.8` (2.0 dropped the espeak-ng phonemiser Piper needs) and `prep.fetch voice`. App
screenshots: `scripts/screenshots.py` drives the installed Edge through Playwright (`pip install playwright`).
Hugging Face: account **`meettttttttttt`** (Meet Kabra), signed in by Meet with `hf auth login` (OAuth, browser) in
`.venv-train` — never handle the token. **IPv6 is black-holed on this network**: curl falls back to IPv4, but Python
HTTP clients hang on huggingface.co's IPv6 addresses. Run Python Hub calls with name resolution limited to IPv4 (a
small wrapper that filters `socket.getaddrinfo` to `AF_INET`); hf_xet's own transfers are fine. Never kill
`llama-server` by image name: the other Claude session may be running one (use `SlmServer` or the PID).

## 8. Open items / blockers

- [x] Repo public (2026-10-28), topics set. Old history still contains removed competitor notes and a 6 MB VED extract
      — rewrite only if Meet asks (force-push).
- [x] R1 slide uploaded by Meet (2026-10-29). Shortlist expected before R2 opens on 14 Nov.
- [ ] Teammate/outsiders: write a fresh paraphrase + refusal question set (the current sets are author-written).
- [ ] **Two Claude sessions work on this repo (since 2026-10-29).** Session A (this log's main author): Hugging Face
      publishing (`train/cards.py`, `train/publish.py`), AWS Graviton runs, `prep/fetch.py`, CI and Graviton scripts,
      README / PROGRESS / ADR numbers, `nlu/slm.py`, `train/`. Session B (reviewer): app and privacy hardening
      (`app/server.py`, `privacy.py`, `voice.py`, `analytics/engine.py` for redaction marks) and evaluation rigour
      (bootstrap CIs, McNemar, a robustness suite in `eval/robustness/`) on branch `review/hardening` in the worktree
      `C:\Users\ACER\edgecar-ai-review`. B never pushes to main; A reviews and merges. Before editing, check
      `git log` and this file, and record decisions here.

## 9. Roadmap after R1

Goal: turn the competition entry into something people can see and use, that is measurably better than the
few-shot baseline, checked by people outside the project, and that works on real cars. Every item ends in a number
or an artefact.

| # | Track | Deliverable | Proof | Needs Meet |
|---|---|---|---|---|
| 1 | Model | ✅ x86 · ⏳ Hugging Face (Meet approved 2026-10-29), then ARM CI + Graviton — **Our own fine-tuned model**: LoRA on Qwen2.5-0.5B and 1.5B, synthetic training set checked for overlap with every eval set, GGUF Q4 | accuracy on dev, held-out and the outside set · Graviton2 model-path p95 (1.89 s today) · memory | approve publishing the weights |
| 2 | Credibility | ⏳ form ready — **Outside question set**: teammate + classmates write questions and trick requests in a form, frozen before any run | blind accuracy, reported whatever it is | share the form |
| 3 | Product | ✅ built, live panel next — **In-car app** (`edgecar serve`): push-to-talk with on-device speech in and out, answers with evidence (charts, root-cause timeline, event tables), driver / service / fleet views, live bus panel; no CDN, no cloud speech | voice round trip + memory on ARM | — |
| 4 | Visibility | 2-minute demo video + README GIF; one-command Docker image (amd64 + arm64) | — | voice-over; approve publishing |
| 5 | Real cars | **OBD-II**: own ELM327 reader + Torque Pro / Car Scanner log import — any OBD-II car (every BS-VI car in India) with a ~₹400 dongle; an ELM327 emulator replaying real VED trips for CI | a real drive | a car + dongle |
| 6 | Give back | Upstream the Leaf DBC heater-scale finding (PR with evidence); question sets + scorer as an open benchmark; write-up | merged PR, benchmark page | approve PRs and posts |
| 7 | R2 | 10-slide deck generated from results; prototype demo; stretch: Android Automotive emulator with a VHAL bridge | — | — |

Order: 1 and 2 now (training runs on the laptop's RTX 3050 while people write questions); 3 next — a smaller model
frees memory for speech on the 2 GB tier; then 4–6; 7 from 14 Nov if shortlisted.

Carried over: weight repacking (1.7× prompt speed on N1, +~0.6 GB) as another latency lever; DuckDB FTS over log
text; camera perception metadata + clip references; weighted fault links (FMEA likelihoods) and more fault families;
the known false refusal "Has the battery been running hotter lately?".

## How to reproduce the numbers

```bash
python -m edgecar.prep.fetch all                    # public datasets + model, SHA-256 pinned (TUM i3: manual)
python -m edgecar.prep.build                        # data/edgecar.duckdb
python -m pytest -q                                 # 108 tests
python -m edgecar.bench.nlu_eval --questions eval/test.jsonl --mode hybrid \
       --model models/qwen2.5-1.5b-instruct-q4_k_m.gguf          # held-out accuracy (also refusal_test.jsonl)
python -m edgecar.bench.e2e --threads 2                           # latency + memory, all 192 questions
python -m edgecar.bench.coldstart --threads 2                     # ignition: rules / model, prefix computed vs restored
python -m edgecar.bench.rootcause --seed 1000 --n 200             # root cause on random fault scenarios
python -m edgecar.bench.storage                                   # tier sizes, retention, query latency over a year
edgecar brief                                                     # the five brief questions, route + latency
```

## Verified results

**Fine-tuned command models (2026-10-29, x86 laptop, 2 threads, after the decline-check fix; `eval/results/rerun/`):**

| | Qwen2.5-1.5B few-shot | edgecar-0.5b | edgecar-1.5b |
|---|---|---|---|
| Held-out (70): rules + model · model alone | 94.3 % · 90.0 % | 92.9 % · 94.3 % | 95.7 % · 97.1 % |
| Dev (122): rules + model · model alone | 100 % · 82.8 % | 100 % · 99.2 % | 100 % · 99.2 % |
| Refusal test (40) · dev (20) | 39 · 19 | 39 · 19 | 40 · 20 |
| Model path p50 / p95 (e2e, 192 q) | 480 / 670 ms | 221 / 299 ms | 384 / 557 ms |
| llama-server RSS (private) · file · start-up | 1,157 (227) · 1,117 MB · 13.9 s | 499 (163) · 398 MB · 1.0 s | 1,123 (222) · 986 MB · 1.6 s |
| Q8_0: held-out rules + model · model alone · refusal test | – | 94.3 % · 95.7 % · 39 | 95.7 % · 97.1 % · 40 |

Latency and memory: re-run 2026-10-29 with corrected percentiles (inclusive definition; the first run's exclusive
percentiles read 470 / 669, 205 / 295, 393 / 562 ms).

Training: 3,795 questions (417 hand-written seeds + templates; 576 dropped for overlapping an eval set), LoRA r=16,
3 epochs, RTX 3050 6 GB: 0.5B 5.2 min (batch 32, lr 2e-4; **epoch 1 kept**, epoch 3 only tied it on dev), 1.5B
13.9 min (batch 16, lr 1e-4, gradient checkpointing; epoch 3 kept). Logs: `eval/results/train/`. Speech (laptop):
recognition 37–49 ms per spoken question, synthesis 0.4–0.6 s per sentence. **Not yet on ARM.**

**R1 numbers (as submitted on the slide):**

**AWS t4g.small** (2 Graviton2 cores = Neoverse-N1, 2 GiB total incl. OS, no swap, stock Ubuntu; files in
`eval/results/graviton/r1-2026-10-28/`): 100 % of 192 < 2 s; rules p50 10 ms / p95 24 ms; model p50 1.46 s / p95 1.89 s / max
1.90 s; brief questions 11–34 ms; whole machine peak 670 MiB in use of 1,835 MiB, 0 OOM (AI zone 1,520 MiB incl.
1,230 MiB model page cache); ignition: rules 1.07 s, model 1.53 s restored vs 81.4 s; held-out 94.3 %, refusals 40/40;
llama-bench pp 17.7 tok/s (30 repacked), tg 10.9. **The slide uses these numbers.**

**ARM CI** (Neoverse-N2, 2 cores, whole AI zone in a 1,792 MiB cgroup, live ingestion running; CI run 40848224856
after the decline-check fix, files in `eval/results/arm/qwen2.5-1.5b-instruct-q4_k_m/`): rules p50 5.6 ms / p95
15.7 ms; model p50 0.93 s / p95 1.34 s / max 1.36 s; 100 % of 192 < 2 s; peak 1,578 MiB = 1.54 GiB (anon 330 MiB)
· 0 OOM; cold start: rules 0.71 s, model ready 0.77 s restored vs 56.7 s computed; held-out 94.3 %; live commit p50
42 ms; llama-bench 2 threads: pp 34 tok/s (repack 55–61), tg 15 (18).

**Accuracy (R1 slide, before the decline-check fix):** held-out 95.7 % (x86; 92.9 % on the first run with the decline
checks, see ADR-0008); dev 93.4 %; refusal test 40/40 (baseline 29/40); refusal dev 19/20. Graviton2 scored 94.3 %
on the same build: the model's output differs on one question (t-cmp-7) between x86 and ARM.

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
| 1 | x86 numbers under "Measured, not claimed" | ✅ **AWS t4g.small (Graviton2, 2 GiB incl. OS)** + Arm64 CI on every push: rules vs model path, cold start |
| 2 | Range breakdown doesn't add up (−7 −5 ≠ −10) | ✅ engine reports the offsetting +2 km; answer and slide add up |
| 3 | P0C73 can't explain an i3 battery overheat | ✅ fault chain P0534 → P0C32 → P0A7E (refrigerant-cooled pack) |
| 4 | "Listen-only" vs UDS | ✅ passive decoding of the gateway's polls; gateway-side read-only poll list otherwise |
| 5 | Root cause on one self-injected scenario | ✅ 200 random scenarios with distractors + decoys: 197/200 |
| 6 | Query accuracy ≠ answer accuracy; refusals; who wrote the questions | ✅ refusal set 40/40 (29/40 before); authorship disclosed. Answer-level eval vs independent ground truth: R2 |
| 7 | RAM figure leaves out OS, ingest, DB | ✅ whole AI zone 1.54 GB in a 1.75 GB cgroup, 256 MB left for the OS |
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

### 2026-10-29 — Session 5: publishing the models; a code review from a second session
- Meet approved publishing the fine-tuned weights on Hugging Face (under Meet's account; Meet signs in with
  `hf auth login` in a separate terminal, so the token never passes through the chat) and a Graviton re-run.
  Plan: per model, `<user>/edgecar-<size>` (merged safetensors) and `<user>/edgecar-<size>-GGUF` (Q4_K_M, Q8_0,
  `commands.gbnf`), created private, SHA-256-checked, then public; cards rendered from `eval/results` by
  `python -m edgecar.train.cards` (`docs/models/`). Then ARM CI (now takes a model choice) and one t4g.small run
  measuring the stock, 0.5B and 1.5B models in turn.
- **Bug: p95 above the maximum.** `statistics.quantiles` defaults to the exclusive definition, which extrapolates for
  small samples (4 out-of-scope questions: p95 125.9 ms, max 119.4 ms). All reports now use `edgecar.stats.percentile`
  (inclusive). x86 e2e re-run: 0.5B 221 / 299 ms, 1.5B 384 / 557 ms, few-shot 480 / 670 ms.
- Q8_0 scored: 0.5B one held-out question better than Q4_K_M, 1.5B identical. `nlu_eval --no-guard` added (ablation:
  how well the model refuses on its own).
- A second Claude session reviewed the code (11 findings). Verified each against code and files before acting:
  privacy gaps in the app (chart / facts / window not redacted; client-chosen role; no Host check), unbounded
  `/api/listen`, voice loading → session B on `review/hardening`. CI mislabelling non-default models, stale ARM CI
  numbers in this file, GB vs GiB, undocumented 0.5B epoch and 1.5B hyperparameters, x86-vs-ARM output difference,
  lost Q8_0 runs (cp1252 crash before saving) → fixed here.
- Results folders now hold one subfolder per run: `eval/results/arm/<model>/`, `eval/results/graviton/<model>/`;
  the R1 Graviton run is `eval/results/graviton/r1-2026-10-28/`. Training logs: `eval/results/train/`.
- Merged from session B after review: app privacy (charts, facts and window follow the role; the server sets the
  widest view; trusted hosts + same-origin; bounded `/api/listen`), then three follow-ups found while merging (a raw
  0x01 byte instead of `\1` in the hour rounding, window ends rounded down to an empty window, wildcard hosts);
  `eval/results/significance.md` (Wilson intervals, exact McNemar: on the 110 blind questions no model is
  significantly better than the stock one — the fine-tuned gains are speed, memory and start-up);
  `robustness.md` (typos −14 questions, numbers spoken as words −8, real Piper → Moonshine voice loop −12, refusals
  40/40 throughout) and `calibration.md` (min-token confidence AUROC 0.79, too blunt to gate on).
- Published: `meettttttttttt/edgecar-0.5b`, `-0.5b-GGUF`, `-1.5b` (+ collection "EdgeCar AI"), each verified file by
  file before going public; HF shows the model tree Qwen2.5 → edgecar (fine-tune) → GGUF (quantised). The 1.5B GGUF
  upload stalled twice on hf_xet (network errors to the Xet CAS server); retried over classic LFS.

### 2026-10-29 — Session 4: after R1 — our own model, the in-car app, two real bugs
- Meet submitted the R1 slide. New goal: make the project matter beyond the competition — roadmap in §9.
- **Fine-tuned command models** (ADR-0009): `edgecar/train/` builds 3,795 overlap-checked synthetic questions, trains
  LoRA on the laptop's RTX 3050 (loss on answer tokens only — full-vocabulary logits spilled out of 6 GB), exports
  GGUF with llama.cpp's converter. edgecar-0.5b: 2.3× faster and smaller than the few-shot 1.5B, held-out 92.9 %
  vs 94.3 %; edgecar-1.5b: 95.7 %, refusals 60/60. Weights not published yet (needs Meet's go-ahead).
- **In-car app** (ADR-0010): `edgecar serve` — CSP-locked single page, SVG evidence (waterfall, trends, root-cause
  timeline), driver/service/fleet views, push-to-talk with Moonshine Tiny + Piper via sherpa-onnx 1.13.8 (2.0 drops
  the espeak-ng phonemiser; espeak-ng is GPL-3.0 — flagged for production). Screenshots: `scripts/screenshots.py`.
- **Bug: dead multi-word cues in the decline checks** (`re.escape` escapes spaces). Found because the tuned model's
  correct `now` for "What's my battery level?" was declined. Fixed + regression test; re-measured: dev 93.4 → 100 %,
  held-out 95.7 → 94.3 %, refusals 40/40 → 39/40. README and ADR-0008 carry the corrected numbers.
- **Bug: "than before" compared all time with nothing** ("compared with n/a"); now last 30 days vs the 30 before.
- Outside question set: protocol + form text in `eval/outside/README.md`, labelling rules in `eval/LABELLING.md`.
- CI can benchmark any model (`MODEL=` through `arm_bench.sh`); fine-tuned models need hosted weights first.
- 95 tests.

### 2026-10-28 — Session 3b: AWS Graviton2 (the reviewer's t4g.small)
- Meet created an AWS account (free plan). AWS CLI installed; `aws login` (browser, temporary credentials).
- First t4g.small: the llama.cpp arm64 build needs `libgomp1` (absent on the EC2 Ubuntu image) → benchmark died;
  the 3-hour self-terminate safety net shut the idle instance down. Fixed `run.sh`, relaunched.
- Second run (13 min): 100 % < 2 s, model-path p95 1.89 s / max 1.90 s, rules 10 ms, whole machine 670 MiB in use
  incl. OS, 0 OOM, held-out 94.3 %, refusals 40/40, brief questions 11–34 ms. My earlier projection for A76-class
  cores (2–3 s+) was too pessimistic; docs now carry the measurement.
- Slide v6 switched to the Graviton numbers (1.89 s, 2 GB incl. OS, Graviton2 route times). Instance, volume, key
  pair and security group deleted; nothing left in the account.

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
- **R1 slide v6** (`scripts/make_r1_slide.py`, numbers read from `eval/results`): architecture as zones (safety domain,
  lab simulation, Android Automotive → AI zone with the data path and the question path drawn separately, each box
  carrying its measured number) + user surfaces; four hero numbers (1.35 s model-path p95 on ARM, 1.54 GB, 95.7 %,
  197/200); typical-vs-ours strip; the brief's five questions + a paraphrase + a refusal with route, time and the
  computed answer; footer with security, scaling, QR code and a live link. Calibri (the template's font) instead of
  Segoe UI; body 10–11.5 pt (was 8–9.5), hero numbers 26 pt; every text/fill pair ≥ 4.5:1 (lowest 6.2:1, checked in
  code); non-breaking spaces keep numbers with their units; no accent bars. Title slide: "Team Sidemen", the case
  title and the solution name. Validated (skill validator vs the template), rendered in PowerPoint, PDF exported
  (`scripts/finalize_deck.ps1`; PowerPoint would not embed Calibri, so the PDF is the reflow-proof copy).
- Unit slip caught and fixed: the cgroup reports MiB, so the peak is 1.54 GB (not 1.58) against the 1.75 GB limit.

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
