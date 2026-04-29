'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useTheme } from 'next-themes'
import { Users, BellRing, Settings, GitBranch, LogOut } from 'lucide-react'
import { useEffect, useState } from 'react'
import Image from 'next/image'
import { logout } from '@/lib/auth'

interface NavItem {
  label: string
  href: string
  icon: React.ReactNode
}

const NAV_ITEMS: NavItem[] = [
  { label: 'Patients',    href: '/patients',    icon: <Users size={18} strokeWidth={2} /> },
  { label: 'Alerts',      href: '/alerts',      icon: <BellRing size={18} strokeWidth={2} /> },
  { label: 'Shadow Mode', href: '/shadow-mode', icon: <GitBranch size={18} strokeWidth={2} /> },
  { label: 'Admin',       href: '/admin',       icon: <Settings size={18} strokeWidth={2} /> },
]

export default function Sidebar() {
  const pathname = usePathname()
  const { theme } = useTheme()
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])

  const isDark = mounted && theme === 'dark'

  return (
    <aside className="flex flex-col w-60 h-full bg-white dark:bg-[#111827] border-r border-gray-100 dark:border-[#1F2937] flex-shrink-0">

      {/* Logo — full width, centered */}
      <div className="flex flex-col items-center justify-center px-4 py-5 border-b border-gray-100 dark:border-[#1F2937]">
        {mounted ? (
          <Image
            src={isDark ? '/ARIA_DARK LOGO.jpg' : '/ARIA_LIGHT LOGO.jpg'}
            alt="ARIA Clinical Intelligence"
            width={180}
            height={100}
            priority
            className="object-contain"
          />
        ) : (
          <div className="w-[180px] h-[100px]" />
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-3 space-y-0.5" aria-label="Main navigation">
        <p className="px-3 mb-2 text-[10px] font-semibold uppercase tracking-widest text-gray-300 dark:text-gray-600">
          Navigation
        </p>
        {NAV_ITEMS.map((item) => {
          const isActive = pathname === item.href || pathname.startsWith(item.href + '/')
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-[14px] font-medium
                          transition-colors duration-150
                          ${isActive
                            ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400'
                            : 'text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-[#1F2937] hover:text-gray-900 dark:hover:text-gray-200'
                          }`}
            >
              <span className={`flex-shrink-0 ${isActive ? 'text-blue-600 dark:text-blue-400' : 'text-gray-400 dark:text-gray-500'}`}>
                {item.icon}
              </span>
              {item.label}
              {isActive && (
                <span className="ml-auto w-1.5 h-1.5 rounded-full bg-blue-600 dark:bg-blue-400" />
              )}
            </Link>
          )
        })}
      </nav>

      {/* Footer */}
      <div className="px-4 py-3 border-t border-gray-100 dark:border-[#1F2937]">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-full bg-blue-100 dark:bg-blue-900/40 flex items-center justify-center flex-shrink-0">
            <span className="text-[11px] font-bold text-blue-600 dark:text-blue-400">GP</span>
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-[12px] font-semibold text-gray-700 dark:text-gray-300 truncate">Clinician</p>
            <p className="text-[11px] text-gray-400 dark:text-gray-500 truncate">IIT Hypertension Clinic</p>
          </div>
          <button
            onClick={logout}
            aria-label="Sign out"
            title="Sign out"
            className="flex items-center justify-center w-7 h-7 rounded-lg text-gray-400
                       hover:bg-gray-100 dark:hover:bg-[#1F2937] hover:text-red-500
                       transition-colors flex-shrink-0"
          >
            <LogOut size={14} strokeWidth={2} />
          </button>
        </div>
      </div>
    </aside>
  )
}
