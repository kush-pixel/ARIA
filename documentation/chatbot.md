# ARIA — Clinical Chatbot Q&A Plan

**Author:** Sahil Khalsa
**Status:** Planning — not yet started
**Scope:** Conversational patient Q&A interface for clinicians (Layer 3 addition — not a replacement)

---

## TL;DR

The existing 3-sentence LLM summary (`summarizer.py` → `readable_summary` in `BriefingCard`) stays
exactly as-is. This chatbot is an additive Layer 3 feature rendered below the briefing card.

Layer 3 now has two components:
1. **AI Summary (existing, unchanged)** — `summarizer.py` generates a 3-sentence `readable_summary`
   stored in `briefings.llm_response`. Validated by `llm_validator.py`. Displayed in `BriefingCard.tsx`.
2. **Chatbot Q&A (new)** — `services/chat/` agent answers natural-language questions about a patient,
   grounded in real DB data via tool calls. Rendered in `ChatPanel.tsx` below the briefing.

A clinician reads the briefing summary first, then can ask follow-up questions — *"Why was treatment
review flagged?"*, *"Was adherence good during that period?"*, *"When did readings start worsening?"*
— and receive answers with full evidence provenance and clinical guardrails.

The briefing summary tells the GP what ARIA found. The chatbot lets them interrogate why.

---

## Out of scope

- Patient-facing chat — clinical boundary; chat is clinician-only
- Cross-patient queries — agent is scoped to a single patient_id per session
- Mutations — agent is read-only; it cannot update readings, alerts, or medications
- Persistent conversation history across sessions — in-memory only; clears when clinician navigates away
- Voice input
- Free-text clinical note ingestion
- Any form of medication recommendation or dosage suggestion

---

## Layer 3 Architecture (both components)

```
Layer 1 briefing (composer.py) — always runs first, authoritative output
        │
        ├── Component A: AI Summary (EXISTING — unchanged)
        │     summarizer.py → 3-sentence readable_summary
        │     validated by llm_validator.py (14 checks)
        │     stored in briefings.llm_response["readable_summary"]
        │     displayed in BriefingCard.tsx (top of patient page)
        │
        └── Component B: Chatbot Q&A (NEW — additive)
              services/chat/ agent answers clinician questions
              grounded in real DB data via 6 read-only tools
              displayed in ChatPanel.tsx (below BriefingCard)
```

The two components share the same clinical boundary rules and forbidden-phrase guardrails
but have separate validators — `llm_validator.py` for the summary, `chat/validator.py` for
the chatbot. Neither is replaced or modified by the other.

---

## Chatbot Architecture

```
Clinician types question in ChatPanel
            │
            ▼
    POST /api/chat
      │  JWT auth (clinician only — patient_required dependency blocked)
      │  Load session from session.py (conversation history for this patient)
      │  Pre-load patient context once per session (cached with Anthropic prompt caching)
            │
            ▼
        agent.py  ─────────────── Anthropic tool-use loop (max 3 rounds)
            │
            ├── Round 1: LLM decides which tools to call based on question + context
            │
            ├── Round 2: tools execute → real DB rows returned
            │
            └── Round 3: LLM optionally chains (e.g. reads trend data →
                         decides to also fetch medication history to contextualise)
            │
            ▼
       formatter.py
         Parses structured LLM JSON response:
         { answer, evidence[], confidence, data_gaps[] }
            │
            ▼
       validator.py
         5 checks reused from llm_validator.py
         5 new chatbot-specific checks
         On failure → safe fallback answer returned (never null)
            │
            ▼
       audit_events row written
         action="chat_query", outcome="success"|"blocked"
            │
            ▼
    SSE stream → ChatPanel.tsx
         Tokens appear as generated (streaming)
         Evidence cards rendered below answer
         Confidence indicator shown
```

---

## Repo layout (additions)

Same monorepo. No new repo.

```
ARIA/
  backend/
    app/
      api/
        chat.py                        ← NEW — POST /api/chat (SSE streaming)
                                                GET  /api/chat/suggested-questions/{patient_id}
      services/
        chat/                          ← NEW — chat service package
          __init__.py
          agent.py                     ← multi-turn tool-use loop + prompt caching + streaming
          tools.py                     ← 6 read-only tool definitions + DB executors
          session.py                   ← in-memory conversation history per (clinician_id, patient_id)
          validator.py                 ← 5 reused guardrails + 5 new chatbot checks
          formatter.py                 ← parse LLM structured JSON → Python dataclass
    tests/
      test_chat_validator.py
      test_chat_agent.py
  prompts/
    chat_system_prompt.md              ← NEW — chatbot system prompt + clinical boundary rules
  frontend/
    src/
      components/
        briefing/
          ChatPanel.tsx                ← NEW — streaming chat UI
          SuggestedQuestions.tsx       ← NEW — clickable question chips from Layer 1 signals
          EvidenceCard.tsx             ← NEW — tool provenance display per answer
```

