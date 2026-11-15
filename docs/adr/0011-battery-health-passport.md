# ADR-0011 — Battery Health Passport: the car measures its battery and signs what it found

- **Status:** Accepted (prototype)
- **Date:** 2026-11-15

## Context

Battery health decides what a used electric car is worth, and buyers cannot see it. India's used-EV market has no
accepted battery-health standard (a 2026 Autocar India × Spinny study of 11,000+ transactions found EV resale values
unpredictable mostly for that reason); the in-car battery display is widely distrusted; the draft Battery Pack
Aadhaar (MoRTH, Dec 2025) gives packs an identity and the EU battery passport (Feb 2027) requires state of health —
but neither says how a used car's owner proves it. Existing services upload the car's data to a cloud (Recurrent) or
need a test device (Aviloo). Meanwhile, car data sold without consent is a live problem (FTC order against GM,
January 2026).

EdgeCar already has what such a report needs: months of the battery management system's own signals on the vehicle,
analytics in which code — never the model — computes every number (ADR-0001), and a key store in the architecture
(docs/ARCHITECTURE.md §8).

## Decision

1. **Measure capacity from the car's own drives** (`edgecar/analytics/battery.py`): on every drive that uses ≥ 15 %
   of charge, capacity = ∫ I dt ÷ ΔSoC. The figure is the median over drives with a bootstrap 95 % interval; the
   trend is a Theil–Sen slope with a bootstrap interval; state of health = capacity ÷ the pack's rating. Wear
   exposure is reported as recorded (hours parked above 90 % after a charge, hours driven above 35 °C, time below
   10 %), not turned into a prediction.
2. **State a figure only when the evidence is tight**: at least 8 drives and an interval no wider than ±3 points;
   otherwise the answer and the report say "inconclusive" and why.
3. **Sign conclusions, not data** (`edgecar/passport.py`): the report carries capacity, health, trend, wear, the
   period (days only), a SHA-256 of the measured inputs and the software version — no trip, time of day or place.
   It is signed with an Ed25519 key over its exact bytes. The QR code holds the whole signed report after the `#` of
   a link to a static checker page (`docs/verify/`), so a buyer's phone verifies it with no upload and no account.
4. **Ask it like anything else**: "Is my battery healthy?" goes to the rules path (`battery_health`), the answer
   shows the per-drive capacities as evidence, and a "Battery passport for a buyer" button draws the signed QR in
   the app. The intent is deliberately not added to the model's grammar: the measured models were never trained on
   it, and the rules cover the phrasing.

## Results

- **BMW i3 (TU Munich, real, 23 drives, Jun 2019 – Feb 2020):** 61.7 Ah of a rated 60 Ah — state of health 103 %,
  interval 102.8–103.1 %; trend +0.8 % a year, interval −0.2 to +3.3 (no measurable change over 7 months).
- **Nissan Leaf 455 (VED, real):** 10 qualifying drives spread from 35 to 45 Ah → inconclusive, by rule 2.
- **Validation against measured capacity — not yet done.** EVBattery (Tsinghua, CC BY 4.0) has engineer-measured
  capacities, but its current and timestamps were perturbed and scaled to anonymise it, so charge cannot be
  integrated in real units and, within a vehicle, the estimate does not follow the labels
  ([`eval/results/battery-validation.md`](../../eval/results/battery-validation.md)). The next check is TU Munich's
  *EV Usage and State of Health Data* (mediaTUM 1845276), which keeps real units.
- Browser check (Edge): a genuine report verifies; changing one figure breaks the signature.

## Consequences

- **What the signature proves:** the figures came from the software of the vehicle holding the key, from its own
  data, unchanged since. Not that the sensors were honest — that rests on secure boot and the key living in the
  HSM, as in a production vehicle; this prototype keeps the key in `data/keys/` (git-ignored). A buyer must compare
  the key ID with one they trust (in production, the manufacturer's certificate for the vehicle).
- **What the interval means:** the spread of this car's own measurements, not accuracy against a laboratory test;
  the report says so in a `limits` field until a validation exists.
- The same signing gives other owner-controlled reports later: a driving score for pay-how-you-drive insurance
  without GPS trails, a service report, an incident timeline.
- Two runtime dependencies: `cryptography` (Ed25519) and `segno` (QR, pure Python).
