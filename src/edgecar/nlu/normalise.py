"""Repair a typed or transcribed question before it is understood.

People mistype, and speech recognisers write what they hear: "ten days" for "10 days", "hard breaking" for "hard
braking", "region" for "regen". The rules and the time-phrase reader expect the written forms, so a question is
repaired first, in three deterministic steps that never change a word the domain already knows:

1. **Spoken forms** — a small table of what a recogniser writes for domain words ("d t c" → "dtc", "adaptive crews"
   → "adaptive cruise", "kilowatt hours" → "kwh"). Every entry was taken from transcripts of the *dev* questions
   (``python -m edgecar.bench.robustness --dev --voice``), never from the blind sets.
2. **Number words** before a unit become digits ("thirty days" → "30 days", "fifteen minutes" → "15 minutes"), so
   windows and look-backs are read the same way however the number was written.
3. **Spelling** — a word the vocabulary does not contain is replaced by a vocabulary word one edit away (swapped
   neighbours first, the commonest slip; two edits for words of nine letters or more; for three-letter words only a
   missing letter) when one candidate clearly dominates — the only one, or twice as frequent as the next. The
   vocabulary is the code's own lexicon (rules, time reader, decline checks, command language) plus our training
   questions (``VOCAB_SOURCES``); no outside word list, no evaluation question. A typo is never repaired *into* a
   verb that asks the car to act (``CONTROL_VERBS``): a repair must not turn a question into a refusal. Nor is a
   real word repaired: common English words (``real_words``) and inflections of known stems are left as they are.

Kept conservative on purpose: an ambiguous or distant word is left alone, and the decline checks still see the
original question as well (see ``edgecar.nlu.pipeline``).
"""

from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

from edgecar.nlu import guard

_PKG = Path(__file__).resolve().parents[1]  # src/edgecar
# The words the rules, the time reader and the decline checks match, and our own training questions — all shipped
# with the package; no outside word list, no evaluation question.
VOCAB_SOURCES = (
    _PKG / "nlu/fastpath.py",
    _PKG / "nlu/guard.py",
    _PKG / "nlu/command.py",
    _PKG / "train/seeds.txt",
)

# What a recogniser writes → the written form. From dev transcripts only (see the module docstring).
SPOKEN = [
    (r"\bkilowatt[- ]hours? per (?:one )?hundred kilomet(?:er|re)s\b", "kwh/100 km"),
    (r"\bkilomet(?:er|re)s per kilowatt[- ]hour\b", "km/kwh"),
    (r"\bkilowatt[- ]hours?\b", "kwh"),
    (r"\bd t cs?\b", "dtc"),
    (r"\ba das\b", "adas"),
    (r"\bs o c\b", "soc"),
    (r"\bhard[- ]break(?:ing|s)?\b", "hard braking"),
    (r"\bharsh break(?:ing|s)?\b", "harsh braking"),
    (r"\bbreaking\b", "braking"),
    (r"\bregion (energy|trend|braking)\b", r"regen \1"),
    (r"\badaptive crews\b", "adaptive cruise"),
    (r"\bdriver? (?:resistance|assistants?) disagagements?\b", "driver assistance disengagement"),
    (r"\bdisagagements?\b", "disengagement"),
    (r"\bdisoggaged\b", "disengaged"),
    (r"\bproceeded\b", "preceded"),
]

_ONE_WORDS = (
    "zero one two three four five six seven eight nine ten "
    "eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen"
)
_TEN_WORDS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety"
_ONES = {w: i for i, w in enumerate(_ONE_WORDS.split())}
_TENS = {w: 10 * i for i, w in enumerate(_TEN_WORDS.split()) if i > 1}
_UNIT = r"(?=\s+(?:days?|weeks?|months?|years?|minutes?|mins?|hours?|trips?|km|kilomet))"
_NUMBER = re.compile(
    r"\b(?:(" + "|".join(_TENS) + r")(?:[\s-](" + "|".join(k for k in _ONES if 0 < _ONES[k] < 10) + r"))?"
    r"|(" + "|".join(_ONES) + r"))\b" + _UNIT
)


def _numbers(text: str) -> str:
    def value(m: re.Match) -> str:
        if m.group(3):
            return str(_ONES[m.group(3)])
        return str(_TENS[m.group(1)] + (_ONES[m.group(2)] if m.group(2) else 0))

    return _NUMBER.sub(value, text)


def _words_in(path: Path) -> list[str]:
    text = re.sub(r"\\[a-zA-Z]", " ", path.read_text(encoding="utf-8"))  # regex escapes such as \b are not words
    return re.findall(r"[a-z]+", text.lower())


@lru_cache(maxsize=1)
def _code_words() -> frozenset[str]:
    return frozenset(w for path in VOCAB_SOURCES if path.suffix == ".py" for w in _words_in(path))


@lru_cache(maxsize=1)
def vocabulary() -> Counter[str]:
    """Repair targets, with how often each occurs: the training questions' words, and the code's words that are
    real words (not regex stems such as "declin" or identifiers such as "endswith")."""
    seeds = Counter(w for path in VOCAB_SOURCES if path.suffix != ".py" for w in _words_in(path))
    code = Counter(w for path in VOCAB_SOURCES if path.suffix == ".py" for w in _words_in(path))
    return seeds + Counter({w: n for w, n in code.items() if w in seeds or w in real_words()})


