# Calibration tables

## `td2d_takeovers.csv`

The 500 takeover cases of **TD2D — Takeover during Distracted L2 Driving** (Kangwon National University; 50 drivers ×
10 secondary-task conditions in a modified CARLA 0.9.11 simulator). Source: <https://zenodo.org/records/14185964> ·
paper: *Scientific Data* (2025), s41597-025-04781-8. Licence: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

**Changes:** five columns copied unchanged from the 500 `takeoverScenarioInformations.csv` files —
`criticalEventType`, `reactionTime` (ms), `takeoverResult` — plus `driver` and `condition` taken from each file's
folder (`D01/0back/…`). Rows are in sorted file-path order, so the ADAS simulator's seeded sampling is identical on
every platform. Everything else in the 16.7 GB archive (eye tracking, video, physiology) is not used.

Regenerate from the original files (`python -m edgecar.prep.fetch td2d` reads only these CSVs out of the archive):

```python
from pathlib import Path
from edgecar.sim.adas import td2d_table

td2d_table(Path("data/raw/td2d")).to_csv("src/edgecar/sim/calibration/td2d_takeovers.csv", index=False)
```
