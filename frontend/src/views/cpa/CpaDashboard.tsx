import { Header, SystemHealthStrip } from '../../components/layout/Header'
import { ReviewQueue } from './ReviewQueue'
import { PauseListPanel } from './PauseDetail'
import { InstructionModule } from './InstructionModule'
import { CpaMetrics } from './CpaMetrics'
import { Card, CardHeader } from '../../components/ui/Card'
import { ActivityFeed } from '../../components/shared/WsStatusIndicator'
import { useReviewQueue } from '../../hooks/useCpa'
import { usePauses } from '../../hooks/usePauses'
import { useUser } from '../../store/authStore'

export function CpaDashboard() {
  const user = useUser()
  const { data: queue  } = useReviewQueue(1, 100)
  const { data: pauses } = usePauses({ status: 'ACTIVE' })

  const pendingCount  = queue?.items.filter(i => i.status === 'PENDING').length ?? 0
  const criticalCount = pauses?.items.filter(p => p.severity === 'CRITICAL').length ?? 0
  const highCount     = pauses?.items.filter(p => p.severity === 'HIGH').length ?? 0

  const stats = [
    {
      label: 'En cola',
      value: String(queue?.total ?? '—'),
      icon:  '▤',
      color: 'var(--color-accent)',
      urgent: pendingCount > 0,
    },
    {
      label: 'Críticas',
      value: String(criticalCount),
      icon:  '⚠',
      color: criticalCount > 0 ? 'var(--color-danger)' : 'var(--color-success)',
      urgent: criticalCount > 0,
    },
    {
      label: 'Alta severidad',
      value: String(highCount),
      icon:  '↑',
      color: highCount > 0 ? 'var(--color-warning)' : 'var(--color-success)',
      urgent: false,
    },
    {
      label: 'Pausas activas',
      value: String(pauses?.total ?? '—'),
      icon:  '⏸',
      color: (pauses?.total ?? 0) > 0 ? 'var(--color-warning)' : 'var(--color-success)',
      urgent: false,
    },
  ]

  return (
    <div className="flex flex-col h-full">
      <SystemHealthStrip />
      <Header
        title={`Dashboard CPA · ${user?.username}`}
        subtitle={`${user?.role} · Vista profesional`}
      />

      <div className="flex-1 p-4 sm:p-6 space-y-6 overflow-y-auto">

        {/* KPI strip */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {stats.map((s) => (
            <div
              key={s.label}
              className={`card p-4 text-center relative overflow-hidden
                ${s.urgent ? 'ring-1 ring-[var(--color-danger)]/30' : ''}`}
            >
              {s.urgent && (
                <div className="absolute top-2 right-2">
                  <span className="w-2 h-2 rounded-full bg-[var(--color-danger)] animate-pulse-dot block" />
                </div>
              )}
              <div
                className="w-8 h-8 rounded-lg mx-auto mb-2 flex items-center justify-center text-sm"
                style={{ backgroundColor: `${s.color}18`, color: s.color }}
              >
                {s.icon}
              </div>
              <div className="text-xl font-mono font-bold" style={{ color: s.color }}>
                {s.value}
              </div>
              <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-4)] mt-0.5">
                {s.label}
              </div>
            </div>
          ))}
        </div>

        {/* Main grid: queue + activity */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2">
            <ReviewQueue />
          </div>

          <div className="space-y-4">
            <Card>
              <CardHeader
                title="Actividad en tiempo real"
                icon={<span className="text-sm">⚡</span>}
              />
              <ActivityFeed />
            </Card>

            {/* SLA compliance snapshot */}
            {pendingCount > 0 && (
              <div className="card p-4 border-[var(--color-warning)] bg-[var(--color-warning-dim)]">
                <div className="flex items-center gap-2 mb-2">
                  <span className="w-2 h-2 rounded-full bg-[var(--color-warning)] animate-pulse-dot" />
                  <span className="text-xs font-semibold text-[var(--color-warning)]">
                    {pendingCount} items pendientes de revisión
                  </span>
                </div>
                <p className="text-xs text-[var(--color-text-3)]">
                  Revisa la cola antes de que venzan los SLAs.
                  Los items CRITICAL vencen en menos de 4 horas.
                </p>
              </div>
            )}

            {/* Severity breakdown */}
            {pauses && pauses.total > 0 && (
              <Card>
                <CardHeader title="Distribución de pausas" icon={<span className="text-sm">⏸</span>} />
                <div className="space-y-1.5">
                  {(['CRITICAL','HIGH','MEDIUM','LOW'] as const).map((sev) => {
                    const count = pauses.items.filter(p => p.severity === sev).length
                    const color =
                      sev === 'CRITICAL' ? 'var(--color-danger)' :
                      sev === 'HIGH'     ? 'var(--color-warning)' :
                      sev === 'MEDIUM'   ? 'var(--color-accent-2)' : 'var(--color-success)'
                    return (
                      <div key={sev} className="flex items-center gap-2 text-xs">
                        <span className="w-16 text-[var(--color-text-4)]">{sev}</span>
                        <div className="flex-1 h-2 rounded-full bg-[var(--color-surface-3)] overflow-hidden">
                          <div
                            className="h-full rounded-full"
                            style={{
                              width: `${pauses.total > 0 ? (count / pauses.total) * 100 : 0}%`,
                              backgroundColor: color,
                            }}
                          />
                        </div>
                        <span className="font-mono w-4 text-right" style={{ color }}>{count}</span>
                      </div>
                    )
                  })}
                </div>
              </Card>
            )}
          </div>
        </div>

        {/* Pauses + Instructions row */}
        <div className="grid grid-cols-1 gap-6">
          <PauseListPanel />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <InstructionModule />
          <div className="space-y-4">
            <CpaMetrics />
          </div>
        </div>
      </div>
    </div>
  )
}