@lru_cache(maxsize=1)
def _known() -> frozenset[str]:
    """Never repaired: every target, every word in the code (domain words like "defrost" or "tpms" included), and
    common English."""
    return frozenset(vocabulary()) | _code_words() | real_words()


# Never a repair target: the verbs the decline checks treat as a request to act.
CONTROL_VERBS = frozenset(re.findall(r"[a-z]+", guard._ALWAYS + "|" + guard._WITH_OBJECT))


_LETTERS = "abcdefghijklmnopqrstuvwxyz"


def _edits(w: str) -> tuple[set[str], set[str]]:
    """One edit away: (swapped neighbours — the commonest typo, other single edits)."""
    splits = [(w[:i], w[i:]) for i in range(len(w) + 1)]
    swaps = {a + b[1] + b[0] + b[2:] for a, b in splits if len(b) > 1}
    others = {
        *(a + b[1:] for a, b in splits if b),  # a letter too many
        *(a + c + b[1:] for a, b in splits if b for c in _LETTERS),  # a wrong letter
        *(a + c + b for a, b in splits for c in _LETTERS),  # a letter missing
    }
    return swaps, others - swaps


def _pick(candidates: set[str]) -> str | None:
    """The candidate, if one clearly dominates: the only one, or at least twice as common as the next."""
    ranked = sorted(candidates, key=lambda c: -vocabulary()[c])
    if len(ranked) == 1 or (ranked and vocabulary()[ranked[0]] >= 2 * vocabulary()[ranked[1]]):
        return ranked[0]
    return None


@lru_cache(maxsize=1)
def _targets() -> frozenset[str]:
    return frozenset(vocabulary().keys() - CONTROL_VERBS)


def _deletes(w: str, depth: int = 2) -> set[str]:
    out, frontier = {w}, {w}
    for _ in range(depth):
        frontier = {f[:i] + f[i + 1 :] for f in frontier for i in range(len(f))}
        out |= frontier
    return out


@lru_cache(maxsize=1)
def _delete_index() -> dict[str, frozenset[str]]:
    """Every long target word under each of its ≤ 2-letter deletions: two-slip candidates by lookup (SymSpell)."""
    index: dict[str, set[str]] = {}
    for t in _targets():
        if len(t) >= 7:
            for d in _deletes(t):
                index.setdefault(d, set()).add(t)
    return {k: frozenset(v) for k, v in index.items()}


def _distance(a: str, b: str) -> int:
    """Damerau–Levenshtein distance (adjacent swaps count as one edit)."""
    d = [[i + j if i * j == 0 else 0 for j in range(len(b) + 1)] for i in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


@lru_cache(maxsize=1)
def real_words() -> frozenset[str]:
    """Common English words (``words.txt``, generated by ``scripts/make_wordlist.py`` from the Qwen2.5 tokenizer,
    Apache-2.0): a real word is never "repaired", even when the domain vocabulary lacks it ("point", "cost")."""
    lines = (_PKG / "nlu/words.txt").read_text(encoding="utf-8").splitlines()
    return frozenset(w for w in lines if w and not w.startswith("#"))


_SUFFIXES = ("ing", "ings", "ed", "er", "ers", "es", "s", "ly", "ment", "ments", "al", "ion", "ions")


@lru_cache(maxsize=1)
def _prefixes() -> frozenset[str]:
    return frozenset(w[:i] for w in vocabulary() for i in range(4, len(w) + 1))


def _inflection(word: str) -> bool:
    """A known stem with another ending ("developed" next to "developer"): a real word, not a typo."""
    return any(
        word.endswith(suf) and len(word) - len(suf) >= 4 and word[: -len(suf)] in _prefixes() for suf in _SUFFIXES
    )


@lru_cache(maxsize=4096)
def _correct(word: str) -> str:
    if len(word) < 3 or word in _known() or _inflection(word):
        return word
    swaps, others = _edits(word)
    if len(word) == 3:  # short words: only a missing letter ("lat" → last, "wek" → week, "dys" → days)
        others = {a + c + b for a, b in ((word[:i], word[i:]) for i in range(4)) for c in _LETTERS}
        swaps = set()
    targets = _targets()
    for tier in (swaps & targets, others & targets):
        if tier:
            return _pick(tier) or word
    if len(word) >= 9:  # long words may carry two slips
        index = _delete_index()
        near = set().union(*(index.get(d, frozenset()) for d in _deletes(word)))
        return _pick({t for t in near if _distance(word, t) <= 2}) or word
    return word


def normalise(question: str) -> str:
    text = question.lower()
    for pattern, written in SPOKEN:
        text = re.sub(pattern, written, text)
    text = _numbers(text)
    text = re.sub(r"[a-z]+", lambda m: _correct(m.group()), text)
    return question if text == question.lower() else text  # nothing repaired: the question exactly as asked
