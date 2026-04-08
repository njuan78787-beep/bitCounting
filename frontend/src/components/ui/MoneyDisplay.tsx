import { formatMoney, formatMoneyCompact } from '../../utils/money'

interface MoneyDisplayProps {
  value:    string | number
  compact?: boolean
  size?:    'sm' | 'md' | 'lg' | 'xl'
  color?:   'default' | 'success' | 'danger' | 'muted'
  className?: string
}

const sizes = {
  sm: 'text-sm',
  md: 'text-base',
  lg: 'text-lg',
  xl: 'text-2xl',
}

const colors = {
  default: 'text-[var(--color-text)]',
  success: 'text-[var(--color-success)]',
  danger:  'text-[var(--color-danger)]',
  muted:   'text-[var(--color-text-4)]',
}

export function MoneyDisplay({
  value,
  compact  = false,
  size     = 'md',
  color    = 'default',
  className = '',
}: MoneyDisplayProps) {
  const formatted = compact ? formatMoneyCompact(value) : formatMoney(value)
  const num = typeof value === 'string' ? parseFloat(value) : value
  const isNeg = !isNaN(num) && num < 0

  return (
    <span
      className={`font-mono tabular font-semibold ${sizes[size]} ${isNeg ? 'text-[var(--color-danger)]' : colors[color]} ${className}`}
    >
      {formatted}
    </span>
  )
}
