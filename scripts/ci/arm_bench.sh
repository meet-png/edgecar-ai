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

# Raw model throughput on the same two cores, with and without weight repacking (repacking is faster on ARM but
# keeps a private copy of the weights, so the edge profile turns it off). Outside the cgroup: repack would not fit.
for repack in 0 1; do
    LD_LIBRARY_PATH="$PWD/tools/llama.cpp" taskset -c "$CPUS" tools/llama.cpp/llama-bench \
        -m models/qwen2.5-1.5b-instruct-q4_k_m.gguf -t 2 -p 32,128 -n 8 -r 3 --repack "$repack" -o json \
        > "$OUT/llama-bench-repack$repack.json" || echo "llama-bench (repack $repack) failed"
done

# LIMIT_MB=0: no artificial limit — on a real 2 GiB machine the hardware is the limit, operating system included
LIMITS=(-p AllowedCPUs="$CPUS")
if [ "$LIMIT_MB" != "0" ]; then LIMITS+=(-p MemoryMax="${LIMIT_MB}M" -p MemorySwapMax=0); fi

sync && echo 3 | sudo tee /proc/sys/vm/drop_caches > /dev/null  # start cold: the model and the store come from disk
sudo systemd-run --scope --quiet --unit="edgecar-ai-zone-$$" "${LIMITS[@]}" \
    --uid="$(id -u)" --gid="$(id -g)" \
    -- /usr/bin/env PATH="$PATH" HOME="$HOME" OUT="$OUT" LIMIT_MB="$LIMIT_MB" \
    RUN_BRIEF="${RUN_BRIEF:-0}" RUN_REFUSAL="${RUN_REFUSAL:-0}" MODEL="${MODEL:-models/qwen2.5-1.5b-instruct-q4_k_m.gguf}" \
    taskset -c "$CPUS" bash scripts/ci/ai_zone.sh
