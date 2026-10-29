"""Generate src/edgecar/nlu/words.txt — the "is this a real English word?" list used by edgecar.nlu.normalise.

    python scripts/make_wordlist.py models/tuned/edgecar-0.5b/merged/tokenizer.json

Source: the byte-level BPE vocabulary of the Qwen2.5 tokenizer (Apache-2.0), which our models already ship. A word
that the tokenizer stores as one whole-word token (a leading-space token of letters only) is common written
English; a typo almost never is. Only words of four letters or more are kept (three-letter words have their own,
narrower rule in the repair). The output is sorted and deterministic.
"""

import json
import re
import sys
from pathlib import Path

vocab = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["model"]["vocab"]
words = sorted({t[1:].lower() for t in vocab if t.startswith("Ġ") and re.fullmatch(r"[A-Za-z]{4,}", t[1:])})
out = Path(__file__).resolve().parents[1] / "src/edgecar/nlu/words.txt"
header = "# Whole-word tokens of the Qwen2.5 tokenizer (Apache-2.0), 4+ letters — see scripts/make_wordlist.py\n"
out.write_text(header + "\n".join(words) + "\n", encoding="utf-8", newline="\n")
print(f"{len(words)} words -> {out}")
