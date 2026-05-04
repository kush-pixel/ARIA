# ARIA Demo Script
## CS 595 Final Presentation — Spring 2026
## Leap of Faith Technologies

**Total runtime: ~15 minutes**
**Speakers: Sahil (host/demo), Krishna (problem), Kush (solution), Nesh (technical), Prakriti (AI layers), Yash (close)**
**Demo patients: Patient A (Inertia), Patient C (Gap), Patient D (Adherence)**

---

## PART 1 — THE HOOK
### Speaker: Sahil | ~1.5 min

*(Walk to center. Pause. Let the room settle.)*

"There are 1.3 billion people in the world living with hypertension.

It is the single leading cause of preventable death on the planet.

And yet — the average GP has 8 minutes per consultation.

8 minutes to review everything that happened to a patient since the last time they walked through that door.
8 minutes to catch a trend, flag a concern, make a decision.

For a doctor managing 1800 patients — that is not enough time.
Not even close.

So what happens?

Things get missed. Patterns go unnoticed. Patients deteriorate quietly — between visits — when no one is watching.

We asked ourselves one question.

What if the doctor already knew — before the patient even sat down?"

*(Pause. Let it land.)*

"That is ARIA."

---

## PART 2 — THE PROBLEM
### Speaker: Krishna | ~1.5 min

*(Krishna steps forward.)*

"We didn't just build a system. We went and spoke to doctors — real GPs, real clinical environments.

And what they told us was consistent.

They don't lack data. They have too much of it — scattered across EHRs, spreadsheets, portal messages. There is no structure. No signal. Just noise.

And hypertension is insidious. It doesn't announce itself. A patient's blood pressure can trend upward for weeks before anyone notices. A patient can quietly stop taking their medication and no one knows until it shows up in a clinic reading six months later.

The gap between visits is where patients are most at risk.

That is the problem we are solving."

---

## PART 3 — VIDEO TRANSITION
### Speaker: Sahil | ~20 seconds

*(Sahil steps back to center.)*

"We want to show you what that problem looks like — and how we approached it.

Please watch."

