"""Publish our command models to the Hugging Face Hub (ADR-0009).

    python -m edgecar.train.cards --owner <user>      # render the model cards (docs/models/) first
    python -m edgecar.train.publish --owner <user> edgecar-0.5b edgecar-0.5b-GGUF edgecar-1.5b edgecar-1.5b-GGUF

Each name is a repository under the owner. ``edgecar-<size>`` gets the merged weights (safetensors, bf16) and the
tokenizer; ``edgecar-<size>-GGUF`` the llama.cpp files listed in ``models/<name>.json`` and the command grammar.
Publish a merged repository before its GGUF one: the GGUF card names it as its base model.

A new repository is created private and filled in one commit; it is made public only when every file the Hub
reports matches the local one (the SHA-256 of large files, the git blob hash of small ones). Sign in first with
``hf auth login``: this script uses the saved sign-in and never reads or prints a token.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from huggingface_hub import CommitOperationAdd, HfApi
from huggingface_hub.hf_api import RepoFile

from edgecar.nlu import command
from edgecar.prep.fetch import _sha256

MODELS = Path("models")
CARDS = Path("docs/models")
# The command model decodes greedily: a parser, not a writer. (The base model's defaults sample.)
GREEDY = {"bos_token_id": 151643, "do_sample": False, "eos_token_id": [151645, 151643], "pad_token_id": 151643}


def contents(repo: str) -> dict[str, Path | bytes]:
    """What goes into a repository: published path → local file or bytes."""
    files: dict[str, Path | bytes] = {"README.md": CARDS / f"{repo}.md", "LICENSE": Path("LICENSE")}
    if repo.endswith("-GGUF"):
        manifest = json.loads((MODELS / f"{repo.removesuffix('-GGUF')}.json").read_text(encoding="utf-8"))
        for entry in manifest["files"].values():
            path = MODELS / entry["file"]
            if path.stat().st_size != entry["bytes"] or _sha256(path) != entry["sha256"]:
                raise SystemExit(f"{path} does not match its manifest — re-run the export")
            files[entry["file"]] = path
        files["commands.gbnf"] = (command.model_grammar() + "\n").encode()
    else:
        merged = MODELS / "tuned" / repo / "merged"
        files |= {p.name: p for p in sorted(merged.iterdir()) if p.is_file()}
        files["generation_config.json"] = (json.dumps(GREEDY, indent=2) + "\n").encode()
    missing = [str(v) for v in files.values() if isinstance(v, Path) and not v.exists()]
    if missing:
        raise SystemExit(f"missing: {', '.join(missing)}")
    return files


def digests(item: Path | bytes) -> tuple[str, str]:
    """(SHA-256, git blob SHA-1) of a file or of bytes: the Hub reports the first for large files, the second for
    small ones."""
    size = len(item) if isinstance(item, bytes) else item.stat().st_size
    sha256, blob = hashlib.sha256(), hashlib.sha1(f"blob {size}\0".encode())
    if isinstance(item, bytes):
        sha256.update(item)
        blob.update(item)
    else:
        with item.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                sha256.update(chunk)
                blob.update(chunk)
    return sha256.hexdigest(), blob.hexdigest()


def verify(api: HfApi, repo_id: str, files: dict[str, Path | bytes]) -> list[str]:
    """Paths whose published content differs from the local one (empty when everything matches)."""
    remote = {f.path: f for f in api.get_paths_info(repo_id, list(files)) if isinstance(f, RepoFile)}
    bad = []
    for path, item in files.items():
        f = remote.get(path)
        sha256, blob = digests(item)
        if f is None or (f.lfs.sha256 != sha256 if f.lfs else f.blob_id != blob):
            bad.append(path)
    return bad


def publish(api: HfApi, repo_id: str, files: dict[str, Path | bytes], *, keep_private: bool) -> None:
    url = api.create_repo(repo_id, private=True, exist_ok=True)
    ops = [
        CommitOperationAdd(path_in_repo=k, path_or_fileobj=v if isinstance(v, bytes) else str(v))
        for k, v in files.items()
    ]
    size = sum(len(v) if isinstance(v, bytes) else v.stat().st_size for v in files.values())
    print(f"{repo_id}: uploading {len(files)} files, {size / 1e6:,.0f} MB", flush=True)
    api.create_commit(repo_id, ops, commit_message=f"{repo_id.split('/')[1]}: weights and model card")
    if bad := verify(api, repo_id, files):
        raise SystemExit(f"{repo_id}: published content differs for {', '.join(bad)} — left as it is, not made public")
    print(f"{repo_id}: all {len(files)} files verified", flush=True)
    if not keep_private and api.repo_info(repo_id).private:
        api.update_repo_settings(repo_id, private=False)
        print(f"{repo_id}: public — {url}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repos", nargs="+", help="edgecar-<size> or edgecar-<size>-GGUF")
    ap.add_argument("--owner", required=True, help="Hugging Face user or organisation")
    ap.add_argument("--card-only", action="store_true", help="update README.md only (after re-rendering the cards)")
    ap.add_argument("--keep-private", action="store_true")
    ap.add_argument("--collection", default="EdgeCar AI", help="collection to list the repositories in ('' for none)")
    a = ap.parse_args()

    api = HfApi()
    for repo in a.repos:
        files = contents(repo)
        if a.card_only:
            files = {"README.md": files["README.md"]}
        publish(api, f"{a.owner}/{repo}", files, keep_private=a.keep_private)
    if a.collection and not a.keep_private:
        slug = api.create_collection(
            a.collection,
            namespace=a.owner,
            description="On-device vehicle-data assistant: small models that turn a driver's question into a "
            "read-only command — github.com/meet-png/edgecar-ai",
            exists_ok=True,
        ).slug
        for repo in a.repos:
            api.add_collection_item(slug, f"{a.owner}/{repo}", "model", exists_ok=True)
        print(f"collection: https://huggingface.co/collections/{slug}")


if __name__ == "__main__":
    main()
