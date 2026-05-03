import { Suspense } from 'react'
import PatientList from '@/components/dashboard/PatientList'
import { Users } from 'lucide-react'

export const metadata = { title: 'Patients | ARIA' }

export default function PatientsPage() {
  return (
    <div className="p-5">
      <div className="flex items-start justify-between mb-4">
        <div>
          <div className="flex items-center gap-2.5 mb-1">
            <Users size={20} strokeWidth={2} className="text-blue-600" />
            <h1 className="text-[22px] font-bold text-gray-900 dark:text-gray-100">
              Patient Panel
            </h1>
          </div>
          <p className="text-[14px] text-gray-500 dark:text-gray-400">
            Sorted by clinical priority. High-risk patients first.
          </p>
        </div>
        <div className="text-right">
          <p className="text-[12px] text-gray-400 dark:text-gray-500">Today</p>
          <p className="text-[14px] font-semibold text-gray-700 dark:text-gray-300">
            {new Date().toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' })}
          </p>
        </div>
      </div>
      <Suspense fallback={null}>
        <PatientList />
      </Suspense>
    </div>
  )
}
