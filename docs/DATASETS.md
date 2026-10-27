# Dataset registry

EdgeCar AI uses **real vehicle data wherever an open source exists**, and simulates only the channels for which
no public real-world time series exists. Every dataset below is *fetched* by the user or by a script — none is
redistributed in this repository (several licences forbid it or are unstated).

Legend: ✅ in use · 🟡 evaluating · ⛔ access-gated · ❌ rejected

## Real vehicle data

### ✅ VED — Vehicle Energy Dataset (University of Michigan)
- **What:** OBD-II logs from 383 personal vehicles in Ann Arbor, MI, Nov 2017 – Nov 2018 (~374,000 miles).
  264 ICE, 92 HEV, 24 PHEV, 3 EV (2013 Nissan Leaf, 24 kWh).
- **Signals:** GPS, vehicle speed, MAF, engine RPM, absolute load, outside air temperature, fuel rate,
  A/C power, heater power, HV battery current / SoC / voltage, fuel trims. Weekly CSVs, ~1 s resolution.
- **Source:** <https://github.com/gsoh/VED> · paper: arXiv:1905.02081
- **Licence:** Apache-2.0
- **Profiled (2026-10-27):** EV `455` = 293 trips over 191 days spanning the full year (incl. a Michigan winter);
  EV `10` = 201 trips / 144 days; PHEVs `457`, `550`, `569` carry a populated *Fuel Rate* column.
- **Used for:** year-long EV energy/range history, seasonal range loss, city-vs-highway comparison, PHEV fuel efficiency.

### ✅ TUM — Battery and Heating Data in Real Driving Cycles (BMW i3, 60 Ah)
- **What:** 72 real trips (70 trip CSVs) recorded by TU Munich's Institute of Automotive Technology.
  Category **A** = summer 2019 (partial channels), category **B** = winter 2019/20 (all channels). 10 Hz.
- **Signals:** velocity, elevation, throttle, motor torque, longitudinal acceleration, regen-braking signal,
  battery voltage / current / temperature / max temperature, SoC (true + displayed), heating power (CAN + LIN),
  requested heating power, A/C power, ambient temperature, coolant and heat-exchanger temperatures, cabin
  temperature, ~20 vent/footwell/defrost temperatures (winter set).
- **Source:** <https://ieee-dataport.org/open-access/battery-and-heating-data-real-driving-cycles>
  (DOI 10.21227/6jr9-5235) — free IEEE account required; download `Measurement Data.zip` (36 MB) manually.
- **Licence:** "Open Access", terms unstated → not redistributed.
- **Used for:** battery thermal behaviour, heating load, summer-vs-winter consumption.
- **Profiled (2026-10-27, trips > 5 km, distance-weighted, from `Overview.xlsx`):** summer (A, 30 trips,
  mean ambient 22.3 °C) **0.553 % SoC/km**; winter (B, 37 trips, mean ambient 3.9 °C) **0.749 % SoC/km** —
  ≈ 35 % more energy per km, ≈ 26 % less range. Caveat: route mix differs (summer mostly urban Munich East;
  winter adds Munich North, a fixed test route and highway), so the attribution engine must separate
  temperature/heating effects from speed-profile effects.

### ✅ Nissan Leaf CAN database files (DBC)
- **What:** Reverse-engineered DBC files for the Leaf's EV-CAN and CAR-CAN buses (ZE0 / AZE0 / ZE1).
- **Source:** <https://github.com/dalathegreat/leaf_can_bus_messages>
- **Licence:** GPL-3.0 → fetched at setup time, never vendored.
- **Used for:** re-encoding replayed real signals into genuine CAN frames so the ingestion layer decodes real
  bus traffic, not CSV rows.

### ⛔ ADAS-TO longitudinal (OpenLKA / Univ. of Wisconsin)
- **What:** Real openpilot ADAS takeover windows (−3 s … +1 s at 10 Hz) with gap, relative speed, ego speed and
  acceleration; case–control pairs.
- **Source:** <https://huggingface.co/datasets/HenryYHW/ADAS-TO_longitudinal> · paper arXiv:2603.06986
- **Licence:** MIT · **Access:** Hugging Face gate with *manual* approval (requested by the author of this repo).

