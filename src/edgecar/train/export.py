"""Export a fine-tuned model to GGUF for llama.cpp: convert the merged weights, quantize, record checksums.

    python -m edgecar.train.export --merged models/tuned/edgecar-0.5b/merged --name edgecar-0.5b

Uses llama.cpp's own converter from the same release as the runtime, fetched once into ``tools/``. The output name
starts with ``edgecar-``, which is how the runtime knows to use the short fine-tuned prompt.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

LLAMA_TAG = "b11577"  # the llama.cpp release the runtime uses (CI, Graviton, laptop)
SRC_URL = f"https://github.com/ggml-org/llama.cpp/archive/refs/tags/{LLAMA_TAG}.tar.gz"
SRC_DIR = Path("tools/llama.cpp-src")


def converter() -> Path:
    script = SRC_DIR / "convert_hf_to_gguf.py"
    if not script.exists():
        print(f"fetching llama.cpp {LLAMA_TAG} sources for the converter")
        data = urllib.request.urlopen(SRC_URL, timeout=300).read()
        prefix = f"llama.cpp-{LLAMA_TAG}/"
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            members = [m for m in tar.getmembers() if m.name.startswith(prefix) and m.name != prefix.rstrip("/")]
            for m in members:
                m.name = m.name[len(prefix) :]
            tar.extractall(SRC_DIR, members=members, filter="data")
    return script


def quantize_binary() -> Path:
    for p in (Path("tools/llama.cpp/llama-quantize.exe"), Path("tools/llama.cpp/llama-quantize")):
        if p.exists():
            return p
    found = shutil.which("llama-quantize")
    if not found:
        raise FileNotFoundError("llama-quantize not found in tools/llama.cpp or on PATH")
    return Path(found)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merged", type=Path, required=True, help="merged Hugging Face model directory")
    ap.add_argument("--name", required=True, help="output name, e.g. edgecar-0.5b")
    ap.add_argument("--out", type=Path, default=Path("models"))
    ap.add_argument("--quants", nargs="+", default=["Q4_K_M", "Q8_0"])
    a = ap.parse_args()
    if not a.name.startswith("edgecar-"):
        raise SystemExit("the name must start with 'edgecar-' (the runtime picks the prompt by this prefix)")

    a.out.mkdir(parents=True, exist_ok=True)
    f16 = a.out / f"{a.name}-f16.gguf"
    env = os.environ | {"PYTHONPATH": str((SRC_DIR / "gguf-py").resolve()), "NO_LOCAL_GGUF": ""}
    subprocess.run(
        [sys.executable, str(converter()), str(a.merged), "--outfile", str(f16), "--outtype", "f16"],
        check=True,
        env=env,
    )
    manifest = {"name": a.name, "llama_cpp": LLAMA_TAG, "files": {}}
    for q in a.quants:
        out = a.out / f"{a.name}-{q.lower()}.gguf"
        subprocess.run([str(quantize_binary()), str(f16), str(out), q], check=True, capture_output=True)
        manifest["files"][q] = {"file": out.name, "bytes": out.stat().st_size, "sha256": sha256(out)}
        print(f"{q}: {out} ({out.stat().st_size / 1e6:.0f} MB)")
    f16.unlink()
    (a.out / f"{a.name}.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
