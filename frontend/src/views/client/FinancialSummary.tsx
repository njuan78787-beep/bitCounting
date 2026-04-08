import { useState } from 'react'
import { useBalanceSheet, useIncomeStatement, useIvuSummary, useSystemSummary, useConfidenceDistribution } from '../../hooks/useReports'
import { useUser } from '../../store/authStore'
import { Card, CardHeader, CardSkeleton } from '../../components/ui/Card'
import { MoneyDisplay } from '../../components/ui/MoneyDisplay'
import { NetworkError } from '../../components/ui/Alert'
import { Badge } from '../../components/ui/Badge'
import { periodStart, periodEnd, currentYearMonth, formatDate } from '../../utils/dates'

export function FinancialSummary() {
  const user = useUser()
  const cid  = user?.client_id ?? ''

  const [activeTab, setActiveTab] = useState<'balance' | 'income' | 'ivu' | 'system'>('balance')

  const tabs = [
    { id: 'balance' as const, label: 'Balance',   icon: '▤' },
    { id: 'income'  as const, label: 'Resultado',  icon: '↗' },
    { id: 'ivu'     as const, label: 'IVU',        icon: '§' },
    { id: 'system'  as const, label: 'Sistema',    icon: '◐' },
  ]

  return (
    <Card>
      <CardHeader
        title="Estados Financieros"
        subtitle={`Actualizado: ${formatDate(new Date().toISOString())}`}
        icon={<span className="text-sm">▤</span>}
      />

      {/* Tab bar */}
      <div className="flex gap-1 mb-4 p-1 bg-[var(--color-surface-2)] rounded-lg">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setActiveTab(t.id)}
            className={`
              flex-1 flex items-center justify-center gap-1.5 px-2 py-1.5
              text-xs font-medium rounded-md transition-all cursor-pointer
              ${activeTab === t.id
                ? 'bg-[var(--color-surface)] text-[var(--color-accent)] shadow-sm'
                : 'text-[var(--color-text-4)] hover:text-[var(--color-text-2)]'}
            `}
          >
            <span>{t.icon}</span>
            <span className="hidden sm:inline">{t.label}</span>
          </button>
        ))}
      </div>

      {activeTab === 'balance'  && <BalanceSheetPanel  clientId={cid} />}
      {activeTab === 'income'   && <IncomeStatPanel    clientId={cid} />}
      {activeTab === 'ivu'      && <IvuPanel           clientId={cid} />}
      {activeTab === 'system'   && <SystemPanel />}
    </Card>
  )
}

// ── Balance Sheet ─────────────────────────────────────────────────────────────

