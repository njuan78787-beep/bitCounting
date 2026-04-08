import { type ButtonHTMLAttributes, type ReactNode } from 'react'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'success' | 'warning'
type Size    = 'sm' | 'md' | 'lg'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?:  Variant
  size?:     Size
  loading?:  boolean
  icon?:     ReactNode
  children?: ReactNode
}

const base = `
  inline-flex items-center justify-center gap-2
  font-medium leading-none rounded-lg
  border transition-all duration-150
  focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2
  disabled:opacity-50 disabled:pointer-events-none
  cursor-pointer select-none
`.replace(/\s+/g, ' ').trim()

const variants: Record<Variant, string> = {
  primary:   'bg-[var(--color-accent)] border-[var(--color-accent)] text-white hover:bg-blue-700 focus-visible:ring-blue-500',
  secondary: 'bg-[var(--color-surface)] border-[var(--color-border-2)] text-[var(--color-text-2)] hover:bg-[var(--color-surface-2)] focus-visible:ring-blue-400',
  ghost:     'bg-transparent border-transparent text-[var(--color-text-3)] hover:bg-[var(--color-surface-3)] hover:text-[var(--color-text-2)] focus-visible:ring-blue-400',
  danger:    'bg-[var(--color-danger)] border-[var(--color-danger)] text-white hover:bg-red-700 focus-visible:ring-red-500',
  success:   'bg-[var(--color-success)] border-[var(--color-success)] text-white hover:bg-emerald-700 focus-visible:ring-emerald-500',
  warning:   'bg-[var(--color-warning)] border-[var(--color-warning)] text-white hover:bg-amber-600 focus-visible:ring-amber-400',
}

const sizes: Record<Size, string> = {
  sm: 'h-7  px-3  text-xs gap-1.5',
  md: 'h-9  px-4  text-sm',
  lg: 'h-11 px-5  text-sm',
}

export function Button({
  variant  = 'primary',
  size     = 'md',
  loading  = false,
  icon,
  children,
  className = '',
  disabled,
  ...rest
}: ButtonProps) {
  return (
    <button
      {...rest}
      disabled={disabled ?? loading}
      className={`${base} ${variants[variant]} ${sizes[size]} ${className}`}
    >
      {loading ? (
        <span className="inline-block w-3.5 h-3.5 border-2 border-current border-t-transparent rounded-full animate-spin" />
      ) : icon}
      {children}
    </button>
  )
}
