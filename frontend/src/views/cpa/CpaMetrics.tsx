import { useCpaMetrics } from '../../hooks/useCpa'
import { useSystemSummary, useConfidenceDistribution } from '../../hooks/useReports'
import { Card, CardHeader, CardSkeleton } from '../../components/ui/Card'
import { NetworkError } from '../../components/ui/Alert'

export function CpaMetrics() {
  const { data: metrics, isLoading: mLoad, error: mErr, refetch: mRefetch } = useCpaMetrics()
  const { data: summary }  = useSystemSummary()
  const { data: conf }     = useConfidenceDistribution()

  if (mLoad) return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <CardSkeleton rows={5} />
      <CardSkeleton rows={5} />
    </div>
  )

  if (mErr) return <NetworkError message="No se pudieron cargar las métricas" onRetry={mRefetch} />

  const slaColor =
    !metrics ? 'neutral' :
    metrics.sla_compliance_pct >= 95 ? 'success' :
    metrics.sla_compliance_pct >= 80 ? 'warning' : 'danger'

  return (
    <div className="space-y-4">
      {/* KPI cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <MetricCard
          label="Tiempo medio respuesta"
          value={metrics ? `${metrics.avg_response_time_hours.toFixed(1)}h` : '—'}
          icon="⏱"
          color={
            !metrics ? undefined :
            metrics.avg_response_time_hours < 4 ? 'var(--color-success)' :
            metrics.avg_response_time_hours < 8 ? 'var(--color-warning)' : 'var(--color-danger)'
          }
        />
        <MetricCard
          label="SLA cumplido"
          value={metrics ? `${metrics.sla_compliance_pct.toFixed(0)}%` : '—'}
          icon="✓"
          color={
            slaColor === 'success' ? 'var(--color-success)' :
            slaColor === 'warning' ? 'var(--color-warning)' : 'var(--color-danger)'
          }
        />
        <MetricCard
          label="Revisados"
          value={metrics ? String(metrics.total_reviewed) : '—'}
          icon="▤"
        />
        <MetricCard
          label="Pendientes"
          value={metrics ? String(metrics.pending_count) : '—'}
          icon="⏳"
          color={metrics?.pending_count && metrics.pending_count > 5 ? 'var(--color-warning)' : undefined}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Decision breakdown */}
        {metrics && (
          <Card>
            <CardHeader
              title="Decisiones del período"
              subtitle={`Últimos ${metrics.period_days} días`}
              icon={<span className="text-sm">◐</span>}
            />
            <div className="space-y-3">
              <DecisionBar
                label="Aprobados"
                count={metrics.approved_count}
                total={metrics.total_reviewed}
                color="var(--color-success)"
              />
              <DecisionBar
                label="Rechazados"
                count={metrics.rejected_count}
                total={metrics.total_reviewed}
                color="var(--color-danger)"
              />
              <DecisionBar
                label="Pendientes"
                count={metrics.pending_count}
                total={metrics.total_reviewed + metrics.pending_count}
                color="var(--color-warning)"
              />
            </div>

            {/* SLA gauge */}
            <div className="mt-4 pt-4 border-t border-[var(--color-border)]">
              <div className="flex items-center justify-between text-xs mb-2">
                <span className="text-[var(--color-text-3)]">Cumplimiento SLA</span>
                <span className={`font-mono font-bold ${
                  slaColor === 'success' ? 'text-[var(--color-success)]' :
                  slaColor === 'warning' ? 'text-[var(--color-warning)]' : 'text-[var(--color-danger)]'
                }`}>
                  {metrics.sla_compliance_pct.toFixed(1)}%
                </span>
              </div>
              <div className="h-2 rounded-full bg-[var(--color-surface-3)] overflow-hidden">
                <div
                  className="h-full rounded-full transition-all duration-700"
                  style={{
                    width: `${Math.min(100, metrics.sla_compliance_pct)}%`,
                    backgroundColor:
                      slaColor === 'success' ? 'var(--color-success)' :
                      slaColor === 'warning' ? 'var(--color-warning)' : 'var(--color-danger)',
                  }}
                />
              </div>
            </div>
          </Card>
        )}

        {/* System activity */}
        {summary && (
          <Card>
            <CardHeader
              title="Actividad del Sistema"
              subtitle="CENTINELA + Orquestador"
              icon={<span className="text-sm">⚡</span>}
            />
            <div className="space-y-2">
              <SysStat label="Documentos totales"  value={summary.total_documents_submitted} />
              <SysStat label="Procesados"          value={summary.total_documents_processed} />
              <SysStat label="Pausas activas"      value={summary.active_pauses}    highlight={summary.active_pauses > 0} />
              <SysStat label="Pausas resueltas"    value={summary.resolved_pauses} />
              <SysStat label="Decisiones orch."    value={summary.total_orchestrator_decisions} />
              <SysStat label="Requieren CPA"       value={summary.decisions_requiring_cpa_review} highlight={summary.decisions_requiring_cpa_review > 0} />
            </div>

            {/* Confidence summary */}
            {conf && (
              <div className="mt-4 pt-4 border-t border-[var(--color-border)]">
                <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-4)] mb-2">
                  Confianza promedio
                </div>
                <div className="flex items-end gap-1">
                  <span className="text-2xl font-mono font-bold text-[var(--color-text-2)]">
                    {(conf.average_confidence * 100).toFixed(1)}
                  </span>
                  <span className="text-sm text-[var(--color-text-4)] mb-0.5">%</span>
                  <span className="text-xs text-[var(--color-text-4)] mb-0.5 ml-1">
                    ({conf.total_documents_with_confidence} docs)
                  </span>
                </div>
              </div>
            )}
          </Card>
        )}
      </div>
    </div>
  )
}

function MetricCard({ label, value, icon, color }: {
  label: string; value: string; icon: string; color?: string
}) {
  return (
    <div className="card p-4 text-center">
      <div
        className="w-8 h-8 rounded-lg mx-auto mb-2 flex items-center justify-center text-sm"
        style={{ backgroundColor: color ? `${color}20` : 'var(--color-surface-3)', color: color ?? 'var(--color-text-4)' }}
      >
        {icon}
      </div>
      <div className="text-xl font-mono font-bold" style={{ color: color ?? 'var(--color-text)' }}>
        {value}
      </div>
      <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-4)] mt-0.5">{label}</div>
    </div>
  )
}

function DecisionBar({ label, count, total, color }: {
  label: string; count: number; total: number; color: string
}) {
  const pct = total > 0 ? (count / total) * 100 : 0
  return (
    <div className="flex items-center gap-3 text-xs">
      <span className="w-20 text-[var(--color-text-3)] flex-shrink-0">{label}</span>
      <div className="flex-1 h-2 rounded-full bg-[var(--color-surface-3)] overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
      <span className="font-mono w-10 text-right text-[var(--color-text-2)]">{count}</span>
    </div>
  )
}

function SysStat({ label, value, highlight = false }: { label: string; value: number; highlight?: boolean }) {
  return (
    <div className="flex items-center justify-between text-xs">
      <span className="text-[var(--color-text-3)]">{label}</span>
      <span className={`font-mono font-semibold ${highlight ? 'text-[var(--color-danger)]' : 'text-[var(--color-text-2)]'}`}>
        {value}
      </span>
    </div>
  )
}
