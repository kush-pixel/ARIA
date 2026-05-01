import Sidebar from '@/components/shared/Sidebar'
import Topbar from '@/components/shared/Topbar'
import { TourProvider } from '@/components/shared/Tour'

export default function MainLayout({ children }: { children: React.ReactNode }) {
  return (
    <TourProvider>
      <div className="flex h-screen overflow-hidden bg-gray-50 dark:bg-[#0B1220]">
        <Sidebar />
        <div className="flex flex-col flex-1 overflow-hidden">
          <Topbar />
          <main className="flex-1 overflow-y-auto">
            {children}
          </main>
        </div>
      </div>
    </TourProvider>
  )
}
