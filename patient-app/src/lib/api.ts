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

// ---- Auth ----------------------------------------------------------------

export interface TokenResponse {
  access_token: string
  expires_in: number
}

export async function login(researchId: string): Promise<TokenResponse> {
  return apiFetch<TokenResponse>('/api/auth/patient-token', {
    method: 'POST',
    body: JSON.stringify({ research_id: researchId }),
  })
}

// ---- Readings ------------------------------------------------------------

export interface ReadingPayload {
  patient_id: string
  systolic_1: number
  diastolic_1: number
  heart_rate_1?: number
  systolic_2?: number
  diastolic_2?: number
  effective_datetime: string
  session: 'morning' | 'evening' | 'ad_hoc'
  medication_taken?: 'yes' | 'no' | 'partial'
  submitted_by: 'patient'
  symptoms?: string[]
}

export async function submitReading(payload: ReadingPayload): Promise<void> {
  await apiFetch('/api/readings', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

// ---- Confirmations -------------------------------------------------------

export interface PendingConfirmation {
  confirmation_id: string
  medication_name: string
  rxnorm_code: string | null
  scheduled_time: string
}

export async function getPendingConfirmations(patientId: string): Promise<PendingConfirmation[]> {
  return apiFetch<PendingConfirmation[]>(`/api/confirmations/pending?patient_id=${patientId}`)
}

export async function confirmDoses(patientId: string, confirmationIds: string[]): Promise<void> {
  await apiFetch('/api/confirmations/confirm', {
    method: 'POST',
    body: JSON.stringify({ patient_id: patientId, confirmation_ids: confirmationIds }),
  })
}

export function icsDownloadUrl(patientId: string): string {
  const token = getToken()
  // Trigger download via fetch so auth header can be sent
  return `${API_BASE}/api/confirmations/ics/${patientId}?token=${token ?? ''}`
}

export async function downloadIcs(patientId: string): Promise<void> {
  const token = getToken()
  const res = await fetch(`${API_BASE}/api/confirmations/ics/${patientId}`, {
    headers: { Authorization: `Bearer ${token ?? ''}` },
  })
  if (!res.ok) throw new Error(`Download failed: ${res.status}`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'aria-medications.ics'
  a.click()
  URL.revokeObjectURL(url)
}
