# ADR-0010 — The in-car app: evidence on screen, speech on the device

- **Status:** Accepted
- **Date:** 2026-10-29

## Context

Until now the assistant was a command line and a benchmark. A driver needs to ask by voice and get a short spoken
answer; a service engineer needs the evidence behind it — the fault timeline, the chart, the query that ran. Both have
to work with no network: the brief rules out cloud speech services as firmly as cloud language models.

## Decision

1. **One page served by the AI zone itself** (`edgecar serve`): plain HTML, CSS and JavaScript — no build step, no
   framework, no CDN, no web fonts. A Content-Security-Policy of `default-src 'self'` makes "loads nothing from the
   network" a property the browser enforces, not a promise. Every value is set with `textContent`.
2. **The answer card shows its evidence.** The analytics engine already returns a chart specification, a table and
   the time window; the page draws them as SVG (trend lines with the anomaly day marked, a horizontal waterfall for
   "why did it change", the root-cause timeline with the cause and the warning marked) and shows the route (rules or
   model), the time taken and the exact read-only command.
3. **Two views of one answer.** The driver view is glanceable — one sentence, spoken, evidence one tap away (driver
   distraction guidelines budget a 2-second glance). Service and fleet views show everything, after the same privacy
   redaction as the rest of the system (`privacy.py`).
4. **Speech on the device**, through sherpa-onnx (Apache-2.0) on the CPU:
   - recognition: Moonshine Tiny (MIT, 27 M parameters, 8-bit, 28 MB), built for short commands on small CPUs;
     push-to-talk, at most 9 s per question (the model's single-pass limit is ~9.5 s);
   - speech: a Piper VITS voice (8-bit, 19 MB) trained from scratch on public-domain LibriVox recordings; units are
     expanded for reading aloud ("kWh/100 km" → "kilowatt-hours per hundred kilometres").
   The browser records 16 kHz PCM with an AudioWorklet and posts a WAV to the local server; nothing else hears it.

## Measured (laptop, 2 threads; ARM to follow)

| | |
|---|---|
| Recognition, one spoken question (3–4 s) | 37–49 ms |
| Synthesis, a one-sentence answer (2.6–3.9 s of audio) | 0.4–0.6 s |
| Speech models on disk | 47 MB |

## Consequences and open points

- sherpa-onnx is pinned to 1.13.8: version 2.0 removed the built-in espeak-ng phonemiser that Piper voices need.
  espeak-ng is GPL-3.0 — acceptable for this demo, not for a locked-down vehicle image, which would need a GPL-free
  grapheme-to-phoneme step or a commercially licensed voice. Recognition has no such issue.
- The recogniser is English-only; Hinglish questions are a natural next step for the Indian market.
- On the 2 GB tier, speech fits beside the 0.5B command model (ADR-0009) but not comfortably beside the 1.5B one —
  one more reason the smaller fine-tuned model matters.
