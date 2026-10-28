# ADR-0006 — Every source reaches the store through its real wire protocol

- **Status:** Accepted
- **Date:** 2026-10-27

## Context

The brief asks how the system collects data from CAN / CAN FD, SOME/IP services, the Android Vehicle HAL, QNX
services and logs, ADAS and diagnostic logs. Labelling simulated events with protocol names would not demonstrate
any of that: the ingestion layer must decode real encodings, and its behaviour must be testable.

## Decision

1. **Simulators and dataset replays emit wire traffic only.** Events are created exclusively by ingestion adapters
   that decode that traffic (`edgecar.ingest.adapters`); each adapter owns its event vocabulary (titles, severities).
2. **Protocols implemented** (`edgecar.protocols`):

   | Protocol | Implementation | Carries in the demo |
   |---|---|---|
   | CAN | Nissan Leaf community DBC (real), candump logs | Leaf speed, battery current/voltage/SoC, outside temperature, heater/A-C power |
   | CAN FD | EdgeCar reference DBC (64-byte frames; BMW's DBC is proprietary), candump `##` lines | i3 battery, thermal-warning level, powertrain |
   | Android VHAL | `VehiclePropValue` records with AOSP property IDs, units and enums (verified against AOSP source) | i3 speed (m/s), outside temperature; cluster warning (vendor property) |
   | SOME/IP | 16-byte AUTOSAR header, notifications, Wireshark-readable pcap | ADAS state changes; thermal-management power limit |
   | UDS over ISO-TP | `ReadDTCInformation` 0x19/0x02 responses, SAE J2012 DTC encoding, single/multi-frame ISO-TP | P0534, P0C32, P0A7E, P0A7F (+ distractors U0155, U0121, P0562, P0C73) |
   | QNX slog2 | `slog2info` text records with slog2 severity levels | thermal manager and BMS logs |

3. **Read-only by construction.** CAN interfaces are opened listen-only; SOME/IP and VHAL are subscribe-only. UDS is
   request/response, so somebody must ask: EdgeCar decodes the request/response pairs of the gateway's own
   `ReadDTCInformation` polls, passively. A platform without such polling gets a read-only poll list (0x19, 0x22) in
   the *gateway's* configuration; the AI zone never transmits.
4. **Defensive decoding.** Every canonical signal has a physical plausibility range; out-of-range samples are dropped
   at ingestion. Encoders reserve the all-ones raw value for "not available" (except enumerations and fields under
   4 bits, where every value is meaningful) and saturate out-of-range readings at the field limits.

## Results

- Leaf: 860,105 CAN frames for one car-year; decoded values within ½ LSB of the source on every signal; trips
  segmented from bus silence.
- i3: 215,570 CAN FD frames + 215,570 VHAL property events; round trip within ½ LSB, no values lost or invented.
- Overheat scenario: 9 of 9 ground-truth events recovered from QNX slog2, UDS/ISO-TP (incl. a multi-frame
  response), SOME/IP, VHAL and CAN FD; ADAS: 6,065 events decoded from SOME/IP captures, count-checked.
- Bugs this caught: a 2-bit enumeration whose top value (OverTemp) collided with the "not available" convention,
  and an encoder that crashed on out-of-range values — both fixed and covered by tests.

## Consequences

- The captures under `data/interim/wire/` can be inspected with standard tools (candump-format viewers, Wireshark).
- Camera feeds remain design-only: on a 2 GB budget the system ingests perception *metadata*, not video.
