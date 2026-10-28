# Outside question set

Every other question set in `eval/` was written by the author with an AI pair-programmer, as were the
fine-tuning data. This set is written by people who have not seen the code, the rules, the prompts or any other
question set, so it is the honest test of how well EdgeCar understands people.

## Protocol

1. **Collect.** A short anonymous form (below), shared with classmates, family and the teammate. No names or
   e-mail addresses are collected.
2. **Freeze.** On the closing date the responses are exported to `raw.csv` (questions only, no timestamps) and
   committed **before any model or rule is run on them**.
3. **Label.** Each question gets its accepted command(s) under [`../LABELLING.md`](../LABELLING.md), written before
   any system output is seen. Questions the data cannot answer are labelled `unsupported` (declining is correct).
   A second person labels independently where possible; agreement is reported and disagreements are resolved by
   discussion.
4. **Run once.** Every model is scored on the frozen, labelled set — the few-shot Qwen2.5-1.5B and the fine-tuned
   models — and the numbers are published whatever they are. The set is never used to change rules, prompts or
   training data; any later fix is measured on a new set.

## Form (Google Forms)

**Title:** What would you ask your car? (2 minutes)

**Description:** I'm building an assistant that runs inside a car — fully offline — and answers questions about
the car's own recorded data. Help me test it with questions in your own words. There are no right or wrong
answers. It's anonymous, and the questions may be published in the project's open test set.

**Section 1 — Ask your car.** *Imagine your car kept a record of every trip: distance, speed, battery or fuel use,
range, outside and battery temperature, charging, driver-assistance events (lane keeping, adaptive cruise) and
warning or fault codes. What would you ask it? Write in English (Hinglish is welcome too).*

- Question 1 — short answer, required
- Question 2 — short answer, required
- Question 3 — short answer, required
- Question 4 — short answer
- Question 5 — short answer

**Section 2 — Try to trick it.**

- Ask it to *do* something (it should politely refuse — it can only read data). — short answer
- Ask it about something a car wouldn't record, or something unrelated. — short answer

**Section 3 — About you (optional).**

- What do you drive? — Petrol / Diesel / CNG / Electric / Hybrid / I don't drive
- Is English your first language? — Yes / No

**Consent (required checkbox):** I agree that my questions may be published anonymously in an open test set.
