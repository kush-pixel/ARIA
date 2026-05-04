# Student Final Project Contribution Report
## CS 595 — Spring 2026
## ARIA: Adaptive Real-time Intelligence Architecture
## Leap of Faith Technologies | Illinois Institute of Technology

**Team Members:** Krishna Patel, Kush Patel, Sahil Khalsa, Nesh Rochwani, Prakriti Sharma, Yash Sharma

---

## Krishna Patel – 9/10

Krishna was the primary owner of the frontend throughout the project. She built the initial dashboard shell, led the full clinical UI redesign including dashboard columns and BP chart improvements, added the guided product tour, and contributed to chatbot guardrails on the frontend side. She also worked on expanding the synthetic data generator to support a broader patient timeline, which required understanding the underlying clinical data model. Beyond the technical work, Krishna took the initiative to speak directly with doctors and healthcare professionals to validate the clinical direction of ARIA, which meaningfully shaped how the system was designed and presented. She brought a strong visual and product sense to ARIA that made the system feel polished and clinician-ready. An area for growth would be taking on more backend responsibilities to broaden her full-stack impact.

---

## Kush Patel – 10/10

Kush was the most prolific contributor on the team and the backbone of ARIA's core infrastructure. He built the database schema and connected it to Supabase, created the iEMR to FHIR adapter, implemented the FHIR ingestion engine, and built the synthetic reading and confirmation generators. He implemented the adaptive window logic, shadow mode, white-coat guard, Pattern B scoring, variability detector, adaptive threshold in the adherence analyzer, TITRATION_WINDOWS, and comorbidity adjustments. He also delivered risk tier implementation, drug interaction detection, dismissed alert suppression, demo preparation, and the final adapter and confirmation generator calibration. His output was consistently high quality.

---

## Sahil Khalsa (Self) – 10/10

I was one of the most technically active contributors on the team across the full stack and full timeline of the project. On the backend, I built the API route structure, adherence endpoint, Layer 1 detector wiring, briefing composer, LLM summarizer, and Layer 3 validation with guardrails. I also implemented alert feedback, shadow mode CLI, multi-patient pipeline testing, BLE webhook integration, escalation logic, calibration engine wiring, and 30-day outcome verification. I built the full chatbot feature end to end including backend, frontend, guardrails, UX polish, and session memory and delivered the Patient PWA including BP submission, medication confirmation, .ics calendar reminders, and 28 passing tests. Additional contributions included CORS security hardening, NICE NG136 suppression window correction, and frontend fixes across search, tier override, and drug interaction UI. An area I can improve is communicating progress more visibly to the team during parallel development sprints.

---

## Nesh Rochwani – 9/10

Nesh made meaningful and technically solid contributions throughout the project. He built the background worker processor and scheduler, which is a core piece of ARIA's processing pipeline. He replaced the hardcoded 140 mmHg threshold in the briefing composer with a patient-adaptive threshold consistent with the Layer 1 detectors, resolved a shadow mode crash on cold-start visits, and added the acknowledged alert history with a 24-hour undo window and full audit trail. He also resolved TypeScript type errors across the frontend and delivered a solid set of bug fixes and improvements. His work was reliable and impactful.

---

## Yash Sharma – 5/10

Yash completed the risk scorer as an early task and contributed a briefing UI improvement that filtered medication status and adherence display to antihypertensives only. He also helped with the demo shift and a few minor status updates. However, his overall involvement across the project was limited compared to other team members. There was a lack of consistent initiative and he did not take on any larger components independently. Greater engagement throughout the semester would have made a noticeable difference to the team.

---

## Prakriti Sharma – 5/10

Prakriti contributed the initial project commit and implemented the v5.0 pattern engine, which included the adaptive threshold, Pattern B suppression, the fifth inertia condition, and deterioration gates. These were meaningful technical contributions. However, her overall involvement beyond these areas was limited and she was largely absent from the broader development effort, integration work, and testing. More sustained participation across the project lifecycle would have significantly strengthened her overall contribution.

---

*All comments are strictly private and submitted in good faith for class assessment purposes only.*
