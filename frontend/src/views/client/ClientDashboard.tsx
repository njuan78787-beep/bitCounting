import { Header, SystemHealthStrip } from '../../components/layout/Header'
import { NucleusAndromeda } from '../../components/shared/NucleusAndromeda'
import { DocumentUpload } from './DocumentUpload'
import { TransactionList } from './TransactionList'
import { FinancialSummary } from './FinancialSummary'
import { FiscalAlerts } from './FiscalAlerts'
import { ActivityFeed } from '../../components/shared/WsStatusIndicator'
import { Card, CardHeader } from '../../components/ui/Card'
import { useTransactions } from '../../hooks/useTransactions'
import { useUser } from '../../store/authStore'
import { MoneyDisplay } from '../../components/ui/MoneyDisplay'
import { Badge } from '../../components/ui/Badge'

export function ClientDashboard() {
  const user = useUser()
  const { data } = useTransactions({
    client_id: user?.client_id ?? undefined,
    page_size: 3,
  })

  const stats = [
    {
      label:  'Transacciones',
      value:  String(data?.total ?? '—'),
      icon:   '↔',
      color:  'var(--color-accent)',
    },
    {
      label: 'Pausadas',
      value: String(data?.items.filter((t) => t.status === 'paused').length ?? '—'),
      icon:  '⏸',
      color: 'var(--color-warning)',
    },
    {
      label: 'Procesadas',
      value: String(data?.items.filter((t) => t.status === 'processed').length ?? '—'),
      icon:  '✓',
      color: 'var(--color-success)',
    },
  ]

  return (
    <div className="flex flex-col h-full">
      <SystemHealthStrip />
      <Header
        title={`Dashboard · ${user?.username}`}
        subtitle="Vista del cliente"
      />

      <div className="flex-1 p-4 sm:p-6 space-y-6 overflow-y-auto">
        {/* AI nucleus visual */}
        <NucleusAndromeda className="w-full h-64 sm:h-80 rounded-xl" />

        {/* KPI strip */}
        <div className="grid grid-cols-3 gap-3">
          {stats.map((s) => (
            <div key={s.label} className="card p-4 text-center">
              <div
                className="w-8 h-8 rounded-lg mx-auto mb-2 flex items-center justify-center text-sm"
                style={{ backgroundColor: `${s.color}15`, color: s.color }}
              >
                {s.icon}
              </div>
              <div className="text-xl font-mono font-bold text-[var(--color-text)]">{s.value}</div>
              <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-4)] mt-0.5">{s.label}</div>
            </div>
          ))}
        </div>

        {/* Main 2-column grid */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Left column — upload + alerts */}
          <div className="space-y-6">
            <DocumentUpload />
            <FiscalAlerts />
          </div>

          {/* Right column — financial summary */}
          <div className="lg:col-span-2">
            <FinancialSummary />
          </div>
        </div>

        {/* Recent transactions + activity feed */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2">
            <TransactionList />
          </div>
          <div>
            <Card>
              <CardHeader
                title="Actividad del Sistema"
                subtitle="Eventos en tiempo real"
                icon={<span className="text-sm">⚡</span>}
              />
              <ActivityFeed />
            </Card>

            {/* Latest transaction detail */}
            {data?.items[0] && (
              <div className="mt-4">
                <Card>
                  <CardHeader
                    title="Última Transacción"
                    icon={<span className="text-sm">↔</span>}
                  />
                  <div className="space-y-2 text-xs">
                    <div className="flex justify-between">
                      <span className="text-[var(--color-text-4)]">Proveedor</span>
                      <span className="font-medium text-[var(--color-text-2)]">
                        {data.items[0].vendor ?? 'Procesando…'}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-[var(--color-text-4)]">Monto</span>
                      <MoneyDisplay value={data.items[0].amount} size="sm" />
                    </div>
                    <div className="flex justify-between">
                      <span className="text-[var(--color-text-4)]">Estado</span>
                      <Badge
                        color={
                          data.items[0].status === 'processed' ? 'success' :
                          data.items[0].status === 'paused'    ? 'warning' :
                          data.items[0].status === 'rejected'  ? 'danger'  : 'accent'
                        }
                        dot
                      >
                        {data.items[0].status}
                      </Badge>
                    </div>
                  </div>
                </Card>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
