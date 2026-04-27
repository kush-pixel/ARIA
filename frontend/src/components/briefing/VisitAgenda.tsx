interface VisitAgendaProps {
  items: string[]
}

const PRIORITY_COLORS = [
  'bg-red-500',
  'bg-amber-500',
  'bg-amber-400',
  'bg-teal-500',
  'bg-teal-400',
  'bg-slate-300',
]

export default function VisitAgenda({ items }: VisitAgendaProps) {
  if (items.length === 0) {
    return (
      <p className="text-[16px] text-slate-400 italic">
        No agenda items generated.
      </p>
    )
  }

  return (
    <ol className="space-y-2" aria-label="Visit agenda">
      {items.map((item, idx) => (
        <li
          key={idx}
          className="flex items-start gap-4 min-h-[48px] py-3 px-4 rounded-lg
                     bg-slate-50 dark:bg-slate-800/60 border border-slate-100 dark:border-slate-700"
        >
          {/* Priority dot + number */}
          <div className="flex-shrink-0 flex items-center gap-2 pt-0.5">
            <span
              className={`inline-block h-2.5 w-2.5 rounded-full flex-shrink-0 ${PRIORITY_COLORS[idx] ?? 'bg-slate-200'}`}
              aria-hidden
            />
            <span className="text-[14px] font-bold text-slate-400 dark:text-slate-500 w-4 tabular-nums">
              {idx + 1}
            </span>
          </div>
          <span className="text-[16px] text-slate-700 dark:text-slate-200 leading-snug">
            {item}
          </span>
        </li>
      ))}
    </ol>
  )
}
