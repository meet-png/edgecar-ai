# Arm64 edge benchmark — published run

- Run: https://github.com/meet-png/edgecar-ai/actions/runs/40682528585 (2026-10-28, commit "Storage: one real car-year is 5.5 MB…")
- Runner: GitHub `ubuntu-24.04-arm` (Arm Neoverse-N2), pinned to CPUs 0–1.
- The whole AI zone in one cgroup: `MemoryMax=1792M`, `MemorySwapMax=0`, page cache dropped first.
- Store rebuilt in CI from public data only (VED Leafs as real Leaf CAN, PHEV, ADAS over SOME/IP); the TU Munich i3
  data needs a login, so the overheat scenario is not part of the CI store. The 192 questions are asked of Leaf 455.
- Every push to `main` that touches `src/`, `eval/*.jsonl` or the workflow re-runs it; see `.github/workflows/arm-bench.yml`.

Files: `summary.json` (headline numbers), `e2e.json` (every question), `heldout.json`, `coldstart.json`,
`cgroup.json` (memory peaks and limit events), `live.txt` (ingestion running alongside), `llama-bench-repack*.json`
(raw model throughput with and without weight repacking), `cpu.txt` (`lscpu`).
