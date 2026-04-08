import { useState } from 'react'
import { useTransactions } from '../../hooks/useTransactions'
import { useUser } from '../../store/authStore'
import { Card, CardHeader, CardSkeleton } from '../../components/ui/Card'
import { StatusBadge } from '../../components/ui/Badge'
import { MoneyDisplay } from '../../components/ui/MoneyDisplay'
import { NetworkError } from '../../components/ui/Alert'
import { Button } from '../../components/ui/Button'
import { formatDate, relativeTime } from '../../utils/dates'
import type { TransactionStatus } from '../../types/api'

const STATUS_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '',          label: 'Todos'     },
  { value: 'pending',   label: 'Pendientes' },
  { value: 'processed', label: 'Procesados' },
  { value: 'paused',    label: 'Pausados'   },
  { value: 'rejected',  label: 'Rechazados' },
]

export function TransactionList() {
  const user = useUser()

  const [status,   setStatus]   = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo,   setDateTo]   = useState('')
  const [page,     setPage]     = useState(1)
  const [search,   setSearch]   = useState('')

  const { data, isLoading, error, refetch } = useTransactions({
    client_id:  user?.client_id ?? undefined,
    status:     status || undefined,
    date_from:  dateFrom || undefined,
    date_to:    dateTo   || undefined,
    page,
    page_size:  20,
  })

  const items = data?.items ?? []
  const filtered = search
    ? items.filter((t) =>
        t.vendor?.toLowerCase().includes(search.toLowerCase()) ||
        t.account_name?.toLowerCase().includes(search.toLowerCase()) ||
        t.transaction_id.includes(search)
      )
    : items

  return (
    <div className="space-y-4">
      {/* Filters */}
      <Card padding="sm">
        <div className="flex flex-wrap gap-2 items-center">
          {/* Search */}
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Buscar proveedor, cuenta, ID…"
            className="
              flex-1 min-w-[180px] px-3 py-1.5 text-xs rounded-lg
              border border-[var(--color-border)] bg-[var(--color-surface-2)]
              text-[var(--color-text)] placeholder:text-[var(--color-text-4)]
              focus:outline-none focus:border-[var(--color-accent)]
            "
          />
          {/* Status filter */}
          <select
            value={status}
            onChange={(e) => { setStatus(e.target.value); setPage(1) }}
            className="
              px-3 py-1.5 text-xs rounded-lg
              border border-[var(--color-border)] bg-[var(--color-surface-2)]
              text-[var(--color-text-2)]
              focus:outline-none focus:border-[var(--color-accent)]
            "
          >
            {STATUS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          {/* Date range */}
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => { setDateFrom(e.target.value); setPage(1) }}
            className="px-2 py-1.5 text-xs rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] text-[var(--color-text-2)] focus:outline-none focus:border-[var(--color-accent)]"
          />
          <span className="text-[var(--color-text-4)] text-xs">—</span>
          <input
            type="date"
            value={dateTo}
            onChange={(e) => { setDateTo(e.target.value); setPage(1) }}
            className="px-2 py-1.5 text-xs rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] text-[var(--color-text-2)] focus:outline-none focus:border-[var(--color-accent)]"
          />
        </div>
      </Card>

      {/* Table card */}
      <Card padding="none">
        <CardHeader
          title={`Transacciones ${data ? `(${data.total})` : ''}`}
          subtitle="Historial completo · Actualizado en tiempo real"
          icon={<span className="text-sm">↔</span>}
          actions={
            <Button variant="ghost" size="sm" onClick={() => refetch()}>↻</Button>
          }
        />

        {isLoading ? (
          <div className="px-4 pb-4 space-y-2">
            {[...Array(5)].map((_, i) => <CardSkeleton key={i} rows={1} />)}
          </div>
        ) : error ? (
          <div className="px-4 pb-4">
            <NetworkError message="No se pudieron cargar las transacciones" onRetry={() => refetch()} />
          </div>
        ) : filtered.length === 0 ? (
          <div className="px-4 pb-6 text-center">
            <div className="text-2xl mb-2">📭</div>
            <p className="text-sm text-[var(--color-text-4)]">No hay transacciones con esos filtros</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-[var(--color-border)] bg-[var(--color-surface-2)]">
                  {['Fecha', 'Proveedor', 'Cuenta', 'Monto', 'Conf.', 'Estado', ''].map((h) => (
                    <th key={h} className="px-4 py-2.5 text-left font-medium text-[var(--color-text-4)] uppercase tracking-wider text-[10px]">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)]">
                {filtered.map((t) => (
                  <tr key={t.transaction_id} className="hover:bg-[var(--color-surface-2)] transition-colors">
                    <td className="px-4 py-3 font-mono text-[var(--color-text-4)]">
                      {t.date ? formatDate(t.date) : relativeTime(t.created_at)}
                    </td>
                    <td className="px-4 py-3 max-w-[160px]">
                      <div className="font-medium text-[var(--color-text-2)] truncate">
                        {t.vendor ?? <span className="text-[var(--color-text-4)] italic">Procesando…</span>}
                      </div>
                      <div className="text-[10px] text-[var(--color-text-4)] font-mono truncate">
                        {t.transaction_id.slice(0, 12)}…
                      </div>
                    </td>
                    <td className="px-4 py-3 text-[var(--color-text-3)]">
                      {t.account_code ? `${t.account_code} · ${t.account_name ?? ''}` : '—'}
                    </td>
                    <td className="px-4 py-3">
                      <MoneyDisplay value={t.amount} size="sm" />
                    </td>
                    <td className="px-4 py-3">
                      {t.confidence ? (
                        <ConfidenceBar value={parseFloat(t.confidence)} />
                      ) : (
                        <span className="text-[var(--color-text-4)]">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <StatusBadge status={t.status as TransactionStatus} />
                    </td>
                    <td className="px-4 py-3">
                      <Button variant="ghost" size="sm">Ver →</Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination */}
        {data && data.total > 20 && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-[var(--color-border)]">
            <span className="text-xs text-[var(--color-text-4)]">
              Página {page} de {Math.ceil(data.total / 20)}
            </span>
            <div className="flex gap-2">
              <Button variant="secondary" size="sm" disabled={page === 1} onClick={() => setPage(p => p - 1)}>
                ← Anterior
              </Button>
              <Button variant="secondary" size="sm" disabled={page * 20 >= data.total} onClick={() => setPage(p => p + 1)}>
                Siguiente →
              </Button>
            </div>
          </div>
        )}
      </Card>
    </div>
  )
}

function ConfidenceBar({ value }: { value: number }) {
  const pct   = Math.round(value * 100)
  const color = value >= 0.9 ? 'var(--color-success)' :
                value >= 0.7 ? 'var(--color-warning)' :
                               'var(--color-danger)'
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-12 h-1.5 rounded-full bg-[var(--color-surface-3)] overflow-hidden">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
      <span className="font-mono text-[10px] text-[var(--color-text-4)]">{pct}%</span>
    </div>
  )
}
