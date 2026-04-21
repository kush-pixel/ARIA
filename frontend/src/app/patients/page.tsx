import PatientList from '@/components/dashboard/PatientList'

export const metadata = { title: 'Patients — ARIA' }

export default function PatientsPage() {
  return (
    <div className="p-8">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">
          Patient Panel
        </h1>
        <p className="mt-1 text-[16px] text-slate-500 dark:text-slate-400">
          Sorted by clinical priority. High-risk patients first.
        </p>
      </div>
      <PatientList />
    </div>
  )
}
