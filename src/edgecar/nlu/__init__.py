"""Natural-language understanding: turn a question into a typed ``Query``.

Two paths, tried in order:

1. ``fastpath`` — deterministic rules for the common phrasings (no model, ~1 ms).
2. ``slm`` — a small language model constrained by a grammar to emit one compact command line.
"""
