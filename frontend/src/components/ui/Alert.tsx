import type { ReactNode } from 'react'

type AlertType = 'info' | 'success' | 'warning' | 'error'

interface AlertProps {
  type:      AlertType
  title?:    string
  children:  ReactNode
  onDismiss?: () => void
  className?: string
}

const styles: Record<AlertType, { wrap: string; icon: string; iconGlyph: string }> = {
  info: {
    wrap:      'bg-[var(--color-accent-dim)] border-blue-200 text-[var(--color-accent)]',
    icon:      'text-[var(--color-accent)]',
    iconGlyph: 'ℹ',
  },
  success: {
    wrap:      'bg-[var(--color-success-dim)] border-emerald-200 text-[var(--color-success)]',
    icon:      'text-[var(--color-success)]',
    iconGlyph: '✓',
  },
  warning: {
    wrap:      'bg-[var(--color-warning-dim)] border-amber-200 text-[var(--color-warning)]',
    icon:      'text-[var(--color-warning)]',
    iconGlyph: '⚠',
  },
  error: {
    wrap:      'bg-[var(--color-danger-dim)] border-red-200 text-[var(--color-danger)]',
    icon:      'text-[var(--color-danger)]',
    iconGlyph: '✕',
  },
}

export function Alert({ type, title, children, onDismiss, className = '' }: AlertProps) {
  const s = styles[type]
  return (
    <div
      className={`
        flex gap-3 p-3 rounded-lg border text-sm animate-fade-in
        ${s.wrap} ${className}
      `}
      role={type === 'error' ? 'alert' : 'status'}
    >
      <span className={`flex-shrink-0 font-bold text-base leading-none mt-0.5 ${s.icon}`}>
        {s.iconGlyph}
      </span>
      <div className="flex-1 min-w-0">
        {title && <div className="font-semibold mb-0.5">{title}</div>}
        <div className="opacity-90">{children}</div>
      </div>
      {onDismiss && (
        <button
          onClick={onDismiss}
          className="flex-shrink-0 opacity-60 hover:opacity-100 text-lg leading-none cursor-pointer"
          aria-label="Cerrar"
        >
          ×
        </button>
      )}
    </div>
  )
}

export function NetworkError({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <Alert type="error" title="Error de conexión">
      <span>{message}</span>
      {onRetry && (
        <button
          onClick={onRetry}
          className="ml-2 underline font-medium cursor-pointer"
        >
          Reintentar
        </button>
      )}
    </Alert>
  )
}
