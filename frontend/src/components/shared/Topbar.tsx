'use client'

import { useTheme } from 'next-themes'
import { Sun, Moon, Search, Bell } from 'lucide-react'
import { useEffect, useState } from 'react'

export default function Topbar() {
  const { theme, setTheme } = useTheme()
  const [mounted, setMounted] = useState(false)

  useEffect(() => setMounted(true), [])

  const isDark = theme === 'dark'

  return (
    <header className="flex items-center justify-between h-16 px-6
                       bg-white dark:bg-[#111827]
                       border-b border-gray-100 dark:border-[#1F2937]
                       flex-shrink-0">
      {/* Search */}
      <div className="relative w-[360px]">
        <Search
          size={16}
          strokeWidth={2}
          className="absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400 dark:text-gray-500 pointer-events-none"
        />
        <input
          type="search"
          placeholder="Search patients by ID, name, or condition…"
          className="h-11 w-full pl-10 pr-4 text-[14px] rounded-xl
                     bg-white dark:bg-[#111827]
                     border border-slate-300 dark:border-[#374151]
                     text-gray-900 dark:text-[#E5E7EB]
                     placeholder:text-gray-400 dark:placeholder:text-gray-500
                     shadow-sm
                     focus:outline-none focus:ring-2 focus:ring-blue-600 focus:border-blue-600
                     dark:focus:ring-blue-500 dark:focus:border-blue-500
                     transition-all duration-150"
        />
      </div>

      {/* Right controls */}
      <div className="flex items-center gap-2">
        {/* Alert bell */}
        <button
          aria-label="View alerts"
          className="relative flex items-center justify-center w-9 h-9 rounded-lg
                     text-gray-500 dark:text-gray-400
                     hover:bg-gray-100 dark:hover:bg-[#1F2937]
                     transition-colors duration-150"
        >
          <Bell size={18} strokeWidth={2} />
        </button>

        {/* Divider */}
        <div className="w-px h-5 bg-gray-200 dark:bg-gray-700 mx-1" />

        {/* Theme toggle */}
        {mounted && (
          <button
            onClick={() => setTheme(isDark ? 'light' : 'dark')}
            aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
            className="flex items-center justify-center w-9 h-9 rounded-lg
                       text-gray-500 dark:text-gray-400
                       hover:bg-gray-100 dark:hover:bg-[#1F2937]
                       transition-colors duration-150"
          >
            {isDark ? <Sun size={18} strokeWidth={2} /> : <Moon size={18} strokeWidth={2} />}
          </button>
        )}
      </div>
    </header>
  )
}
