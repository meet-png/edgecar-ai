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
| [`qwen2.5-1.5b-instruct-q4_k_m/`](qwen2.5-1.5b-instruct-q4_k_m) | Qwen2.5-1.5B-Instruct, few-shot | [40764935436](https://github.com/meet-png/edgecar-ai/actions/runs/40764935436), 2026-10-29, after the decline-check fix of ADR-0008 (the earlier run 40682528585 measured the same latency and memory within 1 %) |

Files in each folder: `summary.json` (headline numbers), `e2e.json` (every question), `heldout.json`, `coldstart.json`,
`cgroup.json` (memory peaks in MiB and limit events), `live.txt` (ingestion running alongside),
`llama-bench-repack*.json` (raw model throughput with and without weight repacking), `cpu.txt` (`lscpu`).
