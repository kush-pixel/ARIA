import AlertInbox from '@/components/dashboard/AlertInbox'
import { BellRing } from 'lucide-react'

export const metadata = { title: 'Alerts | ARIA' }

export default function AlertsPage() {
  return (
    <div className="p-5">
      <div className="flex items-center gap-2.5 mb-1">
        <BellRing size={20} strokeWidth={2} className="text-blue-600" />
        <h1 className="text-[22px] font-bold text-gray-900 dark:text-gray-100">Alert Inbox</h1>
      </div>
      <p className="text-[14px] text-gray-500 dark:text-gray-400 mb-6">
        Unacknowledged alerts requiring clinical review.
      </p>
      <AlertInbox />
    </div>
  )
}
