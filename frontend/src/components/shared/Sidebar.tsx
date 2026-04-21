'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { Users, BellRing, Settings, ChevronLeft, ChevronRight } from 'lucide-react'
import { useState } from 'react'
import ThemeToggle from './ThemeToggle'

interface NavItem {
  label: string
  href: string
  icon: React.ReactNode
}

const NAV_ITEMS: NavItem[] = [
  { label: 'Patients', href: '/patients', icon: <Users size={20} strokeWidth={2} /> },
  { label: 'Alerts', href: '/alerts', icon: <BellRing size={20} strokeWidth={2} /> },
  { label: 'Admin', href: '/admin', icon: <Settings size={20} strokeWidth={2} /> },
]

export default function Sidebar() {
  const pathname = usePathname()
  const [collapsed, setCollapsed] = useState(false)

  return (
    <aside
      className={`flex flex-col h-full bg-white dark:bg-slate-900 border-r border-slate-200 dark:border-slate-700
                  transition-all duration-200 ${collapsed ? 'w-16' : 'w-56'}`}
    >
      {/* Branding */}
      <div className={`flex items-center gap-3 px-4 py-5 border-b border-slate-100 dark:border-slate-800 ${collapsed ? 'justify-center px-2' : ''}`}>
        {!collapsed && (
          <div>
            <div className="flex items-baseline gap-2">
              <span className="text-xl font-bold tracking-tight text-aria-purple">ARIA</span>
              <span className="text-xs font-medium bg-aria-purple/10 text-aria-purple px-1.5 py-0.5 rounded">
                v4.3
              </span>
            </div>
            <p className="text-xs text-slate-400 mt-0.5 leading-tight">
              Clinical Intelligence Platform
            </p>
          </div>
        )}
        {collapsed && (
          <span className="text-lg font-bold text-aria-purple">A</span>
        )}
      </div>

      {/* Nav items */}
      <nav className="flex-1 py-4 space-y-1 px-2" aria-label="Main navigation">
        {NAV_ITEMS.map((item) => {
          const isActive = pathname === item.href || pathname.startsWith(item.href + '/')
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-label={item.label}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-[15px] font-medium
                          transition-colors duration-150
                          ${isActive
                            ? 'bg-teal-50 text-teal-700 dark:bg-teal-900/40 dark:text-teal-400'
                            : 'text-slate-600 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800'
                          }
                          ${collapsed ? 'justify-center' : ''}`}
            >
              <span className="flex-shrink-0">{item.icon}</span>
              {!collapsed && <span>{item.label}</span>}
            </Link>
          )
        })}
      </nav>

      {/* Bottom: theme toggle + collapse */}
      <div className={`border-t border-slate-100 dark:border-slate-800 px-2 py-3 flex items-center ${collapsed ? 'flex-col gap-2' : 'justify-between'}`}>
        <ThemeToggle />
        <button
          onClick={() => setCollapsed((c) => !c)}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          className="flex items-center justify-center h-10 w-10 rounded-lg text-slate-400
                     hover:text-slate-600 hover:bg-slate-100 dark:hover:bg-slate-800
                     transition-colors duration-150"
        >
          {collapsed ? <ChevronRight size={18} /> : <ChevronLeft size={18} />}
        </button>
      </div>
    </aside>
  )
}
