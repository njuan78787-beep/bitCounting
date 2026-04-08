import { Card, CardHeader } from '../../components/ui/Card'
import { Badge } from '../../components/ui/Badge'
import { formatDate } from '../../utils/dates'

interface FiscalDeadline {
  id:        string
  title:     string
  form:      string
  dueDate:   string   // ISO
  daysLeft:  number
  type:      'IVU' | 'INCOME' | 'SUTA' | 'MUNI' | 'OTHER'
}

function getDaysLeft(isoDate: string): number {
  return Math.ceil((new Date(isoDate).getTime() - Date.now()) / 86_400_000)
}

// Phase 1: static fiscal calendar for Puerto Rico
function buildDeadlines(): FiscalDeadline[] {
  const year  = new Date().getFullYear()
  const month = new Date().getMonth() + 1
  const nextM = month === 12 ? 1  : month + 1
  const nextY = month === 12 ? year + 1 : year

  const pad = (n: number) => String(n).padStart(2, '0')

  return [
    {
      id: 'ivu-monthly',
      title: 'IVU Mensual',
      form:  'SC 2915',
      dueDate: `${nextY}-${pad(nextM)}-20`,
      daysLeft: 0,
      type: 'IVU' as const,
    },
    {
      id: 'income-q',
      title: 'Estimado Trimestral',
      form:  'Form 480.20',
      dueDate: `${year}-04-15`,
      daysLeft: 0,
      type: 'INCOME' as const,
    },
    {
      id: 'suta',
      title: 'SUTA Trimestral',
      form:  'Form DTRH',
      dueDate: `${year}-04-30`,
      daysLeft: 0,
      type: 'SUTA' as const,
    },
    {
      id: 'muni-ivu',
      title: 'IVU Municipal',
      form:  'SC 2915-A',
      dueDate: `${nextY}-${pad(nextM)}-20`,
      daysLeft: 0,
      type: 'MUNI' as const,
    },
  ].map((d) => ({ ...d, daysLeft: getDaysLeft(d.dueDate) }))
    .sort((a, b) => a.daysLeft - b.daysLeft)
}

const typeColor: Record<FiscalDeadline['type'], string> = {
  IVU:    'bg-[var(--color-accent-2-dim)]  text-[var(--color-accent-2)]',
  INCOME: 'bg-[var(--color-info-dim)]      text-[var(--color-info)]',
  SUTA:   'bg-[var(--color-warning-dim)]   text-[var(--color-warning)]',
  MUNI:   'bg-[var(--color-success-dim)]   text-[var(--color-success)]',
  OTHER:  'bg-[var(--color-surface-3)]     text-[var(--color-text-4)]',
}

export function FiscalAlerts() {
  const deadlines = buildDeadlines()

  return (
    <Card>
      <CardHeader
        title="Fechas Límite Fiscales"
        subtitle="Calendario fiscal PR · Actualizado automáticamente"
        icon={<span className="text-sm">⏰</span>}
      />

      <div className="space-y-2">
        {deadlines.map((dl) => {
          const urgent  = dl.daysLeft <= 5
          const warning = dl.daysLeft <= 14 && dl.daysLeft > 5
          const past    = dl.daysLeft < 0

          return (
            <div
              key={dl.id}
              className={`
                flex items-center gap-3 px-3 py-2.5 rounded-lg border
                ${past    ? 'border-red-200    bg-[var(--color-danger-dim)]' :
                  urgent  ? 'border-red-200    bg-[var(--color-danger-dim)]' :
                  warning ? 'border-amber-200  bg-[var(--color-warning-dim)]' :
                            'border-[var(--color-border)] bg-[var(--color-surface-2)]'}
              `}
            >
              {/* Type chip */}
              <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded flex-shrink-0 ${typeColor[dl.type]}`}>
                {dl.type}
              </span>

              {/* Info */}
              <div className="flex-1 min-w-0">
                <div className="text-xs font-medium text-[var(--color-text-2)] truncate">{dl.title}</div>
                <div className="text-[10px] text-[var(--color-text-4)]">
                  Formulario {dl.form} · {formatDate(dl.dueDate)}
                </div>
              </div>

              {/* Days left */}
              <div className="flex-shrink-0 text-right">
                {past ? (
                  <Badge color="danger">Vencido</Badge>
                ) : (
                  <div className={`text-sm font-bold font-mono ${
                    urgent ? 'text-[var(--color-danger)]' :
                    warning ? 'text-[var(--color-warning)]' :
                    'text-[var(--color-text-2)]'
                  }`}>
                    {dl.daysLeft}d
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </Card>
  )
}
