# ADR-0007 — Root cause from a fault-propagation map and causal order, measured on random scenarios

- **Status:** Accepted
- **Date:** 2026-10-28

## Context

"What happened before the battery overheating warning?" was answered with the earliest warning-level fault (DTC or
system error) in the 15 minutes before the warning, and checked on one scenario we had injected ourselves — circular.
A reviewer asked for a blind test with distractors.

## Decision

1. **Test first.** `bench.rootcause` draws random scenarios on real BMW i3 trips: three fault kinds that fit a
   refrigerant-cooled pack (refrigerant charge loss, a stuck battery refrigerant valve, cell degradation), random
   onset and severity, and 2–6 distractors at random times — unrelated faults (U0155, U0121, P0562, the drive
   electronics' P0C73, infotainment logs, ADAS events) and same-subsystem decoys (transient thermal-manager and BMS
   glitches). All traffic goes through the wire encoders and the protocol adapters. Seeds below 1000 are for
   development; the reported run uses seeds 1000–1199.
2. **A fault-propagation map** (`edgecar/diagnosis.py`): each event belongs to a subsystem (by DTC or QNX process), and
   the vehicle's cooling architecture (`Vehicle.battery_cooling`) decides which subsystems can overheat its battery.
   Only those are candidates; unrelated faults are also left out of the timeline (and counted).
3. **Causal order:** the cause is the most recent candidate before the symptom began (the BMS rise-rate flag),
   searched back to the start of the trip — a cooling fault can precede the warning by far more than 15 minutes.

## Results (200 scenarios, seeds 1000–1199)

| Rule | Root cause correct |
|---|---|
| Earliest fault in the 15-minute window (before) | 34/200 |
| Fault-propagation map only | 161/200 |
| **Map + causal order** | **197/200** |

Events recovered from the wire: 2,409/2,409. In 121 scenarios the fault started more than 15 minutes before the
warning; 131 contained a same-subsystem decoy. The 3 misses are decoys logged between a cell fault and the start of the
temperature rise.

## Consequences

- A new vehicle is configured — its DTC catalogue, process names and cooling architecture — not retrained.
- The map is binary; a production version would weight links by FMEA likelihood and learn from confirmed repairs.
