// Puerto Rico money formatting — always $ with 2 decimals, comma thousands
const PR_FORMATTER = new Intl.NumberFormat('en-PR', {
  style:                 'currency',
  currency:              'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

export function formatMoney(value: string | number): string {
  const num = typeof value === 'string' ? parseFloat(value) : value
  if (isNaN(num)) return '$—'
  return PR_FORMATTER.format(num)
}

export function formatMoneyCompact(value: string | number): string {
  const num = typeof value === 'string' ? parseFloat(value) : value
  if (isNaN(num)) return '$—'
  if (Math.abs(num) >= 1_000_000)
    return `$${(num / 1_000_000).toFixed(2)}M`
  if (Math.abs(num) >= 1_000)
    return `$${(num / 1_000).toFixed(1)}K`
  return PR_FORMATTER.format(num)
}

export function parseMoney(str: string): number {
  return parseFloat(str.replace(/[^0-9.-]/g, ''))
}
