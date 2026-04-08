import type { ReactNode } from 'react'
import { useSystemSummary } from '../../hooks/useReports'
import { useWsStatus } from '../../store/wsStore'

interface HeaderProps {
  title:    string
  subtitle?: string
  actions?: ReactNode
}

export function Header({ title, subtitle, actions }: HeaderProps) {
  const ws = useWsStatus()

  return (
    <div className="
      flex items-center justify-between gap-4
      px-6 py-4
      border-b border-[var(--color-border)]
      bg-[var(--color-surface)]
    ">
      <div>
        <h1 className="text-base font-semibold text-[var(--color-text)] leading-tight">{title}</h1>
        {subtitle && (
          <p className="text-xs text-[var(--color-text-4)] mt-0.5">{subtitle}</p>
        )}
      </div>

      <div className="flex items-center gap-3">
        {/* Live indicator */}
        {ws === 'connected' && (
          <div className="hidden sm:flex items-center gap-1.5 text-xs text-[var(--color-success)]">
            <span className="w-1.5 h-1.5 rounded-full bg-[var(--color-success)] animate-pulse-dot" />
            En vivo
          </div>
        )}
        {actions}
      </div>
    </div>
  )
}

// Small stat widget for header
export function HeaderStat({ label, value, color }: { label: string; value: string | number; color?: string }) {
  return (
    <div className="text-right hidden md:block">
      <div className={`text-base font-mono font-bold ${color ?? 'text-[var(--color-text)]'}`}>
        {value}
      </div>
      <div className="text-[10px] text-[var(--color-text-4)] uppercase tracking-wider">{label}</div>
    </div>
  )
}

// System health strip shown across top on CPA/Admin views
export function SystemHealthStrip() {
  const { data } = useSystemSummary()
  if (!data) return null

  const stats = [
    { label: 'Procesados', value: data.total_documents_processed },
    { label: 'Pausas activas', value: data.active_pauses, color: data.active_pauses > 0 ? 'text-[var(--color-warning)]' : undefined },
    { label: 'Decisiones', value: data.total_orchestrator_decisions },
    { label: 'Revisión CPA', value: data.decisions_requiring_cpa_review, color: data.decisions_requiring_cpa_review > 0 ? 'text-[var(--color-danger)]' : undefined },
  ]

  return (
    <div className="flex items-center gap-6 px-6 py-2 bg-[var(--color-surface-2)] border-b border-[var(--color-border)] text-xs overflow-x-auto">
      {stats.map((s) => (
        <div key={s.label} className="flex items-center gap-1.5 flex-shrink-0">
          <span className="text-[var(--color-text-4)]">{s.label}:</span>
          <span className={`font-mono font-semibold ${s.color ?? 'text-[var(--color-text-2)]'}`}>
            {s.value}
          </span>
        </div>
      ))}
    </div>
  )
}