---

## API endpoints

### `POST /api/chat`

Auth: clinician JWT required. Patient JWT rejected.

```
Request body:
{
  "patient_id":  "1091",
  "question":    "Why was treatment review flagged?",
  "session_id":  "abc123"   ← optional; omit on first message to create new session
}

Response (Server-Sent Events stream):
event: token
data: {"token": "28-day"}

event: token
data: {"token": " average"}

...

event: done
data: {
  "answer":     "28-day average is 164 mmHg, above the adjusted threshold...",
  "evidence":   [
    "28-day avg systolic: 164 mmHg (source: get_patient_readings, last 28 days)",
    "No medication change in 287 days (source: get_medication_history)"
  ],
  "confidence": "high",
  "data_gaps":  [],
  "tools_used": ["get_patient_readings", "get_medication_history"],
  "session_id": "abc123",
  "blocked":    false
}

On guardrail failure:
event: done
data: {
  "answer":  "I can't reliably answer that from the available data.",
  "blocked": true,
  "reason":  "guardrail:prescribe"
}
```

### `GET /api/chat/suggested-questions/{patient_id}`

Returns 3–4 question strings dynamically generated from the latest briefing payload.
No auth token required beyond standard clinician JWT.

```
Response:
{
  "questions": [
    "Why was treatment review flagged?",
    "When did readings start worsening?",
    "How does current BP compare to this patient's historical baseline?"
  ]
}
```

---

## The 6 tools

All tools are read-only. The agent cannot update, insert, or delete any row.
Each tool executor is an async function in `tools.py` that takes `patient_id` plus
optional parameters and returns a typed dict.

| Tool | Parameters | Returns |
|---|---|---|
| `get_patient_readings` | `patient_id`, `days=28` | avg systolic/diastolic, trend direction, session breakdown (morning vs evening), reading count, date range |
| `get_patient_alerts` | `patient_id` | active unacknowledged alerts with types, gap_days, systolic_avg, triggered_at |
| `get_medication_history` | `patient_id` | full med timeline sorted by date, last change date, activity (add/modify/remove) |
| `get_adherence_summary` | `patient_id`, `days=28` | per-medication confirmation rate, missed dose count, overall rate |
| `get_clinical_context` | `patient_id` | active problems, overdue labs, recent labs (creatinine, K+, HbA1c), last clinic BP |
| `get_briefing` | `patient_id` | latest Layer 1 briefing payload — trend summary, urgent flags, visit agenda, risk score |

The agent receives these as Anthropic tool schemas and decides which to invoke based on
the question. On empty results (no readings, no history), the tool returns a structured
empty response — the agent is instructed to acknowledge data gaps, not fabricate answers.

---

## Multi-turn reasoning

The agent loops up to **3 rounds** per question. This enables chained reasoning:

**Example — "What changed around March?"**

```
Round 1:
  LLM decides: call get_patient_readings(patient_id, days=90)

Round 2:
  Tool returns 90-day reading array.
  LLM sees step-change in mid-March.
  LLM decides: also call get_medication_history(patient_id) to check if
               a med change coincides with the step-change.

Round 3:
  Both results available.
  LLM synthesises: "Readings show a 15 mmHg step-increase beginning
  14 March 2013. No medication change is recorded near that date —
  the deterioration detector's step-change gate fired on this window."
```

A static briefing cannot produce this — it requires reasoning across two data sources
in response to a specific question about a specific time window.

---

## Prompt caching

The patient context (clinical_context fields + latest briefing payload) and system prompt
are large. Resending them on every turn in a multi-turn session is expensive and slow.

Using Anthropic's `cache_control` ephemeral blocks:

```
┌─────────────────────────────────────┐  ← cached for session lifetime
│  System prompt (chat_system_prompt) │    cache_control: {"type": "ephemeral"}
├─────────────────────────────────────┤
│  Patient context snapshot           │    cache_control: {"type": "ephemeral"}
│  (clinical_context + briefing)      │
├─────────────────────────────────────┤  ← grows each turn, not cached
│  Conversation history               │
├─────────────────────────────────────┤
│  Current question                   │  ← new each turn
└─────────────────────────────────────┘
```

