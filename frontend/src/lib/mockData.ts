import type { Patient, ClinicalContext, Reading, Briefing, Alert, AdherenceData } from './types'

// Today's date for appointment simulation
const TODAY = '2026-04-18'
const TODAY_APPT = '2026-04-18T09:30:00Z'

// ─── Patients ─────────────────────────────────────────────────────────────────

export const MOCK_PATIENTS: Patient[] = [
  {
    patient_id: '1091',
    gender: 'M',
    age: 67,
    risk_tier: 'high',
    tier_override: 'CHF in problem list',
    risk_score: 74.5,
    monitoring_active: true,
    next_appointment: TODAY_APPT,
    enrolled_at: '2026-01-10T08:00:00Z',
    enrolled_by: 'dr.mehta',
  },
  {
    patient_id: '1092',
    gender: 'F',
    age: 58,
    risk_tier: 'medium',
    tier_override: null,
    risk_score: 41.2,
    monitoring_active: false,
    next_appointment: '2026-04-25T10:00:00Z',
    enrolled_at: '2026-01-15T09:00:00Z',
    enrolled_by: 'dr.mehta',
  },
  {
    patient_id: '1093',
    gender: 'M',
    age: 52,
    risk_tier: 'low',
    tier_override: null,
    risk_score: 18.7,
    monitoring_active: true,
    next_appointment: '2026-05-02T11:00:00Z',
    enrolled_at: '2026-02-01T10:00:00Z',
    enrolled_by: 'dr.mehta',
  },
]

// ─── Clinical Context ──────────────────────────────────────────────────────────

export const MOCK_CLINICAL_CONTEXTS: Record<string, ClinicalContext> = {
  '1091': {
    patient_id: '1091',
    active_problems: ['Congestive Heart Failure', 'Hypertension', 'Type 2 Diabetes Mellitus', 'Coronary Artery Disease'],
    problem_codes: ['I50.9', 'I10', 'E11.9', 'I25.10'],
    current_medications: ['Metoprolol Succinate 50mg', 'Lisinopril 10mg', 'Furosemide (Lasix) 40mg'],
    med_rxnorm_codes: ['866514', '314076', '313988'],
    last_med_change: '2025-10-14',
    allergies: ['Penicillin'],
    last_visit_date: '2026-03-21',
    last_clinic_systolic: 185,
    last_clinic_diastolic: 72,
    overdue_labs: ['HbA1c (due 2026-01-14)', 'Basic Metabolic Panel (due 2026-02-21)', 'BNP (due 2026-03-01)'],
    social_context: 'Lives alone. Retired. Daughter assists with medications on weekends.',
  },
  '1092': {
    patient_id: '1092',
    active_problems: ['Hypertension', 'Dyslipidaemia', 'Chronic Kidney Disease Stage 2'],
    problem_codes: ['I10', 'E78.5', 'N18.2'],
    current_medications: ['Amlodipine 5mg', 'Atorvastatin 20mg', 'Ibuprofen 400mg PRN'],
    med_rxnorm_codes: ['329526', '617312', '197805'],
    last_med_change: '2025-12-03',
    allergies: [],
    last_visit_date: '2026-02-10',
    last_clinic_systolic: 152,
    last_clinic_diastolic: 94,
    overdue_labs: ['Renal function panel (due 2026-03-10)', 'Fasting lipid panel (due 2026-04-01)'],
    social_context: 'NSAID use noted — potential interaction with antihypertensive regimen.',
  },
  '1093': {
    patient_id: '1093',
    active_problems: ['Hypertension'],
    problem_codes: ['I10'],
    current_medications: ['Ramipril 5mg'],
    med_rxnorm_codes: ['35208'],
    last_med_change: '2026-02-01',
    allergies: [],
    last_visit_date: '2026-02-01',
    last_clinic_systolic: 138,
    last_clinic_diastolic: 86,
    overdue_labs: [],
    social_context: null,
  },
}

// ─── Readings (Patient A — 28-day scenario) ────────────────────────────────────

function makeReading(
  id: string,
  patientId: string,
  date: string,
  session: 'morning' | 'evening',
  sys1: number,
  dia1: number,
  hr1: number,
  sys2?: number,
  dia2?: number,
  hr2?: number,
): Reading {
  const s2 = sys2 ?? sys1 - 3
  const d2 = dia2 ?? dia1 - 2
  const h2 = hr2 ?? hr1 + 1
  const time = session === 'morning' ? 'T07:15:00Z' : 'T19:30:00Z'
  return {
    reading_id: id,
    patient_id: patientId,
    systolic_1: sys1,
    diastolic_1: dia1,
    heart_rate_1: hr1,
    systolic_2: s2,
    diastolic_2: d2,
    heart_rate_2: h2,
    systolic_avg: parseFloat(((sys1 + s2) / 2).toFixed(1)),
    diastolic_avg: parseFloat(((dia1 + d2) / 2).toFixed(1)),
    heart_rate_avg: parseFloat(((hr1 + h2) / 2).toFixed(1)),
    effective_datetime: date + time,
    session,
    source: 'generated',
    submitted_by: 'generator',
  }
}

