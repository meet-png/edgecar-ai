#!/usr/bin/env bash
# Runs inside the AI-zone cgroup (see arm_bench.sh). Everything started here is charged to that cgroup.
set -euo pipefail
CG="/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup)"
SERVER=tools/llama.cpp/llama-server
MODEL=models/qwen2.5-1.5b-instruct-q4_k_m.gguf
export LD_LIBRARY_PATH="$PWD/tools/llama.cpp${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
echo "cgroup $CG · memory.max $(cat "$CG/memory.max") · cpus $(cat "$CG/cpuset.cpus.effective" 2> /dev/null || nproc)"

python scripts/ci/cgroup_sampler.py "$CG" "$OUT/cgroup.json" &
SAMPLER=$!

# The car keeps driving: real Leaf CAN + ADAS SOME/IP traffic ingested at real-time pace, in the background.
python -m edgecar.ingest.live --vehicle leaf-455 --days 30 --pace 1 > "$OUT/live.txt" 2>&1 &
LIVE=$!

# Cold start, every question end to end, and the held-out accuracy — all while ingestion runs.
python -m edgecar.bench.coldstart --server "$SERVER" --model "$MODEL" --threads 2 --out "$OUT/coldstart.json"
python -m edgecar.bench.e2e --server "$SERVER" --model "$MODEL" --threads 2 --out "$OUT/e2e.json"
python -m edgecar.bench.nlu_eval --questions eval/test.jsonl --mode hybrid --server "$SERVER" --model "$MODEL" \
    --threads 2 --out "$OUT/heldout.json"

if [ "${RUN_REFUSAL:-0}" = "1" ]; then
    python -m edgecar.bench.nlu_eval --questions eval/refusal_test.jsonl --mode hybrid --server "$SERVER" \
        --model "$MODEL" --threads 2 --out "$OUT/refusal.json"
fi
if [ "${RUN_BRIEF:-0}" = "1" ]; then  # the case study's questions on every demo car (needs the TU Munich i3 data)
    edgecar brief --server "$SERVER" --model "$MODEL" --threads 2 --out "$OUT/brief.json"
fi

kill -TERM "$LIVE" && wait "$LIVE" || true
kill -TERM "$SAMPLER" && wait "$SAMPLER" || true
cat "$OUT/live.txt"