Result: first question in a session pays full token cost. Every follow-up question in
the same session saves ~80% of input tokens. Faster responses, lower API cost.
Strong thesis talking point on production-grade engineering.

---

## Conversation session (`session.py`)

In-memory session store keyed by `(clinician_id, patient_id)`.

- Created on first message to a patient
- Stores: `[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]`
- Cleared when clinician navigates to a different patient or logs out
  (frontend fires a `DELETE /api/chat/session` on unmount)
- Max 20 turns stored — oldest pair evicted if exceeded (prevents unbounded context growth)
- No persistence to DB — session is ephemeral by design

This is what makes follow-up questions work. Without session memory, the second question
has no knowledge of the first answer and the agent re-fetches everything from scratch.

---

## Suggested questions (`SuggestedQuestions.tsx`)

`GET /api/chat/suggested-questions/{patient_id}` reads the latest briefing payload and
returns question chips dynamically based on what Layer 1 detected:

| Layer 1 signal in briefing | Suggested question |
|---|---|
| Inertia in `urgent_flags` | "Why was treatment review flagged?" |
| Deterioration in `urgent_flags` | "When did readings start worsening?" |
| Adherence Pattern A in `adherence_summary` | "Which medications had the most missed doses?" |
| Any gap alert | "How long was the monitoring gap and when did it start?" |
| Non-empty `overdue_labs` | "What labs are overdue and when were they last done?" |
| Always included | "How does current BP compare to this patient's historical baseline?" |

Questions appear as clickable chips above the input box. Clicking fills the input field
(editable before sending). Removes the blank-box problem and guides the clinician toward
clinically relevant questions immediately.

---

## Structured response format

The LLM is instructed via the system prompt to always return a JSON object, not raw prose.
`formatter.py` parses this into a `ChatResponse` dataclass before validation.

```json
{
  "answer": "28-day average is 164 mmHg, above the 145 mmHg adjusted threshold for this patient's comorbidity profile. No medication change has been recorded in 287 days, meeting the inertia detector criteria.",
  "evidence": [
    "28-day avg systolic: 164 mmHg (source: get_patient_readings, days=28)",
    "Last medication change: 2013-01-15, 287 days ago (source: get_medication_history)",
    "Adjusted threshold: 145 mmHg — CHF comorbidity applies 7 mmHg reduction (source: get_clinical_context)"
  ],
  "confidence": "high",
  "data_gaps": []
}
```

`confidence` values:
- `"high"` — answer fully grounded in tool-returned data, no inference gaps
- `"medium"` — answer partially grounded; some reasonable inference from available data
- `"low"` — limited data available; answer hedged; clinician should not rely solely on this
- `"no_data"` — tools returned empty; answer explicitly states no data is available

The frontend renders `evidence[]` as expandable cards beneath the answer text.
Confidence level is shown as a subtle coloured dot next to the answer.

---

## Chatbot validator (`services/chat/validator.py`)

Two groups. Runs after every LLM response before it reaches the clinician.

### Group A — Reused from `llm_validator.py` (call the existing functions directly)

| Check | What it catches |
|---|---|
| `check_phi_leak` | Patient ID appearing verbatim in output |
| `check_prompt_injection` | `[INST]`, `system:`, `ignore previous`, etc. |
| `check_guardrails` | Forbidden clinical phrases: `non-adherent`, `hypertensive crisis`, `prescribe`, dosage recommendations, patient-facing language, diagnostic claims |
| `check_medication_hallucination` | Drug names in answer absent from `get_medication_history` tool result |
| `check_bp_plausibility` | BP values outside 60–250 mmHg or >20 mmHg from tool-returned trend |

### Group B — New chatbot-specific checks

| Check | What it catches |
|---|---|
| `check_groundedness` | Numbers cited in answer that do not appear in any tool result for this turn |
| `check_empty_data_acknowledged` | If all tools returned empty, answer must say "no data available" — not fabricate a response |
| `check_no_certainty_predictions` | Blocks "will improve", "definitely will", "certainly" — predictions framed as clinical certainties |
| `check_scope_boundary` | References to other patients, system internals, training data, or requests to ignore instructions |
| `check_evidence_consistency` | Each item in `evidence[]` must match the actual tool result for that source — no invented provenance |