function BalanceSheetPanel({ clientId }: { clientId: string }) {
  const { data, isLoading, error, refetch } = useBalanceSheet(clientId)

  if (isLoading) return <CardSkeleton rows={6} />
  if (error)     return <NetworkError message="No se pudo cargar el balance" onRetry={refetch} />
  if (!data)     return null

  return (
    <div className="space-y-4 animate-fade-in">
      {/* Equation bar */}
      <div className="flex items-center gap-2 text-xs text-[var(--color-text-4)] font-mono overflow-x-auto pb-1">
        <div className="flex-1 text-center">
          <div className="text-[10px] uppercase tracking-wider mb-0.5">Activos</div>
          <MoneyDisplay value={data.total_assets} size="lg" />
        </div>
        <span className="text-[var(--color-text-4)] text-base">=</span>
        <div className="flex-1 text-center">
          <div className="text-[10px] uppercase tracking-wider mb-0.5">Pasivos</div>
          <MoneyDisplay value={data.total_liabilities} size="lg" />
        </div>
        <span className="text-[var(--color-text-4)] text-base">+</span>
        <div className="flex-1 text-center">
          <div className="text-[10px] uppercase tracking-wider mb-0.5">Capital</div>
          <MoneyDisplay value={data.total_equity} size="lg" color="success" />
        </div>
      </div>

      {/* Details grid */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <FinGroup title="Activos Corrientes" items={[
          ['Efectivo',           data.assets.current_assets.cash_and_equivalents],
          ['Cuentas x Cobrar',   data.assets.current_assets.accounts_receivable],
          ['Inventario',         data.assets.current_assets.inventory],
          ['Gastos Prepagados',  data.assets.current_assets.prepaid_expenses],
        ]} total={data.assets.current_assets.total_current_assets} />
        <FinGroup title="Pasivos Corrientes" items={[
          ['Cuentas x Pagar',   data.liabilities.current_liabilities.accounts_payable],
          ['Gastos Acumulados',  data.liabilities.current_liabilities.accrued_expenses],
          ['IVU por Pagar',      data.liabilities.current_liabilities.ivu_payable],
        ]} total={data.liabilities.current_liabilities.total_current_liabilities} />
        <FinGroup title="Capital" items={[
          ['Acciones Comunes',   data.equity.common_stock],
          ['Ganancias Retenidas', data.equity.retained_earnings],
        ]} total={data.equity.total_equity} />
      </div>
    </div>
  )
}

// ── Income Statement ──────────────────────────────────────────────────────────

function IncomeStatPanel({ clientId }: { clientId: string }) {
  const { data, isLoading, error, refetch } = useIncomeStatement(
    clientId, periodStart(1), periodEnd(0)
  )

  if (isLoading) return <CardSkeleton rows={5} />
  if (error)     return <NetworkError message="No se pudo cargar el estado de resultados" onRetry={refetch} />
  if (!data)     return null

  const netPos = parseFloat(data.net_income) >= 0

  return (
    <div className="space-y-3 animate-fade-in">
      {/* KPIs */}
      <div className="grid grid-cols-3 gap-3">
        <KpiBox label="Ingresos"      value={data.revenue['total_revenue'] ?? '0'}     color="text-[var(--color-accent-2)]" />
        <KpiBox label="Utilidad Bruta" value={data.gross_profit}                        color="text-[var(--color-text-2)]" />
        <KpiBox label="Ingreso Neto"  value={data.net_income}                           color={netPos ? 'text-[var(--color-success)]' : 'text-[var(--color-danger)]'} />
      </div>

      <FinGroup title="Ingresos" items={[
        ['Servicios',   data.revenue['service_revenue']  ?? '0'],
        ['Productos',   data.revenue['product_sales']    ?? '0'],
        ['Otros',       data.revenue['other_revenue']    ?? '0'],
      ]} total={data.revenue['total_revenue'] ?? '0'} />

      <FinGroup title="Gastos Operativos" items={[
        ['Nómina',      data.operating_expenses['salaries_and_wages'] ?? '0'],
        ['Renta',       data.operating_expenses['rent']               ?? '0'],
        ['Honorarios',  data.operating_expenses['professional_fees']  ?? '0'],
        ['Depreciación',data.operating_expenses['depreciation']       ?? '0'],
      ]} total={data.operating_expenses['total_operating_expenses'] ?? '0'} />
    </div>
  )
}

// ── IVU Panel ─────────────────────────────────────────────────────────────────

function IvuPanel({ clientId }: { clientId: string }) {
  const period = currentYearMonth()
  const { data, isLoading, error, refetch } = useIvuSummary(clientId, period)

  if (isLoading) return <CardSkeleton rows={4} />
  if (error)     return <NetworkError message="No se pudo cargar el resumen IVU" onRetry={refetch} />
  if (!data)     return null

  const balNum = parseFloat(data.ivu_balance)

  return (
    <div className="space-y-4 animate-fade-in">
      <div className="flex items-start justify-between">
        <div>
          <div className="text-xs text-[var(--color-text-4)]">Período {data.period}</div>
          <MoneyDisplay value={data.ivu_collected} size="xl" />
          <div className="text-xs text-[var(--color-text-4)] mt-0.5">IVU cobrado</div>
        </div>
        {data.form_sc2915_ready ? (
          <Badge color="success" dot>SC 2915 Listo</Badge>
        ) : (
          <Badge color="warning" dot>Pendiente</Badge>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <KpiBox label="IVU Cobrado"   value={data.ivu_collected} />
        <KpiBox label="IVU Remitido"  value={data.ivu_remitted} />
        <KpiBox label="Balance"       value={data.ivu_balance}
          color={balNum === 0 ? 'text-[var(--color-success)]' : 'text-[var(--color-danger)]'} />
        <KpiBox label="Transacciones" value={String(data.transactions_count)} />
      </div>

      <div className="text-xs text-[var(--color-text-4)] bg-[var(--color-surface-2)] rounded-lg p-3">
        Tasa estatal: {(parseFloat(data.ivu_rate_state) * 100).toFixed(1)}% ·
        Tasa municipal: {(parseFloat(data.ivu_rate_municipal) * 100).toFixed(1)}%
      </div>
    </div>
  )
}

// ── System Activity Panel (makes invisible work visible) ──────────────────────

function SystemPanel() {
  const { data: summary } = useSystemSummary()
  const { data: conf    } = useConfidenceDistribution()

  if (!summary) return <CardSkeleton rows={5} />

  return (
    <div className="space-y-4 animate-fade-in">
      {/* This week activity — never just green */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <KpiBox label="Docs procesados"    value={String(summary.total_documents_processed)} />
        <KpiBox label="Pausas activas"     value={String(summary.active_pauses)}
          color={summary.active_pauses > 0 ? 'text-[var(--color-warning)]' : undefined} />
        <KpiBox label="Decisiones"         value={String(summary.total_orchestrator_decisions)} />
        <KpiBox label="Revisión CPA"       value={String(summary.decisions_requiring_cpa_review)}
          color={summary.decisions_requiring_cpa_review > 0 ? 'text-[var(--color-danger)]' : undefined} />
      </div>

      {/* CENTINELA activity */}
      <div className="bg-[var(--color-surface-2)] rounded-lg p-3 space-y-1.5">
        <div className="text-[10px] font-semibold uppercase tracking-wider text-[var(--color-text-4)] mb-2">
          CENTINELA · Actividad esta semana
        </div>
        <ActivityRow icon="🔍" label="Verificaciones ejecutadas"     value={summary.total_orchestrator_decisions} />
        <ActivityRow icon="⏸"  label="Pausas generadas"             value={summary.total_pauses} />
        <ActivityRow icon="✓"  label="Pausas resueltas"             value={summary.resolved_pauses} />
        <ActivityRow icon="📋" label="Requieren revisión CPA"       value={summary.decisions_requiring_cpa_review} urgent={summary.decisions_requiring_cpa_review > 0} />
      </div>

      {/* Confidence distribution */}
      {conf && (
        <div className="space-y-1.5">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-[var(--color-text-4)]">
            Distribución de confianza · {conf.total_documents_with_confidence} docs
          </div>
          {(Object.entries(conf.distribution) as Array<[string, number]>).map(([k, v]) => (
            <ConfidenceRow key={k} label={k} count={v} total={conf.total_documents_with_confidence} />
          ))}
        </div>
      )}
    </div>
  )
}

// ── Shared sub-components ─────────────────────────────────────────────────────

function FinGroup({ title, items, total }: {
  title: string
  items: Array<[string, string]>
  total: string
}) {
  return (
    <div className="bg-[var(--color-surface-2)] rounded-lg p-3">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-[var(--color-text-4)] mb-2">
        {title}
      </div>
      <div className="space-y-1">
        {items.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between text-xs">
            <span className="text-[var(--color-text-3)] truncate mr-2">{label}</span>
            <MoneyDisplay value={value} size="sm" color="muted" />
          </div>
        ))}
        <div className="flex items-center justify-between text-xs pt-1.5 border-t border-[var(--color-border)] mt-1.5">
          <span className="font-semibold text-[var(--color-text-2)]">Total</span>
          <MoneyDisplay value={total} size="sm" />
        </div>
      </div>
    </div>
  )
}

function KpiBox({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-[var(--color-surface-2)] rounded-lg p-3 text-center">
      <div className={`text-base font-mono font-bold ${color ?? 'text-[var(--color-text-2)]'}`}>
        {value.startsWith('-') || parseFloat(value) > 100
          ? <MoneyDisplay value={value} size="md" />
          : value}
      </div>
      <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-4)] mt-0.5">{label}</div>
    </div>
  )
}

