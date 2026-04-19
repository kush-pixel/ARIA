// STUB: All functions return mock data now.
// To wire to the real backend, replace the mock return in each function
// with: const res = await fetch(`${API_BASE}<endpoint>`); return res.json()

import type { Patient, Briefing, Reading, Alert, AdherenceData } from './types'
import {
  MOCK_PATIENTS,
  getMockPatient,
  getMockBriefing,
  getMockReadings,
  getMockAdherence,
  MOCK_ALERTS,
} from './mockData'

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
void API_BASE // referenced when real fetch is wired

// GET /api/patients
export async function getPatients(): Promise<Patient[]> {
  return Promise.resolve(MOCK_PATIENTS)
}

// GET /api/patients/:id
export async function getPatient(id: string): Promise<Patient | null> {
  return Promise.resolve(getMockPatient(id) ?? null)
}

// GET /api/briefings/:patientId
export async function getBriefing(patientId: string): Promise<Briefing | null> {
  return Promise.resolve(getMockBriefing(patientId) ?? null)
}

// GET /api/readings?patient_id=:patientId
export async function getReadings(patientId: string): Promise<Reading[]> {
  return Promise.resolve(getMockReadings(patientId))
}

// GET /api/alerts
export async function getAlerts(): Promise<Alert[]> {
  return Promise.resolve(MOCK_ALERTS)
}

// GET /api/adherence/:patientId
export async function getAdherence(patientId: string): Promise<AdherenceData[]> {
  return Promise.resolve(getMockAdherence(patientId))
}

// POST /api/admin/trigger-scheduler
export async function triggerScheduler(): Promise<{ enqueued: number }> {
  // Simulate a short network delay for the spinner to be visible
  await new Promise((resolve) => setTimeout(resolve, 1200))
  return { enqueued: 2 }
}
