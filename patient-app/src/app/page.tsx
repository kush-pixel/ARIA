'use client'

import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { login } from '@/lib/api'
import { saveToken, isTokenValid } from '@/lib/auth'

export default function LoginPage() {
  const router = useRouter()
  const [researchId, setResearchId] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (isTokenValid()) router.replace('/confirm')
  }, [router])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const { access_token } = await login(researchId.trim())
      saveToken(access_token, researchId.trim())
      router.replace('/confirm')
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Login failed'
      setError(msg.includes('404') ? 'Research ID not found.' : 'Login failed. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="min-h-screen flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <h1 className="text-2xl font-bold text-blue-700">ARIA</h1>
          <p className="text-gray-500 text-sm mt-1">My Health</p>
        </div>

        <form onSubmit={handleSubmit} className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6 space-y-4">
          <div>
            <label htmlFor="research-id" className="block text-sm font-medium text-gray-700 mb-1">
              Research ID
            </label>
            <input
              id="research-id"
              type="text"
              inputMode="numeric"
              value={researchId}
              onChange={e => setResearchId(e.target.value)}
              placeholder="e.g. 1091"
              required
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          {error && (
            <p className="text-red-600 text-sm">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading || !researchId.trim()}
            className="w-full bg-blue-700 text-white rounded-lg py-3 font-medium disabled:opacity-50"
          >
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>

        <p className="text-center text-xs text-gray-400 mt-6">
          Your research ID was provided by your clinical team.
        </p>
      </div>
    </main>
  )
}
