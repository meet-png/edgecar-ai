#!/usr/bin/env bash
# EdgeCar AI on a real edge-class machine: an AWS EC2 t4g.small — 2 Graviton2 vCPUs (Arm Neoverse-N1, the
# Cortex-A76 generation), 2 GiB of RAM in total, no swap. Run on a fresh Ubuntu 24.04 arm64 instance:
#
#     bash run.sh data-bundle.tar.gz
#
# Installs EdgeCar AI from GitHub plus the official llama.cpp b11577 arm64 build and the model (both SHA-256
# checked), unpacks the operator's store and bus captures (data/edgecar.duckdb, data/interim/wire/leaf-455.*,
# data/raw/leaf_dbc/), then runs the AI-zone benchmark with no artificial memory limit: the machine's 2 GiB,
# operating system included, is the limit. Results land in eval/results/graviton/.
set -euo pipefail
DATA=$(realpath "$1")

sudo apt-get -o DPkg::Lock::Timeout=900 update -qq  # first boot: unattended-upgrades may hold the lock
sudo DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=900 install -y -qq python3-venv > /dev/null
git clone -q https://github.com/meet-png/edgecar-ai.git "$HOME/edgecar-ai"
cd "$HOME/edgecar-ai"
git log --oneline -1
python3 -m venv .venv
. .venv/bin/activate
pip install -q --upgrade pip
pip install -q -e ".[prep]"

curl -fsSL -o /tmp/llama.tgz \
    https://github.com/ggml-org/llama.cpp/releases/download/b11577/llama-b11577-bin-ubuntu-arm64.tar.gz
echo "ea19b3ea15d2ae46ea4066685f41a9aaa2446a80145f6f05650ecdfbf6c86345  /tmp/llama.tgz" | sha256sum -c -
mkdir -p tools/llama.cpp && tar -xzf /tmp/llama.tgz -C tools/llama.cpp --strip-components=1
python -m edgecar.prep.fetch model
tar -xzf "$DATA"

free -m
LIMIT_MB=0 RUN_BRIEF=1 RUN_REFUSAL=1 OUT=eval/results/graviton bash scripts/ci/arm_bench.sh
python scripts/ci/summary.py eval/results/graviton
