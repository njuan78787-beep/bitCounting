import type { ReactNode, HTMLAttributes } from 'react'

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  elevated?: boolean
  children:  ReactNode
  padding?:  'none' | 'sm' | 'md' | 'lg'
}

const paddings = {
  none: '',
  sm:   'p-3',
  md:   'p-4',
  lg:   'p-6',
}

export function Card({ elevated = false, children, padding = 'md', className = '', ...rest }: CardProps) {
  return (
    <div
      className={`card ${elevated ? 'card-elevated' : ''} ${paddings[padding]} ${className}`}
      {...rest}
    >
      {children}
    </div>
  )
}

interface CardHeaderProps {
  title:     ReactNode
  subtitle?: ReactNode
  actions?:  ReactNode
  icon?:     ReactNode
}

export function CardHeader({ title, subtitle, actions, icon }: CardHeaderProps) {
  return (
    <div className="flex items-start justify-between gap-3 mb-4">
      <div className="flex items-center gap-3 min-w-0">
        {icon && (
          <div className="flex-shrink-0 w-8 h-8 rounded-lg bg-[var(--color-accent-dim)] flex items-center justify-center text-[var(--color-accent)]">
            {icon}
          </div>
        )}
        <div className="min-w-0">
          <div className="text-sm font-semibold text-[var(--color-text)] truncate">{title}</div>
          {subtitle && <div className="text-xs text-[var(--color-text-4)] mt-0.5">{subtitle}</div>}
        </div>
      </div>
      {actions && <div className="flex-shrink-0 flex items-center gap-2">{actions}</div>}
    </div>
  )
}

export function CardSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="card p-4 space-y-3">
      <div className="skeleton h-4 w-2/5" />
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton h-3" style={{ width: `${60 + (i % 3) * 15}%` }} />
      ))}
    </div>
  )
}
