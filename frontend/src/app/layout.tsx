import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import { ThemeProvider } from 'next-themes'
import Sidebar from '@/components/shared/Sidebar'
import Topbar from '@/components/shared/Topbar'
import './globals.css'

const inter = Inter({
  subsets: ['latin'],
  variable: '--font-inter',
  display: 'swap',
})

export const metadata: Metadata = {
  title: 'ARIA — Clinical Intelligence Platform',
  description: 'Adaptive Real-time Intelligence Architecture for hypertension management',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={inter.variable}>
      <body className="font-sans">
        <ThemeProvider attribute="class" defaultTheme="light" enableSystem={false}>
          <div className="flex h-screen overflow-hidden bg-gray-50 dark:bg-[#0B1220]">
            <Sidebar />
            <div className="flex flex-col flex-1 overflow-hidden">
              <Topbar />
              <main className="flex-1 overflow-y-auto">
                {children}
              </main>
            </div>
          </div>
        </ThemeProvider>
      </body>
    </html>
  )
}
