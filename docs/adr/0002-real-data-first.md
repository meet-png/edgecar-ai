# ADR-0002 — Real vehicle data first; simulate only what has no open source

- **Status:** Accepted
- **Date:** 2026-10-27

## Context

The brief asks how driving scenarios and error sequences will be recreated (CARLA, simulation, or recorded
data) and how data from CAN/CAN-FD, SOME/IP, Android VHAL, QNX, ADAS, cameras and diagnostics is collected and
retained. A purely synthetic simulator is quick to build, but its trends are whatever the author programmed,
so it cannot show that the analytics work on real vehicle behaviour.

A survey of open datasets (see `docs/DATASETS.md`) found real, openly licensed sources for energy, battery,
thermal and driving-dynamics signals (VED, TUM BMW i3) and real CAN encodings (Nissan Leaf DBC), and gated
real sources for ADAS takeovers (ADAS-TO, BATON). No open real time series was found for DTCs, SOME/IP, VHAL
or QNX logs.

## Decision

1. **Replay real data as real protocol traffic.** Real trips are re-encoded into CAN frames using a real DBC,
   so the ingestion layer decodes genuine frames rather than reading CSV rows.
2. **Simulate the remaining channels to their specifications** (AUTOSAR SOME/IP, AOSP VHAL property IDs,
   QNX `slog2` format, SAE/ISO DTC formats), aligned in time with the real trips.
3. **Inject scenarios with ground truth.** Fault sequences (for example a battery-overheating precursor chain or
   ADAS disengagements) are inserted with a machine-readable record of what happened and when.
4. **Label provenance.** Every stored record carries its origin (`real:ved`, `real:tum`, `sim:<generator>`),
   and the UI and documentation state clearly which is which.

## Consequences

- Answers to questions like "why did my range drop?" rest on genuine seasonal and driving-style effects.
- The evaluation set can be built from real patterns *and* injected ground truth.
- Datasets cannot be redistributed; the repository ships fetch scripts and documentation instead.
- Real datasets come from different vehicles; the demo presents them as separate vehicles rather than
  stitching different cars into one fictitious timeline.
