import { useState, type ReactNode } from 'react'
import { Sidebar } from './Sidebar'

interface LayoutProps {
  children: ReactNode
}

export function Layout({ children }: LayoutProps) {
  const [sidebarOpen, setSidebarOpen] = useState(false)

  return (
    <div className="flex h-full overflow-hidden bg-[var(--color-bg)]">
      {/* Desktop sidebar */}
      <div className="hidden md:flex flex-shrink-0">
        <Sidebar />
      </div>

      {/* Mobile sidebar overlay */}
      {sidebarOpen && (
        <div className="md:hidden fixed inset-0 z-40 flex">
          <div
            className="absolute inset-0 bg-[var(--color-text)]/30 backdrop-blur-sm"
            onClick={() => setSidebarOpen(false)}
          />
          <div className="relative z-50 w-64 flex-shrink-0">
            <Sidebar mobile />
          </div>
        </div>
      )}

      {/* Main content area */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Mobile top bar */}
        <div className="md:hidden flex items-center gap-3 px-4 py-3 bg-[var(--color-surface)] border-b border-[var(--color-border)]">
          <button
            onClick={() => setSidebarOpen(true)}
            className="text-[var(--color-text-3)] text-xl leading-none cursor-pointer"
            aria-label="Abrir menú"
          >
            ☰
          </button>
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded bg-[var(--color-accent)] flex items-center justify-center">
              <span className="text-white text-[10px] font-bold font-mono">BC</span>
            </div>
            <span className="text-sm font-bold text-[var(--color-text)]">Bit-Counting</span>
          </div>
        </div>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto">
          {children}
        </main>
      </div>
    </div>
  )
}
