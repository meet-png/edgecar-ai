# Arm64 edge benchmark (GitHub Actions)

- Runner: GitHub `ubuntu-24.04-arm` (Arm Neoverse-N2), pinned to CPUs 0–1.
- The whole AI zone — live CAN + SOME/IP ingestion, DuckDB, the assistant and llama-server — in one cgroup:
  `MemoryMax=1792M` (1,792 MiB), `MemorySwapMax=0`, page cache dropped first.
- Store rebuilt in CI from public data only (VED Leafs as real Leaf CAN, PHEV, ADAS over SOME/IP); the TU Munich i3
  data needs a login, so the overheat scenario is not part of the CI store. The 192 questions are asked of Leaf 455.
- A push to `main` that touches `src/`, `eval/*.jsonl`, `scripts/ci/` or the workflow re-runs it for the default model;
  other models run by hand (`gh workflow run arm-bench.yml -f model=edgecar-0.5b-q4_k_m.gguf`). See
  [`.github/workflows/arm-bench.yml`](../../../.github/workflows/arm-bench.yml).

| Folder | Model | Run |
|---|---|---|
| [`qwen2.5-1.5b-instruct-q4_k_m/`](qwen2.5-1.5b-instruct-q4_k_m) | Qwen2.5-1.5B-Instruct, few-shot | [40848224856](https://github.com/meet-png/edgecar-ai/actions/runs/40848224856), 2026-10-29, inclusive percentiles (runs 40764935436 and 40682528585 measured the same latency and memory within 2 %) |
| [`edgecar-0.5b-q4_k_m/`](edgecar-0.5b-q4_k_m) | edgecar-0.5b (ours, fine-tuned) | [40882785424](https://github.com/meet-png/edgecar-ai/actions/runs/40882785424), 2026-10-29, commit 49483b1 |
| [`edgecar-1.5b-q4_k_m/`](edgecar-1.5b-q4_k_m) | edgecar-1.5b (ours, fine-tuned) | [40882791686](https://github.com/meet-png/edgecar-ai/actions/runs/40882791686), 2026-10-29, commit 49483b1 |

Files in each folder: `summary.json` (headline numbers), `e2e.json` (every question), `heldout.json`, `coldstart.json`,
`cgroup.json` (memory peaks in MiB and limit events), `live.txt` (ingestion running alongside),
`llama-bench-repack*.json` (raw model throughput with and without weight repacking), `cpu.txt` (`lscpu`).

| Model | Model path p50 / p95 | Peak (anonymous) | Model ready, cold | Held-out |
|---|---|---|---|---|
| Qwen2.5-1.5B few-shot | 0.93 / 1.34 s | 1,578 MiB (330) | 56.7 s (0.77 s from a saved prompt cache) | 94.3 % |
| edgecar-0.5b | **0.34 / 0.43 s** | **827 MiB (266)** | **2.3 s** | 92.9 % |
| edgecar-1.5b | 0.79 / 1.15 s | 1,416 MiB (293) | 4.2 s | **95.7 %** |

All three: 100 % of 192 questions under 2 s, 0 OOM kills, rules path p50 5.6–6.1 ms.
