"""Fetch the public datasets and the model from their original sources (nothing is redistributed by this repo).

    python -m edgecar.prep.fetch all                # everything that needs no login
    python -m edgecar.prep.fetch ved dbc --data data

==========  ==============================================================================================
ved         Vehicle Energy Dataset — 2 archives from github.com/gsoh/VED (Apache-2.0), extracted to raw/ved
dbc         Nissan Leaf DBC files — github.com/dalathegreat/leaf_can_bus_messages (GPL-3.0)
baton       BATON ADAS handover tables — github.com/OpenLKA/BATON via Git LFS (CC BY-NC 4.0)
td2d        TD2D takeover tables — only the 501 small CSVs, read out of the 16.7 GB Zenodo zip with HTTP range
            requests (CC BY 4.0)
model       Qwen2.5-1.5B-Instruct Q4_K_M GGUF — huggingface.co/Qwen (Apache-2.0)
tuned       our fine-tuned command models, edgecar-0.5b and edgecar-1.5b, Q4_K_M GGUF —
            huggingface.co/meettttttttttt (Apache-2.0, ADR-0009) — optional, not part of "all"
voice       on-device speech for the app: Moonshine Tiny recogniser (MIT) and a Piper voice trained on public-domain
            LibriVox recordings, both from github.com/k2-fsa/sherpa-onnx releases — optional, not part of "all"
==========  ==============================================================================================

The TU Munich BMW i3 data needs a free IEEE DataPort login and is downloaded by hand (see docs/DATASETS.md).
Downloads are checked against pinned SHA-256 digests where the source publishes a fixed file.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

import httpx

VED_ARCHIVES = [
    "https://github.com/gsoh/VED/raw/master/Data/VED_DynamicData_Part1.7z",
    "https://github.com/gsoh/VED/raw/master/Data/VED_DynamicData_Part2.7z",
]
LEAF_DBC = {
    "CAR-can_AZE0.dbc": "6b7c9275dbcd0282d3122873b50e420240458ec62191593f38b4bc878e98d628",
    "EV-can_AZE0.dbc": "a465e324a6c5929949c05e6e5daf2192ea9a420bcb480a1cb9fdc2153d29217a",
    "EV-can_ZE1.dbc": "33821816b4c981df8b815333bf3069e8d0b81e4c7b8921ed11d5deb225324940",
}
LEAF_DBC_URL = "https://raw.githubusercontent.com/dalathegreat/leaf_can_bus_messages/master/{}"
BATON = {
    "activation_events_or.csv": "6fb460acc08b97b377134f6a7e1fe89ad2d36af599a861e57829b1e0e036d5a1",
    "takeover_events_or.csv": "3af3c399022dcaa3afcad88d5dca920f05092eb15038fdd26d7641bfe4044aa2",
}
BATON_URL = "https://media.githubusercontent.com/media/OpenLKA/BATON/HEAD/benchmark_v2/{}"
TD2D_ZIP = "https://zenodo.org/api/records/14185964/files/TD2D.zip/content"
TD2D_FILES = ("takeoverScenarioInformations.csv", "driverInformation.csv")
MODEL = (
    "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf",
    "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e",
)
TUNED = {
    "edgecar-0.5b-q4_k_m.gguf": "749a466fca5bdd14adacf0d4750cb2d1deb5e6c12fba315851122d6161e2ef07",
    "edgecar-1.5b-q4_k_m.gguf": "d8cfdf453a7d3b4c76ebbd0ff7c9a93ad7b31ccf6f25bae4f32214a3d2e7897f",
}
TUNED_URL = "https://huggingface.co/meettttttttttt/{repo}-GGUF/resolve/main/{file}"
VOICE = {
    "asr-models/sherpa-onnx-moonshine-tiny-en-quantized-2026-02-27.tar.bz2": (
        "9ec31b342d8fa3240c3b81b8f82e1cf7e3ac467c93ca5a999b741d5887164f8d"
    ),
    "tts-models/vits-piper-en_US-kristin-medium-int8.tar.bz2": (
        "16289d7ee8e6b2311a0a0af6531a55f498f82499644a1bb6fddb991fe6fa950c"
    ),
}
VOICE_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/{}"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _get(client: httpx.Client, url: str, headers: dict | None = None, tries: int = 8) -> httpx.Response:
    """GET with polite retries: servers such as Zenodo rate-limit (HTTP 429) bursts of range requests."""
    for attempt in range(tries):
        r = client.get(url, headers=headers)
        if r.status_code != 429 and r.status_code < 500:
            r.raise_for_status()
            return r
        time.sleep(float(r.headers.get("retry-after", 0)) or min(60.0, 2.0**attempt))
    r.raise_for_status()
    return r


def download(client: httpx.Client, url: str, dest: Path, sha256: str | None = None) -> Path:
    if dest.exists() and (sha256 is None or _sha256(dest) == sha256):
        print(f"  ✓ {dest} (already present)")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    with client.stream("GET", url) as r, part.open("wb") as f:
        r.raise_for_status()
        for chunk in r.iter_bytes(1 << 20):
            f.write(chunk)
    if sha256 is not None and (got := _sha256(part)) != sha256:
        part.unlink()
        raise RuntimeError(f"{url}: SHA-256 {got} does not match the pinned {sha256}")
    part.replace(dest)
    print(f"  ↓ {dest} ({dest.stat().st_size / 1e6:.1f} MB)" + (" — SHA-256 verified" if sha256 else ""))
    return dest


def _extract_7z(archive: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for tool in (["7z", "x", "-y", f"-o{out}"], ["7zz", "x", "-y", f"-o{out}"], ["bsdtar", "-xf"], ["tar", "-xf"]):
        if shutil.which(tool[0]):
            cmd = [*tool, str(archive)] if tool[0].startswith("7z") else [*tool, str(archive), "-C", str(out)]
            if subprocess.run(cmd, capture_output=True).returncode == 0:
                return
    raise RuntimeError(f"cannot extract {archive}: install 7-Zip (7z) or libarchive (bsdtar)")


def fetch_ved(client: httpx.Client, raw: Path) -> None:
    out = raw / "ved"
    if len(list(out.glob("VED_*_week.csv"))) >= 54:
        print(f"  ✓ {out} (54 weekly files present)")
        return
    for url in VED_ARCHIVES:
        archive = download(client, url, raw / "ved_archives" / url.rsplit("/", 1)[1])
        _extract_7z(archive, out)
    for f in out.rglob("VED_*_week.csv"):  # flatten any folder inside the archives
        if f.parent != out:
            f.replace(out / f.name)
    print(f"  ✓ {out}: {len(list(out.glob('VED_*_week.csv')))} weekly files")


def fetch_voice(client: httpx.Client, out: Path) -> None:
    for rel, digest in VOICE.items():
        name = rel.rsplit("/", 1)[1]
        target = out / name.removesuffix(".tar.bz2")
        if target.exists():
            print(f"  ✓ {target} (already present)")
            continue
        archive = download(client, VOICE_URL.format(rel), out / name, digest)
        with tarfile.open(archive, "r:bz2") as tar:
            tar.extractall(out, filter="data")
        archive.unlink()


def fetch_pinned(client: httpx.Client, url: str, files: dict[str, str], out: Path) -> None:
    for name, digest in files.items():
        download(client, url.format(name), out / name, digest)


class _HttpRange(io.RawIOBase):
    """A read-only, seekable view of a remote file over HTTP range requests (enough for ``zipfile``)."""

    def __init__(self, client: httpx.Client, url: str) -> None:
        self.client, self.url, self.pos = client, url, 0
        r = _get(client, url, {"Range": "bytes=0-0"})
        self.url = str(r.url)  # follow any redirect once
        self.size = int(r.headers["content-range"].rsplit("/", 1)[1])

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        self.pos = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence] + offset
        return self.pos

    def readinto(self, b) -> int:
        if self.pos >= self.size:
            return 0
        end = min(self.pos + len(b), self.size) - 1
        r = _get(self.client, self.url, {"Range": f"bytes={self.pos}-{end}"})
        n = len(r.content)
        b[:n] = r.content
        self.pos += n
        return n


def fetch_td2d(client: httpx.Client, out: Path) -> None:
    if len(list(out.glob("TD2D/D*/*/takeoverScenarioInformations.csv"))) >= 500:
        print(f"  ✓ {out} (500 takeover tables present)")
        return
    with zipfile.ZipFile(io.BufferedReader(_HttpRange(client, TD2D_ZIP), buffer_size=1 << 16)) as z:
        members = [m for m in z.infolist() if m.filename.endswith(TD2D_FILES)]
        root = out.resolve()
        for m in members:
            dest = (out / m.filename).resolve()
            if not dest.is_relative_to(root):  # never write outside the target directory
                raise RuntimeError(f"unsafe path in archive: {m.filename}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(z.read(m))
    print(f"  ✓ {out}: {len(members)} files read from the remote archive (no full download)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", nargs="+", choices=["all", "ved", "dbc", "baton", "td2d", "model", "tuned", "voice"])
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--models", type=Path, default=Path("models"))
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # a Windows pipe cannot print "✓"; the download must not fail on it
    want = {"ved", "dbc", "baton", "td2d", "model"} if "all" in a.what else set(a.what)
    raw = a.data / "raw"
    with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(60.0, connect=20.0)) as client:
        if "ved" in want:
            print("VED")
            fetch_ved(client, raw)
        if "dbc" in want:
            print("Leaf DBC")
            fetch_pinned(client, LEAF_DBC_URL, LEAF_DBC, raw / "leaf_dbc")
        if "baton" in want:
            print("BATON")
            fetch_pinned(client, BATON_URL, BATON, raw / "baton")
        if "td2d" in want:
            print("TD2D")
            fetch_td2d(client, raw / "td2d")
        if "model" in want:
            print("model")
            url, digest = MODEL
            download(client, url, a.models / url.rsplit("/", 1)[1], digest)
        if "tuned" in want:
            print("tuned models")
            for name, digest in TUNED.items():  # edgecar-0.5b-q4_k_m.gguf lives in edgecar-0.5b-GGUF
                url = TUNED_URL.format(repo=name.rsplit("-", 1)[0], file=name)
                download(client, url, a.models / name, digest)
        if "voice" in want:
            print("voice")
            fetch_voice(client, a.models / "voice")
    if not (raw / "tum_bmw_i3").exists():
        print("note: the TU Munich BMW i3 data needs a free IEEE DataPort login — see docs/DATASETS.md")


if __name__ == "__main__":
    main()
