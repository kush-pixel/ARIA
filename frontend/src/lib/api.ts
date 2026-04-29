import type { Patient, Briefing, Reading, Alert, AdherenceData, ShadowModeResults } from './types'

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const detail = await res.text()
    throw new Error(`API ${res.status}: ${detail}`)
  }
  return res.json() as Promise<T>
}

// GET /api/patients
export async function getPatients(): Promise<Patient[]> {
  return apiFetch<Patient[]>('/api/patients')
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

// GET /api/alerts
export async function getAlerts(): Promise<Alert[]> {
  return apiFetch<Alert[]>('/api/alerts')
}

// GET /api/adherence/:patientId
export async function getAdherence(patientId: string): Promise<AdherenceData[]> {
  return apiFetch<AdherenceData[]>(`/api/adherence/${patientId}`)
}

// POST /api/alerts/:id/acknowledge
export async function acknowledgeAlert(alertId: string): Promise<void> {
  await apiFetch<unknown>(`/api/alerts/${alertId}/acknowledge`, { method: 'POST' })
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
