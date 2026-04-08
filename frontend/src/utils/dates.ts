const PR_DATE = new Intl.DateTimeFormat('es-PR', {
  year: 'numeric', month: 'short', day: '2-digit',
  timeZone: 'America/Puerto_Rico',
})
const PR_DATETIME = new Intl.DateTimeFormat('es-PR', {
  year: 'numeric', month: 'short', day: '2-digit',
  hour: '2-digit', minute: '2-digit',
  timeZone: 'America/Puerto_Rico',
})

export function formatDate(iso: string): string {
  try { return PR_DATE.format(new Date(iso)) } catch { return iso }
}

export function formatDateTime(iso: string): string {
  try { return PR_DATETIME.format(new Date(iso)) } catch { return iso }
}

export function currentYearMonth(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

export function periodStart(months = 0): string {
  const d = new Date()
  d.setMonth(d.getMonth() - months, 1)
  return d.toISOString().slice(0, 10)
}

export function periodEnd(months = 0): string {
  const d = new Date()
  d.setMonth(d.getMonth() - months + 1, 0)   // last day of target month
  return d.toISOString().slice(0, 10)
}

export function slaColor(slaDeadline: string): 'danger' | 'warning' | 'success' {
  const diff = new Date(slaDeadline).getTime() - Date.now()
  const hours = diff / 3_600_000
  if (hours < 0)  return 'danger'
  if (hours < 4)  return 'danger'
  if (hours < 12) return 'warning'
  return 'success'
}

export function hoursUntil(iso: string): number {
  return (new Date(iso).getTime() - Date.now()) / 3_600_000
}

export function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const min  = Math.floor(diff / 60_000)
  if (min < 1)  return 'Ahora'
  if (min < 60) return `Hace ${min}m`
  const hr = Math.floor(min / 60)
  if (hr  < 24) return `Hace ${hr}h`
  return `Hace ${Math.floor(hr / 24)}d`
}
