# ADR-0003 — One embedded DuckDB file with tiered retention

- **Status:** Accepted
- **Date:** 2026-10-27

## Context

Questions are dominated by aggregations over time windows ("energy per km over the last two weeks", "city vs
highway"), plus event look-ups. The store must keep several weeks of history searchable, run inside the 2 GB RAM
budget on two ARM cores, and need no server process.

## Decision

- **DuckDB**, embedded, single file. Columnar storage and vectorised execution make window aggregations fast;
  `SET memory_limit` and `SET threads` cap its footprint (defaults: 256 MB, 2 threads).
- **Tiers:**

  | Table | Resolution | Retention (production default) |
  |---|---|---|
  | `frames` | 1 Hz canonical signals | weeks (configurable) |
  | `frames_1min` | per-minute mean/min/max | months |
  | `trips` | one row per trip (distance, energy split, speed mix, temperatures, braking) | vehicle lifetime |
  | `events` | every discrete event, any source, with provenance | vehicle lifetime |

- **Pre-computed trip summaries** answer most questions from hundreds of rows instead of millions of samples.
- Every row carries **provenance** (`real:ved`, `real:tum`, `sim:*`).

## Consequences

- Measured on the development machine: the full multi-vehicle history (540 k one-second frames, 814 trips)
  occupies 36 MB, and typical analytics queries complete in 2–13 ms.
- DuckDB's single-writer model fits one ingestion process per vehicle; readers open the file read-only.
- Full-text search over log text uses SQL pattern matching for now; DuckDB's FTS extension must be bundled at
  build time if needed, because the vehicle cannot download extensions.

## Alternatives considered

- **SQLite (+FTS5)** — ubiquitous and tiny, but row-oriented; window aggregations over weeks of 1 Hz data are
  markedly slower.
- **A time-series server (InfluxDB, TimescaleDB)** — a separate service and memory footprint the 2 GB budget
  does not allow.
