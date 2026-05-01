'use client'

const TOKEN_KEY = 'aria_patient_token'
const PATIENT_ID_KEY = 'aria_patient_id'

export function saveToken(token: string, patientId: string): void {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(PATIENT_ID_KEY, patientId)
}

export function getToken(): string | null {
  if (typeof window === 'undefined') return null
  return localStorage.getItem(TOKEN_KEY)
}

export function getPatientId(): string | null {
  if (typeof window === 'undefined') return null
  return localStorage.getItem(PATIENT_ID_KEY)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(PATIENT_ID_KEY)
}

export function isTokenValid(): boolean {
  const token = getToken()
  if (!token) return false
  try {
    // JWT payload is base64url-encoded second segment
    const payload = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')))
    return payload.exp * 1000 > Date.now()
  } catch {
    return false
  }
}