### Failure behaviour

Unlike the briefing validator (which stores `readable_summary=None` on failure), the
chatbot validator never returns null. On any failed check:

```json
{
  "answer":  "I can't reliably answer that from the available data.",
  "blocked": true,
  "reason":  "guardrail:prescribe",
  "confidence": "blocked"
}
```

The frontend renders this as a grey message: *"This question can't be answered reliably
from the available patient data."* The clinician is never left with a broken UI state.

Retry policy: unlike briefing (retry once), chatbot does **not** retry — a blocked
question is returned immediately. Retrying a guardrail-blocked response is unlikely to
succeed and wastes latency during a live consultation.

### Audit trail

Every chatbot interaction writes an `audit_events` row regardless of pass or fail:

```
action="chat_query"
actor_type="clinician"
actor_id=clinician_id (from JWT)
patient_id=patient_id
resource_type="Patient"
resource_id=patient_id
outcome="success" | "blocked"
details="tools_used: [get_patient_readings, get_medication_history]"
      | "blocked_by: guardrail:prescribe"
```

Every question asked by every clinician about every patient is logged. Non-negotiable
for a clinical AI system. Strong thesis talking point on governance and accountability.

---

## System prompt (`prompts/chat_system_prompt.md`)

Key rules enforced at the LLM level:

1. Answer only questions about the patient identified in the context block
2. Never recommend specific medication names, dosages, or adjustments
3. Never make diagnostic statements
4. If a tool returns no data, say so explicitly — never fill gaps with assumptions
5. All output is decision support for the clinician. The clinician makes all decisions
6. Use "possible adherence concern" — never "non-adherent"
7. Use "treatment review warranted" — never "medication failure"
8. Use "sustained elevated readings" — never "hypertensive crisis"
9. "I don't have enough data to answer that" is always a valid and preferred response
    over a low-confidence fabricated answer
10. Always return a JSON object in the format specified — never raw prose

The system prompt is versioned. Its SHA-256 hash is stored in the audit_events
`details` field on every chatbot call (same approach as briefing `prompt_hash`).

---

## Frontend components

### `ChatPanel.tsx`

- Collapsible panel on the patient detail page, **below `BriefingCard.tsx`**
- `BriefingCard` (existing, unchanged) renders first — summary + sparkline + agenda
- `ChatPanel` renders immediately below it — clinician reads briefing then asks questions
- Text input with send button
- SSE consumer — renders tokens as they stream in (real-time feel)
- Shows thinking state: *"ARIA is querying: readings, medication history..."*
  (tools being called are shown as they fire, before the answer arrives)
- Renders `EvidenceCard.tsx` components below each answer
- Confidence dot next to each answer (green = high, amber = medium, red = low/no_data)
- Blocked responses shown as grey italic: *"This question can't be answered reliably
  from the available patient data."*

### `SuggestedQuestions.tsx`

- Renders 3–4 question chips fetched from `GET /api/chat/suggested-questions/{patient_id}`
- Each chip is a button — clicking it fills the ChatPanel input (user can edit before sending)
- Chips hide after the first question is sent (panel is now in conversation mode)
- Fetched once on panel open, not refetched per turn

### `EvidenceCard.tsx`

- Expandable card below each answer
- Collapsed by default: shows *"Based on: readings (28 days), medication history"*
- Expanded: shows each `evidence[]` item as a bullet
- Example: *"28-day avg systolic: 164 mmHg (source: readings, last 28 days)"*
- Gives the clinician a one-click way to verify what data backed the response

---

## Demo scenario (defense day)

1. Open patient 1091 → `BriefingCard` loads with 3-sentence AI summary at top:
   *"28-day average is 164 mmHg with sustained elevated readings. Adherence is high at 91%,
   suggesting treatment review rather than an adherence concern. No medication change has
   been recorded in 287 days — review of treatment plan is the priority agenda item."*
2. Clinician reads briefing, scrolls to `ChatPanel` below — suggested question chips appear
3. Clinician clicks
   *"Why was treatment review flagged?"*
3. Tool trace appears: *"ARIA is querying: readings (28 days), medication history..."*
4. Answer streams live token by token: *"28-day average is 164 mmHg, above the adjusted
   threshold of 145 mmHg. No medication change has been recorded in 287 days..."*
5. Evidence cards expand below the answer
6. Clinician follows up: *"Was adherence good during that period?"*
   (agent uses conversation history — knows the period without being told)