// Patient A 28-day readings
// Days 1-7: baseline ~163 morning, ~156 evening
// Days 8-14: drift to ~165 morning; day 10 evening absent
// Days 15-18: sustained elevation; days 16-17 device outage (no rows)
// Days 19-21: pre-appointment dip 153→148→151
// Days 22-28: return 160-166; days 25-26 weekend misses
export const MOCK_READINGS_1091: Reading[] = [
  // Day 1 (Mar 21)
  makeReading('r001', '1091', '2026-03-21', 'morning', 164, 100, 71),
  makeReading('r002', '1091', '2026-03-21', 'evening', 157, 96, 73),
  // Day 2
  makeReading('r003', '1091', '2026-03-22', 'morning', 161, 98, 70),
  makeReading('r004', '1091', '2026-03-22', 'evening', 153, 93, 72),
  // Day 3
  makeReading('r005', '1091', '2026-03-23', 'morning', 167, 102, 69),
  makeReading('r006', '1091', '2026-03-23', 'evening', 158, 96, 71),
  // Day 4
  makeReading('r007', '1091', '2026-03-24', 'morning', 162, 99, 72),
  makeReading('r008', '1091', '2026-03-24', 'evening', 155, 94, 73),
  // Day 5
  makeReading('r009', '1091', '2026-03-25', 'morning', 165, 101, 70),
  makeReading('r010', '1091', '2026-03-25', 'evening', 159, 97, 72),
  // Day 6
  makeReading('r011', '1091', '2026-03-26', 'morning', 163, 100, 71),
  makeReading('r012', '1091', '2026-03-26', 'evening', 154, 93, 73),
  // Day 7
  makeReading('r013', '1091', '2026-03-27', 'morning', 166, 101, 69),
  makeReading('r014', '1091', '2026-03-27', 'evening', 157, 95, 72),
  // Day 8 — inertia drift begins
  makeReading('r015', '1091', '2026-03-28', 'morning', 165, 101, 70),
  makeReading('r016', '1091', '2026-03-28', 'evening', 158, 96, 72),
  // Day 9
  makeReading('r017', '1091', '2026-03-29', 'morning', 167, 102, 69),
  makeReading('r018', '1091', '2026-03-29', 'evening', 161, 98, 71),
  // Day 10 — evening dose missed, no evening reading
  makeReading('r019', '1091', '2026-03-30', 'morning', 168, 103, 70),
  // Day 11
  makeReading('r020', '1091', '2026-03-31', 'morning', 164, 100, 71),
  makeReading('r021', '1091', '2026-03-31', 'evening', 158, 96, 72),
  // Day 12
  makeReading('r022', '1091', '2026-04-01', 'morning', 166, 102, 70),
  makeReading('r023', '1091', '2026-04-01', 'evening', 159, 97, 73),
  // Day 13
  makeReading('r024', '1091', '2026-04-02', 'morning', 165, 101, 70),
  makeReading('r025', '1091', '2026-04-02', 'evening', 157, 95, 72),
  // Day 14
  makeReading('r026', '1091', '2026-04-03', 'morning', 167, 102, 69),
  makeReading('r027', '1091', '2026-04-03', 'evening', 160, 97, 71),
  // Day 15 — sustained elevation
  makeReading('r028', '1091', '2026-04-04', 'morning', 165, 101, 70),
  makeReading('r029', '1091', '2026-04-04', 'evening', 158, 96, 72),
  // Days 16-17: device outage — no rows
  // Day 18
  makeReading('r030', '1091', '2026-04-07', 'morning', 164, 100, 71),
  makeReading('r031', '1091', '2026-04-07', 'evening', 157, 96, 73),
  // Day 19 — pre-appointment dip begins
  makeReading('r032', '1091', '2026-04-08', 'morning', 153, 93, 73),
  makeReading('r033', '1091', '2026-04-08', 'evening', 147, 89, 74),
  // Day 20
  makeReading('r034', '1091', '2026-04-09', 'morning', 149, 91, 74),
  makeReading('r035', '1091', '2026-04-09', 'evening', 144, 87, 75),
  // Day 21
  makeReading('r036', '1091', '2026-04-10', 'morning', 151, 92, 73),
  makeReading('r037', '1091', '2026-04-10', 'evening', 146, 89, 74),
  // Day 22 — return to baseline
  makeReading('r038', '1091', '2026-04-11', 'morning', 161, 98, 71),
  makeReading('r039', '1091', '2026-04-11', 'evening', 155, 94, 72),
  // Day 23
  makeReading('r040', '1091', '2026-04-12', 'morning', 163, 99, 70),
  makeReading('r041', '1091', '2026-04-12', 'evening', 157, 95, 72),
  // Day 24
  makeReading('r042', '1091', '2026-04-13', 'morning', 166, 101, 70),
  makeReading('r043', '1091', '2026-04-13', 'evening', 158, 96, 71),
  // Days 25-26: weekend misses — no rows
  // Day 27
  makeReading('r044', '1091', '2026-04-16', 'morning', 164, 100, 71),
  makeReading('r045', '1091', '2026-04-16', 'evening', 157, 95, 73),
  // Day 28
  makeReading('r046', '1091', '2026-04-17', 'morning', 162, 98, 70),
  makeReading('r047', '1091', '2026-04-17', 'evening', 155, 94, 72),
]

