interface SpinnerProps {
  size?: 'sm' | 'md' | 'lg'
  className?: string
}

const sizes = { sm: 'w-4 h-4 border-2', md: 'w-6 h-6 border-2', lg: 'w-8 h-8 border-[3px]' }

export function Spinner({ size = 'md', className = '' }: SpinnerProps) {
  return (
    <span
      className={`inline-block rounded-full border-[var(--color-accent)] border-t-transparent animate-spin ${sizes[size]} ${className}`}
      role="status"
      aria-label="Cargando…"
    />
  )
}

export function FullPageSpinner() {
  return (
    <div className="flex items-center justify-center w-full h-full min-h-[200px]">
      <div className="flex flex-col items-center gap-3">
        <Spinner size="lg" />
        <span className="text-xs text-[var(--color-text-4)] tracking-wider uppercase">Cargando</span>
      </div>
    </div>
  )
}
