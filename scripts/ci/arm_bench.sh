#!/usr/bin/env bash
# Run the whole AI zone — live ingestion, DuckDB, the assistant and the model server — inside one cgroup with two
# CPU cores and a hard memory limit, and record what it used. Called by .github/workflows/arm-bench.yml.
set -euo pipefail
LIMIT_MB=${LIMIT_MB:-1792}  # a 2 GB budget minus 256 MB left for the guest OS
CPUS=${CPUS:-0,1}
OUT=${OUT:-eval/results/arm}
mkdir -p "$OUT"
lscpu > "$OUT/cpu.txt"
uname -srm >> "$OUT/cpu.txt"

sync && echo 3 | sudo tee /proc/sys/vm/drop_caches > /dev/null  # start cold: the model and the store come from disk
sudo systemd-run --scope --quiet --unit="edgecar-ai-zone-$$" \
    -p MemoryMax="${LIMIT_MB}M" -p MemorySwapMax=0 -p AllowedCPUs="$CPUS" \
    --uid="$(id -u)" --gid="$(id -g)" \
    -- /usr/bin/env PATH="$PATH" HOME="$HOME" OUT="$OUT" LIMIT_MB="$LIMIT_MB" \
    taskset -c "$CPUS" bash scripts/ci/ai_zone.sh
