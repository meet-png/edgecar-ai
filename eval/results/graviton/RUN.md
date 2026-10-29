# AWS Graviton2 edge benchmark

## R1 run — [`r1-2026-10-28/`](r1-2026-10-28), the numbers on the R1 slide

Measured before the decline-check fix of ADR-0008 (it changes accuracy, not latency or memory).

- **Machine:** AWS EC2 `t4g.small` (ap-southeast-2b), 2 vCPUs **Arm Neoverse-N1 (Graviton2, the Cortex-A76
  generation)**, 2 GiB RAM (1,835 MiB usable), **no swap**, stock Ubuntu 24.04 arm64 (kernel 7.0.0-1014-aws),
  Python 3.12.3, CPU credits "unlimited" so the burstable instance was never throttled during the run.
- **Date:** 2026-10-28, repository commit `997ea60`, llama.cpp b11577 (official ubuntu-arm64 build, SHA-256 checked),
  Qwen2.5-1.5B-Instruct Q4_K_M (SHA-256 checked).
- **What ran:** `scripts/graviton/run.sh` → `scripts/ci/arm_bench.sh` with `LIMIT_MB=0` — no artificial memory
  limit: the machine's 2 GiB, operating system included, is the limit. The AI zone ran in one systemd scope pinned to
  both cores (for accounting); live CAN + SOME/IP ingestion ran at real-time pace throughout; the page cache was
  dropped first, so the model and the store were read from disk.
- **Data:** the full demo store (including the TU Munich i3 data and the overheat scenario) and the Leaf 455 bus
  captures were copied to the instance by the operator — they are not redistributed. The instance, its volume, key
  pair and security group were deleted after the run.

| | Result |
|---|---|
| Questions answered in under 2 s | 100 % of 192 |
| Rules path (146 questions) | p50 9.9 ms · p95 24 ms · max 34 ms |
| Model path (34 questions) | p50 1.46 s · p95 1.89 s · max 1.90 s |
| The brief's five questions (`brief.json`) | 11–34 ms each, rules path; a paraphrase on the model path 1.55 s |
| Whole machine, OS included | peak 670 MiB in use (MemTotal − MemAvailable) of 1,835 MiB · 0 OOM |
| AI zone (scope) | peak 1,520 MiB = 335 MiB anonymous + 1,230 MiB page cache (the memory-mapped model) |
| Ignition | first answer 1.07 s (rules) · model ready 1.53 s from the saved prompt cache, 81.4 s computing it |
| Held-out accuracy · refusal set | 94.3 % (66/70) · 40/40 declined |
| Live ingestion while the model is busy | one second of bus traffic committed in p50 77 ms · p95 100 ms |
| llama-bench, 2 threads | prompt 17.7 tok/s (30 with weight repacking) · generation 10.9 tok/s (14.2) |

Files: `summary.json`, `e2e.json` (every question), `heldout.json`, `refusal.json`, `brief.json`, `coldstart.json`,
`cgroup.json`, `live.txt`, `llama-bench-repack*.json`, `cpu.txt`.