*(Play AI video — covers the clinical need and ARIA's approach. ~2 minutes.)*

---

## PART 4 — THE SOLUTION REVEAL
### Speaker: Kush | ~1.5 min

*(Kush steps forward as video ends.)*

"What you just saw is the world ARIA is designed for.

So how does it work?

ARIA connects to a hospital's existing EHR system via FHIR R4 — the international standard for health data exchange. No rip and replace. No disruption to existing workflows.

Once connected, ARIA does three things continuously.

First — it ingests every patient's clinical history. Conditions, medications, lab results, clinic readings. All of it.

Second — it generates synthetic home blood pressure readings and medication confirmations, building a daily picture of each patient between visits.

Third — and this is where the intelligence lives — it runs a three-layer AI analysis on every patient, every night.

Layer 1: deterministic rules. No black boxes. Pure clinical logic — gap detection, therapeutic inertia, adherence correlation, deterioration detection.

Layer 2: a risk score. Every patient gets a number from 0 to 100. The sickest, most at-risk patients rise to the top.

Layer 3: a large language model that converts all of that into a concise, readable briefing — delivered to the GP at 7:30 AM on every appointment day.

The doctor walks in already knowing. ARIA does the watching."

---

## PART 5 — THE AI ENGINE
### Speaker: Prakriti | ~1 min

*(Prakriti steps forward.)*

"The intelligence behind ARIA is built to be trustworthy — not a black box.

Layer 1 runs first. Always. It applies deterministic clinical rules — no AI, no guessing. It asks: has this patient gone silent? Has their blood pressure been elevated for weeks with no medication change? Is there an adherence gap?

Only after Layer 1 is verified does Layer 2 compute the risk score — a weighted combination of systolic trend, days since last medication change, adherence rate, gap duration, and comorbidity severity.

And Layer 3 — the language model — only ever runs after both previous layers are confirmed. It synthesizes the output into plain clinical language. And it is guarded. Hard guardrails prevent it from ever recommending medications, diagnosing conditions, or using language that oversteps clinical boundaries.

ARIA is decision support. The clinician always decides."

---

## PART 6 — LIVE DEMO
### Speaker: Sahil (with Nesh supporting) | ~6.5 min

*(Sahil moves to the screen/laptop. Nesh stands by.)*

"Let me show you what a GP sees when they open ARIA."

---

### Scene 1 — The Dashboard (~1.5 min)

"This is the ARIA dashboard. Every patient, ranked automatically — high risk at the top, then medium, then low — sorted within each tier by their AI risk score.

The GP doesn't have to search for who needs attention. ARIA tells them.

You can see the BP trend column here — that is a live 28-day average pulled directly from the patient's home readings. Not a clinic reading from three months ago. Today's picture.

The alert inbox on the right — these are active, unacknowledged clinical flags. Gaps, inertia, adherence concerns. Prioritised and waiting."

---

### Scene 2 — Patient A: Therapeutic Inertia (~2 min)

"Let's open our first patient."

*(Click into Patient A — 1091.)*

"This patient has been monitored since 2008. 65 clinic blood pressure readings over five years.

Look at the trend. Sustained elevated systolic — averaging well above target — for months.

Now look at the medication history. The last medication change was years ago.

ARIA has detected therapeutic inertia. The blood pressure has been elevated, consistently, for the entire adaptive window — and there has been no treatment response.

The briefing the GP received at 7:30 this morning says — and I quote — 'treatment review warranted.'

Not a diagnosis. Not a prescription. A flag. A prompt. The clinician decides what happens next.

*(Click to the briefing panel.)*

Three sentences. Everything the doctor needs to walk into that room prepared."

*(Nesh — point out the risk score badge and tier override on screen if needed.)*

---

### Scene 3 — Patient C: Reading Gap (~1.5 min)

"Second patient."

*(Click into Patient C — David Patel.)*

"David has been monitoring consistently — 82 days of home readings. And then — silence. Twelve days with no data.

ARIA flagged this on day nine. Urgent gap alert. Delivered to the GP's inbox automatically.

Now — is David's device broken? Is he travelling? Is something wrong?

ARIA doesn't know. But it made sure the doctor does — before that silence became dangerous.

The GP can log an explanation right here — device issue, travel, illness — and the system suppresses the alert accordingly. Full audit trail maintained."

---

### Scene 4 — Patient D: Adherence Concern (~1.5 min)

"Third patient."

*(Click into Patient D — DEMO_ADH.)*

"This patient has an average systolic of around 152 over the last 28 days. And an adherence rate of 58%.

ARIA has correlated the two. Elevated blood pressure, low medication confirmation. Pattern A — possible adherence concern.

The briefing surfaces this clearly — per medication, per rate — so the GP can have an informed, sensitive conversation. Not an accusation. A conversation.

And notice — ARIA does not say 'non-adherent.' It says 'possible adherence concern.' The language is deliberate. Clinical. Respectful."

### Scene 5 — The Patient App (~2 min)
### Speaker: Sahil

"Now I want to show you the other side of ARIA.

Everything you have seen so far — the dashboard, the briefings, the alerts — that data has to come from somewhere. It comes from the patient.

*(Switch to patient app — port 3001.)*

This is the ARIA Patient App. A progressive web app — no download required. The patient opens it on their phone, logs in, and they are in.

*(Show the BP submission screen.)*

Every morning, the patient takes their blood pressure at home and submits it here. Two readings. Thirty seconds. Done.

That single submission — tonight — feeds into Layer 1 tomorrow morning. It is what ARIA uses to detect a gap, identify a trend, flag a concern.

The patient is not just a passive subject. They are part of the system.

*(Switch to the medication confirmation screen.)*

And here — medication confirmation. At the time their medication is due, the patient gets a prompt. One tap to confirm they took it.

That 58% adherence rate we just saw on Patient D? This is exactly where it comes from. Real confirmations. Real timestamps. Correlated automatically against blood pressure readings.

*(Optional: show the .ics reminder download.)*

And for patients who need a reminder — ARIA generates a calendar file they can add directly to their phone. Their medication schedule, in their calendar, automatically.

The loop is closed. Patient confirms. Data flows. ARIA analyses. Doctor is briefed.

Every night. Without anyone having to chase anyone."

---

## PART 7 — THE CLOSE
### Speaker: Yash | ~45 seconds, then Sahil | ~45 seconds

*(Yash steps forward.)*

"ARIA achieved a 94.3% accuracy rate in shadow mode validation — tested against real historical patient records, blind.

Zero false negatives. No high-risk patient was missed.

It runs every night. It scales to any patient list. And it fits into the GP's existing workflow — no new tools, no new logins. Just a briefing. Every morning."

*(Sahil steps back to center. Slow. Deliberate.)*

"I want to leave you with one thought.

Every year, hypertension kills more people than almost any other condition on earth. Not because we lack treatment. Not because we lack knowledge.

Because the gap between visits is invisible.

ARIA makes it visible.

A patient deteriorates quietly at home — ARIA sees it.
A treatment stops working — ARIA flags it.
A patient forgets their medication for two weeks — ARIA knows.

And at 7:30 every morning — the doctor walks in ready.

Not reactive. Ready.

That is what we built. That is ARIA.

We are Leap of Faith Technologies. Thank you."

*(Pause. Do not move. Let the room sit with it.)*

---

## TIMING GUIDE

| Section | Speaker | Time |
|---|---|---|
| The Hook | Sahil | 1:30 |
| The Problem | Krishna | 1:30 |
| Video transition + video | Sahil + video | 2:20 |
| The Solution Reveal | Kush | 1:30 |
| The AI Engine | Prakriti | 1:00 |
| Live Demo | Sahil + Nesh | 8:30 |
| The Close | Yash + Sahil | 1:30 |
| **Total** | | **~15:30** |

---

## TIPS FOR DELIVERY

- **Sahil:** Slow down on the opening. Every line is its own moment. Do not rush.
- **Krishna:** Speak from the doctor's perspective — you talked to them. That credibility shows.
- **Kush:** The three-layer reveal is your "and one more thing" moment. Build to Layer 3.
- **Prakriti:** Emphasise that Layer 3 never runs before Layer 1. That is the trust story.
- **Sahil (demo):** Click slowly. Let the audience read the screen before you speak. Silence during transitions is fine.
- **Nesh:** Stand close to the screen. You are the clinical expert pointing things out, not a narrator.
- **Yash:** The 94.3% number is your punchline. Say it clearly. Then pause.
- **Everyone:** No filler words. No "um." Practise the handoffs between speakers so they feel seamless.
