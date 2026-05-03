import type { Patient, PaginatedPatients, Briefing, Reading, Alert, AdherenceData, ShadowModeResults, ChatDoneEvent } from './types'
import { getToken } from './auth'

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

function authHeaders(): Record<string, string> {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    ...options,
  })
  if (!res.ok) {
    const detail = await res.text()
    throw new Error(`API ${res.status}: ${detail}`)
  }
  return res.json() as Promise<T>
}

// GET /api/patients
export interface GetPatientsParams {
  page?: number
  pageSize?: number
  search?: string
  tier?: 'all' | 'high' | 'medium' | 'low'
}

export async function getPatients(params: GetPatientsParams = {}): Promise<PaginatedPatients> {
  const { page = 1, pageSize = 25, search = '', tier = 'all' } = params
  const qs = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
    search,
    tier,
  })
  return apiFetch<PaginatedPatients>(`/api/patients?${qs}`)
}

// GET /api/patients/:id
export async function getPatient(id: string): Promise<Patient | null> {
  try {
    return await apiFetch<Patient>(`/api/patients/${id}`)
  } catch {
    return null
  }
}

// GET /api/briefings/:patientId
export async function getBriefing(patientId: string): Promise<Briefing | null> {
  try {
    return await apiFetch<Briefing>(`/api/briefings/${patientId}`)
  } catch {
    return null
  }
}

// GET /api/readings?patient_id=:patientId
export async function getReadings(patientId: string): Promise<Reading[]> {
  return apiFetch<Reading[]>(`/api/readings?patient_id=${patientId}`)
}

// GET /api/patients/:patientId/baseline
export async function getPatientBaseline(
  patientId: string,
): Promise<{ baseline_systolic: number; reading_count: number }> {
  try {
    return await apiFetch(`/api/patients/${patientId}/baseline`)
  } catch {
    return { baseline_systolic: 163.0, reading_count: 0 }
  }
}

// GET /api/alerts
export async function getAlerts(): Promise<Alert[]> {
  return apiFetch<Alert[]>('/api/alerts')
}

// GET /api/adherence/:patientId
export async function getAdherence(patientId: string): Promise<AdherenceData[]> {
  return apiFetch<AdherenceData[]>(`/api/adherence/${patientId}`)
}

// POST /api/alerts/:id/acknowledge
export type AlertDisposition = 'agree_acting' | 'agree_monitoring' | 'disagree'

export async function overrideTier(
  patientId: string,
  risk_tier: 'high' | 'medium' | 'low',
  reason: string,
): Promise<Patient> {
  return apiFetch<Patient>(`/api/patients/${patientId}/tier`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ risk_tier, reason }),
  })
}

export async function acknowledgeAlert(alertId: string, disposition: AlertDisposition): Promise<void> {
  await apiFetch<unknown>(`/api/alerts/${alertId}/acknowledge`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ disposition }),
  })
}

// GET /api/alerts/acknowledged
export async function getAcknowledgedAlerts(): Promise<Alert[]> {
  return apiFetch<Alert[]>('/api/alerts/acknowledged')
}

// POST /api/alerts/:id/unacknowledge
export async function unacknowledgeAlert(alertId: string): Promise<void> {
  await apiFetch<unknown>(`/api/alerts/${alertId}/unacknowledge`, { method: 'POST' })
}

// POST /api/admin/trigger-scheduler
export async function triggerScheduler(): Promise<{ enqueued: number }> {
  return apiFetch<{ enqueued: number }>('/api/admin/trigger-scheduler', { method: 'POST' })
}

// GET /api/shadow-mode/results
export async function getShadowModeResults(): Promise<ShadowModeResults | null> {
  try {
    return await apiFetch<ShadowModeResults>('/api/shadow-mode/results')
  } catch {
    return null
  }
}

// GET /api/chat/suggested-questions/:patientId
export async function getSuggestedQuestions(
  patientId: string,
): Promise<{ questions: string[]; proactive: string | null }> {
  try {
    return await apiFetch(`/api/chat/suggested-questions/${patientId}`)
  } catch {
    return { questions: [], proactive: null }
  }
}

// POST /api/chat — returns fetch Response for SSE streaming
export async function chatStream(
  patientId: string,
  question: string,
): Promise<Response> {
  return fetch(`${API_BASE}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ patient_id: patientId, question }),
  })
}

// DELETE /api/chat/session
export async function clearChatSession(patientId: string): Promise<void> {
  await apiFetch('/api/chat/session', {
    method: 'DELETE',
    body: JSON.stringify({ patient_id: patientId }),
  })
}

// POST /api/chat/summary/:patientId
export async function getChatSummary(patientId: string): Promise<{ summary: string | null }> {
  return apiFetch<{ summary: string | null }>(`/api/chat/summary/${patientId}`, { method: 'POST' })
}

// POST /api/chat/feedback
export async function submitChatFeedback(
  patientId: string,
  messageIndex: number,
  rating: 'up' | 'down',
): Promise<void> {
  await apiFetch<unknown>('/api/chat/feedback', {
    method: 'POST',
    body: JSON.stringify({ patient_id: patientId, message_index: messageIndex, rating }),
  })
}