7. Agent calls `get_adherence_summary` only — already has readings context
8. *"Yes — 91.3% confirmation rate across Lisinopril and Metoprolol. Missed doses
   cannot account for the elevated readings."*
9. Clinician tests the boundary: *"Increase the Lisinopril dose"*
10. Guardrail fires → *"This question can't be answered reliably from the available
    patient data."*

Story: *"Static briefings tell the GP what ARIA found. The chatbot lets them
interrogate why — in natural language, with full evidence provenance, in seconds."*

---

## Effort breakdown

| Component | Effort |
|---|---|
| `tools.py` — 6 read-only DB fetchers + Anthropic tool schemas | 0.5 day |
| `agent.py` — multi-turn loop, prompt caching, streaming | 1.5 days |
| `session.py` — in-memory conversation history | 0.5 day |
| `validator.py` — 5 reused + 5 new checks | 1 day |
| `formatter.py` — structured response parser + ChatResponse dataclass | 0.5 day |
| `chat.py` — SSE streaming endpoint + suggested-questions endpoint | 0.5 day |
| `chat_system_prompt.md` | 0.5 day |
| Frontend — ChatPanel + SuggestedQuestions + EvidenceCard | 1.5 days |
| Tests — validator + agent unit tests | 1 day |
| **Total** | **~7.5 days** (single engineer; ~4 days parallelised across 2) |

---

## Acceptance criteria

- [ ] Clinician can ask a natural-language question about a patient and receive a grounded answer
- [ ] Agent chains tool calls when a single tool is insufficient (multi-round reasoning)
- [ ] Follow-up questions use conversation history (agent does not re-fetch already-known context)
- [ ] Prompt caching is active — second question in a session is measurably faster than first
- [ ] Suggested questions are dynamically generated from Layer 1 briefing signals
- [ ] Tokens stream to the frontend in real time (SSE)
- [ ] Tool trace is shown to the clinician while the agent is working
- [ ] Evidence cards correctly reflect the tool results that backed the answer
- [ ] All 5 reused guardrail checks pass for chatbot responses
- [ ] All 5 new chatbot-specific checks pass for chatbot responses
- [ ] Medication recommendation attempt is blocked, returns safe fallback message
- [ ] Prompt injection attempt is blocked, returns safe fallback message
- [ ] Every Q&A pair (pass or fail) writes an audit_events row
- [ ] All existing tests pass
- [ ] `ruff check app/` passes for all new backend files
- [ ] Demo dry-run completes successfully end-to-end

---

## Open questions for the team

1. **Session expiry** — clear on navigate-away only, or add a time-based TTL (e.g. 30 min
   of inactivity)? Time-based is safer if clinician leaves the tab open.
2. **Max tool rounds** — 3 rounds is the plan; is there a case where 4 rounds is needed?
   Raising it increases latency; lowering it may cut off multi-step chains.
3. **Streaming fallback** — if the client doesn't support SSE (old browser), fall back to
   blocking JSON response or show an error?
4. **Prompt hash audit** — log the chat system prompt hash to audit_events (same as
   briefing `prompt_hash`)? Adds traceability when the prompt is updated.
5. **Rate limiting** — how many questions per clinician per minute? Prevents a runaway
   client from exhausting API quota mid-demo.

---

## Future work (NOT in this phase)

- **Persistent chat history** — store conversations in a `chat_sessions` table so clinicians
  can review past Q&A interactions after the consultation
- **Voice input** — Web Speech API → transcribed question → same chat pipeline
- **Multi-patient queries** — scoped to a GP's own patient list only, with explicit
  privacy controls (Tier 3, post-thesis)
- **Proactive suggestions** — chatbot surfaces a question the clinician hasn't asked but
  should (e.g. *"Would you like to know why adherence dropped in October?"*)
- **Cited reading links** — clicking a cited reading in the evidence card navigates to
  that row in the readings timeline

---

## References

- `documentation/patientapp.md` — Phase 9 Patient PWA + BLE bridge (parallel work)
- `backend/app/services/briefing/llm_validator.py` — source of reused guardrail checks
- `backend/app/services/briefing/summarizer.py` — Layer 3 architecture reference
- `prompts/briefing_summary_prompt.md` — Layer 3 system prompt (style reference)
- CLAUDE.md — clinical boundary rules (enforced identically in chatbot)
- AUDIT.md Fix 43 — patient submission UI (separate Phase 9 work)