function ActivityRow({ icon, label, value, urgent = false }: {
  icon: string; label: string; value: number; urgent?: boolean
}) {
  return (
    <div className="flex items-center justify-between text-xs">
      <div className="flex items-center gap-2">
        <span>{icon}</span>
        <span className="text-[var(--color-text-3)]">{label}</span>
      </div>
      <span className={`font-mono font-semibold ${urgent ? 'text-[var(--color-danger)]' : 'text-[var(--color-text-2)]'}`}>
        {value}
      </span>
    </div>
  )
}

function ConfidenceRow({ label, count, total }: { label: string; count: number; total: number }) {
  const pct   = total > 0 ? (count / total) * 100 : 0
  const color = label === 'very_high' ? 'var(--color-success)' :
                label === 'high'      ? 'var(--color-accent-2)' :
                label === 'medium'    ? 'var(--color-warning)' :
                                       'var(--color-danger)'
  const labels: Record<string, string> = {
    very_high: 'Muy alta ≥90%',
    high:      'Alta 80-90%',
    medium:    'Media 70-80%',
    low:       'Baja 60-70%',
    very_low:  'Muy baja <60%',
  }
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-24 text-[var(--color-text-4)] flex-shrink-0 text-right">{labels[label]}</span>
      <div className="flex-1 h-1.5 rounded-full bg-[var(--color-surface-3)] overflow-hidden">
        <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
      <span className="font-mono w-6 text-[var(--color-text-4)] text-right">{count}</span>
    </div>
  )
}