export const MOCK_READINGS_1093: Reading[] = [
  makeReading('r100', '1093', '2026-04-14', 'morning', 137, 84, 68),
  makeReading('r101', '1093', '2026-04-14', 'evening', 133, 81, 69),
  makeReading('r102', '1093', '2026-04-15', 'morning', 139, 85, 67),
  makeReading('r103', '1093', '2026-04-15', 'evening', 135, 83, 68),
  makeReading('r104', '1093', '2026-04-16', 'morning', 136, 83, 69),
  makeReading('r105', '1093', '2026-04-17', 'morning', 138, 84, 68),
  makeReading('r106', '1093', '2026-04-17', 'evening', 134, 82, 69),
]

// ─── Briefings ─────────────────────────────────────────────────────────────────

export const MOCK_BRIEFINGS: Record<string, Briefing> = {
  '1091': {
    briefing_id: 'b001',
    patient_id: '1091',
    appointment_date: TODAY,
    llm_response: {
      trend_summary:
        'Home monitoring shows sustained systolic elevation averaging 163 mmHg over the past 21 active monitoring days (excluding 4 absent days due to device outage). A brief pre-appointment dip to 148–153 mmHg was observed days 19–21, with readings returning to the 160–166 mmHg range in the most recent week. This pattern is consistent with a white-coat dip rather than genuine improvement.',
      medication_status:
        'Current regimen: Metoprolol Succinate 50mg, Lisinopril 10mg, Furosemide (Lasix) 40mg. Last medication change: 14 October 2025 — 186 days ago. No dose adjustments recorded since last clinic visit.',
      adherence_summary:
        'Synthetic confirmation signal indicates approximately 91% adherence across all three medications (977 of 1,092 scheduled doses confirmed). Adherence is highest for morning doses; weekend evening doses show a possible pattern of reduced confirmation. Given high adherence with sustained elevated readings, this pattern may warrant a treatment review rather than an adherence intervention.',
      active_problems: ['Congestive Heart Failure', 'Hypertension', 'Type 2 Diabetes Mellitus', 'Coronary Artery Disease'],
      overdue_labs: ['HbA1c (due 2026-01-14)', 'Basic Metabolic Panel (due 2026-02-21)', 'BNP (due 2026-03-01)'],
      visit_agenda: [
        'Review sustained elevated home BP (avg 163/99 mmHg) in context of high adherence signal — possible treatment review warranted',
        'Address 3 overdue laboratory investigations, including BNP given CHF history',
        'Assess current diuretic dosing in context of CHF and persistent elevation',
        'Review weekend medication pattern (possible reduced adherence on Saturday evenings)',
        'Discuss home monitoring device reliability following 2-day outage (April 5–6)',
        'Schedule next appointment within 4 weeks if regimen unchanged',
      ],
      urgent_flags: [
        'Therapeutic inertia: no medication change in 186 days with sustained readings ≥140 mmHg',
      ],
      risk_score: 74.5,
      data_limitations:
        'Home monitoring active. 28-day dataset includes 4 absent days (device outage days 16–17, weekend gaps days 25–26). Readings are synthetic — generated for demonstration purposes only.',
    },
    generated_at: '2026-04-18T07:30:00Z',
    delivered_at: '2026-04-18T07:30:05Z',
    read_at: null,
  },
  '1093': {
    briefing_id: 'b002',
    patient_id: '1093',
    appointment_date: '2026-05-02',
    llm_response: {
      trend_summary:
        'Recent home readings show systolic values in the 133–139 mmHg range, near the Stage 1 threshold. Trend is stable over the past 7 days of available data.',
      medication_status:
        'Current regimen: Ramipril 5mg. Last medication change: 1 February 2026 — 76 days ago.',
      adherence_summary:
        'Home monitoring active. Confirmation data not yet sufficient for pattern analysis (7 days of readings available).',
      active_problems: ['Hypertension'],
      overdue_labs: [],
      visit_agenda: [
        'Review recent home BP trend (borderline Stage 1 range)',
        'Assess response to Ramipril dose initiated in February',
        'Encourage continued home monitoring',
      ],
      urgent_flags: [],
      risk_score: 18.7,
      data_limitations:
        'Home monitoring active. Only 7 days of readings available — insufficient for full 28-day trend analysis.',
    },
    generated_at: '2026-04-18T07:30:00Z',
    delivered_at: '2026-04-18T07:30:05Z',
    read_at: null,
  },
}

