import type { ReactNode } from 'react'
import type { PauseSeverity, TransactionStatus } from '../../types/api'

type Color = 'accent' | 'success' | 'warning' | 'danger' | 'info' | 'neutral'

interface BadgeProps {
  color?:    Color
  dot?:      boolean
  children:  ReactNode
  className?: string
}

const colors: Record<Color, string> = {
  accent:  'bg-[var(--color-accent-dim)]  text-[var(--color-accent)]  border-blue-200',
  success: 'bg-[var(--color-success-dim)] text-[var(--color-success)] border-emerald-200',
  warning: 'bg-[var(--color-warning-dim)] text-[var(--color-warning)] border-amber-200',
  danger:  'bg-[var(--color-danger-dim)]  text-[var(--color-danger)]  border-red-200',
  info:    'bg-[var(--color-info-dim)]    text-[var(--color-info)]    border-violet-200',
  neutral: 'bg-[var(--color-surface-3)]  text-[var(--color-text-3)]  border-[var(--color-border)]',
}

const dotColors: Record<Color, string> = {
  accent:  'bg-[var(--color-accent)]',
  success: 'bg-[var(--color-success)]',
  warning: 'bg-[var(--color-warning)]',
  danger:  'bg-[var(--color-danger)]',
  info:    'bg-[var(--color-info)]',
  neutral: 'bg-[var(--color-text-4)]',
}

export function Badge({ color = 'neutral', dot = false, children, className = '' }: BadgeProps) {
  return (
    <span className={`
      inline-flex items-center gap-1.5 px-2 py-0.5
      text-xs font-medium rounded-full border
      ${colors[color]} ${className}
    `}>
      {dot && (
        <span className={`w-1.5 h-1.5 rounded-full animate-pulse-dot ${dotColors[color]}`} />
      )}
      {children}
    </span>
  )
}

// ─── Domain-specific badges ───────────────────────────────────────────────────

export function SeverityBadge({ severity }: { severity: PauseSeverity }) {
  const map: Record<PauseSeverity, Color> = {
    LOW:      'success',
    MEDIUM:   'warning',
    HIGH:     'danger',
    CRITICAL: 'danger',
  }
  return <Badge color={map[severity]} dot>{severity}</Badge>
}

export function StatusBadge({ status }: { status: TransactionStatus }) {
  const map: Record<TransactionStatus, Color> = {
    pending:   'accent',
    processed: 'success',
    paused:    'warning',
    rejected:  'danger',
  }
  const labels: Record<TransactionStatus, string> = {
    pending:   'Pendiente',
    processed: 'Procesado',
    paused:    'Pausado',
    rejected:  'Rechazado',
  }
  return <Badge color={map[status]} dot>{labels[status]}</Badge>
}
