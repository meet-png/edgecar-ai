# ADR-0004 — Simulated channels sit on top of real trips and never alter them

- **Status:** Accepted
- **Date:** 2026-10-27

## Context

The real datasets carry no ADAS, diagnostic-trouble-code, SOME/IP, VHAL or QNX channels, yet the brief asks how
"driving scenarios and error-condition sequences" are recreated and searched.

## Decision

1. **ADAS** (adaptive cruise + lane keeping) is simulated on each real trip's speed profile. It engages after
   sustained speed ≥ 60 km/h and disengages for:
   - causes **read from the real data**: hard deceleration (driver braking), speed below the ACC minimum;
   - causes **sampled per kilometre**: lane markings lost (×3 below 0 °C), sensor blockage (below −2 °C), and
     takeover requests whose reaction time and outcome are drawn from **500 measured takeovers in TD2D**
     (CARLA, 50 drivers).
2. **Fault scenarios** (e.g. a battery-coolant-pump failure leading to an over-temperature warning) are separate,
   clearly labelled trips derived from a real trip, with the failure physics applied on top and every event
   timestamped as ground truth. Diagnostic codes use verified SAE J2012 definitions (P0C73, P0A7E).
3. Generation is **seeded per trip**, so rebuilding the store reproduces identical events.
4. Every simulated row carries `provenance = sim:*`, and the UI shows it.

## Consequences

- Questions such as "Show all ADAS disengagements from yesterday" and "What happened before the battery
  overheating warning?" have exact, known answers, so retrieval accuracy can be measured.
- Real signals stay untouched: energy, range and temperature analytics always reflect genuine vehicle behaviour.