// Briefing for Patient B (monitoring_active: false — EHR only)
export const MOCK_BRIEFING_1092: Briefing = {
  briefing_id: 'b003',
  patient_id: '1092',
  appointment_date: '2026-04-25',
  llm_response: {
    trend_summary:
      'No home monitoring data available for this patient. Last clinic reading: 152/94 mmHg (10 February 2026). Home monitoring pathway not active.',
    medication_status:
      'Current regimen: Amlodipine 5mg, Atorvastatin 20mg, Ibuprofen 400mg PRN. Last medication change: 3 December 2025.',
    adherence_summary:
      'No home confirmation data available. Adherence assessment not possible without home monitoring.',
    active_problems: ['Hypertension', 'Dyslipidaemia', 'Chronic Kidney Disease Stage 2'],
    overdue_labs: ['Renal function panel (due 2026-03-10)', 'Fasting lipid panel (due 2026-04-01)'],
    visit_agenda: [
      'Address 2 overdue laboratory investigations (renal function and lipids)',
      'Review NSAID use (Ibuprofen PRN) — possible interaction with antihypertensive regimen in CKD context',
      'Discuss home monitoring enrolment for future visits',
      'Assess clinic BP at appointment and compare to February reading',
    ],
    urgent_flags: [],
    risk_score: 41.2,
    data_limitations:
      'Home monitoring not active. Briefing generated from EHR data only. No home BP trend available.',
  },
  generated_at: '2026-04-18T07:30:00Z',
  delivered_at: '2026-04-18T07:30:05Z',
  read_at: null,
}

// ─── Alerts ────────────────────────────────────────────────────────────────────

export const MOCK_ALERTS: Alert[] = [
  {
    alert_id: 'a001',
    patient_id: '1091',
    alert_type: 'inertia',
    gap_days: null,
    systolic_avg: 163.2,
    triggered_at: '2026-04-17T07:30:00Z',
    delivered_at: '2026-04-17T07:30:05Z',
    acknowledged_at: null,
  },
  {
    alert_id: 'a002',
    patient_id: '1093',
    alert_type: 'gap_briefing',
    gap_days: 3,
    systolic_avg: null,
    triggered_at: '2026-04-15T07:30:00Z',
    delivered_at: '2026-04-15T07:30:05Z',
    acknowledged_at: null,
  },
]

// ─── Adherence ─────────────────────────────────────────────────────────────────

export const MOCK_ADHERENCE_1091: AdherenceData[] = [
  {
    medication_name: 'Metoprolol Succinate 50mg',
    rxnorm_code: '866514',
    adherence_pct: 93.5,
    total_doses: 56,
    confirmed_doses: 52,
  },
  {
    medication_name: 'Lisinopril 10mg',
    rxnorm_code: '314076',
    adherence_pct: 91.1,
    total_doses: 56,
    confirmed_doses: 51,
  },
  {
    medication_name: 'Furosemide (Lasix) 40mg',
    rxnorm_code: '313988',
    adherence_pct: 87.5,
    total_doses: 56,
    confirmed_doses: 49,
  },
]

// ─── Lookup helpers ────────────────────────────────────────────────────────────

export function getMockPatient(id: string): Patient | undefined {
  return MOCK_PATIENTS.find((p) => p.patient_id === id)
}

export function getMockBriefing(patientId: string): Briefing | undefined {
  if (patientId === '1092') return MOCK_BRIEFING_1092
  return MOCK_BRIEFINGS[patientId]
}

export function getMockReadings(patientId: string): Reading[] {
  if (patientId === '1091') return MOCK_READINGS_1091
  if (patientId === '1093') return MOCK_READINGS_1093
  return []
}

export function getMockAdherence(patientId: string): AdherenceData[] {
  if (patientId === '1091') return MOCK_ADHERENCE_1091
  return []
}
