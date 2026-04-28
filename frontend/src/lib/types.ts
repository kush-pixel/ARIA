export type RiskTier = 'high' | 'medium' | 'low'

export interface Patient {
  patient_id: string
  gender: 'M' | 'F' | 'U'
  age: number
  risk_tier: RiskTier
  tier_override: string | null
  risk_score: number | null
  risk_score_computed_at: string | null
  monitoring_active: boolean
  next_appointment: string | null
  enrolled_at: string
  enrolled_by: string
  has_briefing: boolean
}

export interface ClinicalContext {
  patient_id: string
  active_problems: string[]
  problem_codes: string[]
  current_medications: string[]
  med_rxnorm_codes: string[]
  last_med_change: string | null
  allergies: string[]
  last_visit_date: string | null
  last_clinic_systolic: number | null
  last_clinic_diastolic: number | null
  overdue_labs: string[]
  social_context: string | null
}

export interface Reading {
  reading_id: string
  patient_id: string
  systolic_1: number
  diastolic_1: number
  heart_rate_1: number | null
  systolic_2: number | null
  diastolic_2: number | null
  heart_rate_2: number | null
  systolic_avg: number
  diastolic_avg: number
  heart_rate_avg: number | null
  effective_datetime: string
  session: 'morning' | 'evening' | 'ad_hoc'
  source: 'generated' | 'manual' | 'ble_auto' | 'clinic'
  submitted_by: string
}

export interface BriefingPayload {
  trend_summary: string
  medication_status: string
  adherence_summary: string
  active_problems: string[]
  overdue_labs: string[]
  visit_agenda: string[]
  urgent_flags: string[]
  risk_score: number | null
  data_limitations: string
  readable_summary?: string
}

export interface Briefing {
  briefing_id: string
  patient_id: string
  appointment_date: string
  llm_response: BriefingPayload
  generated_at: string
  delivered_at: string | null
  read_at: string | null
}

export interface Alert {
  alert_id: string
  patient_id: string
  alert_type: 'gap_urgent' | 'gap_briefing' | 'inertia' | 'deterioration' | 'adherence'
  gap_days: number | null
  systolic_avg: number | null
  triggered_at: string
  delivered_at: string | null
  acknowledged_at: string | null
  off_hours: boolean
  escalated: boolean
}

export interface AdherenceData {
  medication_name: string
  rxnorm_code: string | null
  adherence_pct: number
  total_doses: number
  confirmed_doses: number
}

export interface ShadowModeDetectors {
  gap: { fired: boolean; gap_days: number; urgent: boolean }
  inertia: { fired: boolean; avg_systolic: number; no_med_change: boolean }
  adherence: { fired: boolean; pattern: string; overall_pct: number; interpretation: string }
  deterioration: { fired: boolean; slope: number | null }
}

export interface ActiveProblem {
  name: string
  flag: string | null
  status: string
  assessment: string
}

export interface ShadowModeWithoutAria {
  last_clinic_systolic: number | null
  last_clinic_date: string | null
  days_since_last_visit: number
  medications: string[]
  known_problems: string[]
  other_active_problems: ActiveProblem[]
  pending_followups: string[]
}

export interface ShadowModeWithAria {
  fired: boolean
  detectors: ShadowModeDetectors
  visit_agenda: string[]
  urgent_flags: string[]
  adherence_pct: number
}

export interface BetweenVisitAlert {
  alert_date: string
  alert_type: string
  days_before_visit: number
  message: string
  reasons?: string[]
}

export interface ShadowModeVisit {
  visit_index: number
  visit_date: string
  systolic: number
  diastolic: number
  physician_label: 'concerned' | 'stable' | 'no_ground_truth'
  source: 'clinic_bp' | 'no_vitals_assessment'
  days_since_prior_visit: number
  without_aria: ShadowModeWithoutAria
  with_aria: ShadowModeWithAria
  synthetic_readings: Array<{
    day: number
    date: string
    systolic_avg: number
    diastolic_avg: number
    session: string
  }>
  result: 'agree' | 'false_negative' | 'false_positive' | 'no_ground_truth'
  between_visit_alerts: BetweenVisitAlert[]
}

export interface ShadowModeBestWindow {
  visit_indices: number[]
  date_from: string
  date_to: string
  summary: string
}

export interface ShadowModeResults {
  generated_at: string
  patient_id: string
  total_eval_points: number
  skipped: number
  clinic_bp_points: number
  no_vitals_points: number
  with_ground_truth: number
  concerned_flag1_or_2: number
  stable_flag3: number
  no_ground_truth: number
  agreements: number
  false_negatives: number
  false_positives: number
  agreement_pct: number
  passed: boolean
  best_demo_window: ShadowModeBestWindow | null
  visits: ShadowModeVisit[]
}
