const TOKEN_KEY = 'aria_clinician_token'

export function getToken(): string | null {
  if (typeof window === 'undefined') return null
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

export function isAuthenticated(): boolean {
  return getToken() !== null
}

export async function loginClinician(username: string, password: string): Promise<void> {
  const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
  const res = await fetch(`${API_BASE}/api/auth/clinician-token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: 'Login failed' }))
    throw new Error(detail.detail ?? 'Login failed')
  }
  const data = await res.json()
  setToken(data.access_token)
}

export function logout(): void {
  clearToken()
  window.location.href = '/login'
}