### ⛔ BATON-Sample (OpenLKA)
- **What:** 43 real routes from comma devices: `vehicle_dynamics.csv` (speed, steering, pedals, driving-assist
  status), `radar.csv` (lead distance / relative speed), `planning.csv`, `gps.csv`, takeover / activation labels.
- **Source:** <https://huggingface.co/datasets/HenryYHW/BATON-Sample> · paper arXiv:2604.07263
- **Licence:** CC BY-NC 4.0 (academic, non-commercial) · **Access:** Hugging Face gate with *automatic* approval.
- **Planned use:** real ADAS engage/disengage sequences and lead-vehicle context; only CSVs are fetched.

### ✅ NHTSA Standing General Order — Level 2 ADAS incident reports
- **What:** Public US incident reports for crashes involving Level-2 ADAS (system engaged, roadway, weather,
  pre-crash speed and movement, narrative). 116 columns; updated monthly.
- **Source:** <https://static.nhtsa.gov/odi/ffdd/sgo-2021-01/SGO-2021-01_Incident_Reports_ADAS.csv>
- **Licence:** US government work (public domain).
- **Profiled (file dated 15 Oct 2026):** 2,574 incidents; 96 % "verified engaged"; known roadway types are 62 %
  highway/freeway; median pre-crash speed 39 mph (IQR 22–61); "lane/road departure" is the second most common
  pre-crash movement after "proceeding straight".
- **Used for:** priors for the ADAS event generator (road type, speed, movement) and incident wording.

### ❌ California DMV — autonomous-vehicle disengagement reports
- Covers SAE Level 3+ test fleets only (not in-car Level-2 ADAS); report pages returned "Page not found"
  on 2026-10-27. Not used.

## Simulated data

### ✅ TD2D — Takeover during Distracted L2 Driving (Kangwon National University)
- **What:** 50 drivers × 10 secondary-task conditions = 500 takeover cases in a modified **CARLA 0.9.11**
  simulator, with takeover outcome, reaction time, NASA-TLX workload, physiological and eye-tracking data.
- **Source:** <https://zenodo.org/records/14185964> · paper: *Scientific Data* (2025) s41597-025-04781-8
- **Licence:** CC BY 4.0. The archive is a single 16.7 GB zip (98 % eye-tracking video); only the 500
  `takeoverScenarioInformations.csv` files (< 200 kB in total) and `driverInformation.csv` are fetched, each with
  one exact HTTP range request.
- **Profiled:** critical events = right pedestrian 174, front vehicle 171, left pedestrian 155. Reaction time
  median **948 ms** (IQR 796–1,208, range 515–2,000). Failure rate **36 %** overall: 14 % at baseline (median
  821 ms) vs. ~60 % under visual-manual distraction (texting 1,303 ms, gaming 1,190 ms, e-book 1,169 ms).
  Front-vehicle events are hardest (median 1,217 ms, 44 % failure).
- **Used for:** calibrating takeover-request timing and outcome distributions in the ADAS event generator.
  There are no vehicle-dynamics time series in the archive.

### Synthetic channels (no open real time-series exists)
| Channel | Why synthetic | Generated to |
|---|---|---|
| Diagnostic trouble codes (UDS / OBD-II) | No public timestamped DTC fleet data found | SAE J2012 / ISO 14229 code formats, real code definitions |
| SOME/IP service events | Only one research PCAP set exists, itself generated | AUTOSAR SOME/IP header layout |
| Android Automotive VHAL properties | No public recordings | AOSP `VehicleProperty` IDs and area/permission model |
| QNX `slog2` system logs | No public recordings | `slog2info` line format |
| Camera | Raw video is out of scope on 2 GB RAM | Perception *metadata* only (objects, lane status) |

Every synthetic scenario is injected with **ground truth**, so answer accuracy is measurable.

## Rejected
- **OpenLKA full dataset** — Dropbox links return an error page (checked 2026-10-27).
- **ADAS-TO full / BATON full** — manual gate + 40 GB+ including video.
- **comma2k19** — ~100 GB, highway-only, no ADAS state decoding.
- **IEEE "EV CAR CAN BUS DATA"** — raw frames without DBC (proprietary).
- Kaggle "EV telematics" sets — explicitly synthetic / unclear provenance.
