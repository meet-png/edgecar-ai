# Labelling guidelines

A question's label is the command a correct system would run, in the full command language
(`explain_change metric=range window=last_14d`). When a question is genuinely ambiguous between two readings, both
are accepted. These conventions apply to every set in `eval/` and to the fine-tuning data.

## Intent

| The question… | Intent |
|---|---|
| asks why a metric changed (why, what's causing, how come, reason) | `explain_change` — only for range and the four consumption/efficiency metrics |
| asks why an event happened, or what happened before / led up to / triggered it | `events_before` |
| asks when or on which day something was highest, lowest, fell or rose most | `extreme` |
| compares city vs highway, cold vs warm, or now vs before | `compare` |
| asks to show, list or count events, or whether any happened | `list_events` |
| asks whether anything is unusual, wrong, abnormal or worrying | `anomalies` |
| asks how something changed over time, for a trend, graph or plot | `trend` |
| asks for one number over a period: how much, how far, how many, average, total | `summary` |
| asks about the car right now (battery level, range left) with no period | `status` |
| asks the car to act, tries to override the instructions, is not about this car's recorded data, asks about something the car does not record, or is too vague to answer | `unsupported` |

## Conventions

- **Metric words.** "efficiency" alone → `energy_efficiency`; "consumption" alone → `energy_consumption`;
  "mileage", "fuel economy", "kmpl", "mpg" and the Indian-English "average" ("what average is the car giving") →
  `fuel_efficiency`; "range", "how far on a charge" → `range`.
- **Best and worst** follow the metric's polarity: worst efficiency, mileage or range → `min`; worst consumption →
  `max`; best is the opposite. "Largest drop in fuel efficiency" also accepts "largest rise in fuel consumption".
- **Counting.** "How many hard braking *events*" → `list_events event=hard_brake`; "how many hard brakes" →
  `summary metric=hard_brakes`.
- **Compare** takes one of the four consumption/efficiency metrics; with no metric named it is `energy_consumption`
  (`fuel_consumption` when fuel is mentioned).
- **Time windows** come from the question's own words: today, yesterday, this/last week or month, the last N days,
  weeks or months (a month is 30 days), all/ever. With none, the intent's default applies: `explain_change` 14 days,
  `list_events` 7, `anomalies` 30, `trend` 30, `summary` 7; `extreme`, `compare` and `events_before` use all data.
- **Look-back.** "The N minutes before …" → `lookback=Nm`.
- **Unsupported** is the right answer, not a failure, for: control requests ("turn off lane assist"), instruction
  overrides, general knowledge, navigation, media, calls, data the car does not record (tyre pressure, oil level,
  camera footage, who was driving), and questions that need missing context ("why?", "what about yesterday?").
