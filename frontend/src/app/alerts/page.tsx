import AlertInbox from '@/components/dashboard/AlertInbox'

export const metadata = { title: 'Alerts — ARIA' }

export default function AlertsPage() {
  return (
    <div className="p-8">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">
          Alert Inbox
        </h1>
        <p className="mt-1 text-[16px] text-slate-500 dark:text-slate-400">
          Unacknowledged alerts requiring clinical review.
        </p>
      </div>
      <AlertInbox />
    </div>
  )
}
