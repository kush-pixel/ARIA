'use client'

interface SuggestedQuestionsProps {
  questions: string[]
  proactive: string | null
  onSelect: (q: string) => void
}

export default function SuggestedQuestions({
  questions,
  proactive,
  onSelect,
}: SuggestedQuestionsProps) {
  if (questions.length === 0 && !proactive) return null

  return (
    <div className="space-y-2 mb-3">
      {proactive && (
        <div className="rounded-lg bg-teal-50 dark:bg-teal-900/20 border border-teal-200
                        dark:border-teal-700 px-3 py-2">
          <p className="text-[11px] font-medium text-teal-700 dark:text-teal-400 mb-1">
            ARIA suggests
          </p>
          <button
            onClick={() => onSelect(proactive)}
            className="text-[13px] text-teal-800 dark:text-teal-200 text-left hover:underline"
          >
            {proactive}
          </button>
        </div>
      )}

      {questions.length > 0 && (
        <div className="flex flex-col gap-1.5">
          {questions.map((q, i) => (
            <button
              key={i}
              onClick={() => onSelect(q)}
              className="w-full text-left px-3 py-2 rounded-lg border border-gray-200
                         dark:border-[#374151] text-[12px] text-gray-600 dark:text-gray-300
                         hover:bg-gray-100 dark:hover:bg-[#1F2937] hover:border-teal-400
                         dark:hover:border-teal-600 transition-colors"
            >
              {q}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
