import { useState } from 'react'
import { usePauses, usePause, useReleasePause } from '../../hooks/usePauses'
import { Card, CardHeader, CardSkeleton } from '../../components/ui/Card'
import { SeverityBadge, Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { NetworkError, Alert } from '../../components/ui/Alert'
import { Modal } from '../../components/ui/Modal'
import { formatDateTime, hoursUntil, slaColor } from '../../utils/dates'
import { ApiRequestError } from '../../api/client'
import type { PauseResponse } from '../../types/api'

export function PauseListPanel() {
  const { data, isLoading, error, refetch } = usePauses({ status: 'ACTIVE' })
  const [selected, setSelected] = useState<string | null>(null)

  const items = data?.items ?? []

  return (
    <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
      {/* List */}
      <Card padding="none" className="lg:col-span-2">
        <CardHeader
          title={`Pausas CENTINELA ${data ? `(${data.total})` : ''}`}
          subtitle="Activas · Ordenadas por urgencia"
          icon={<span className="text-sm">⏸</span>}
          actions={<Button variant="ghost" size="sm" onClick={() => refetch()}>↻</Button>}
        />

        {isLoading ? (
          <div className="px-4 pb-4 space-y-2">
            {[...Array(4)].map((_, i) => <CardSkeleton key={i} rows={1} />)}
          </div>
        ) : error ? (
          <div className="px-4 pb-4">
            <NetworkError message="Error al cargar pausas" onRetry={refetch} />
          </div>
        ) : items.length === 0 ? (
          <div className="px-4 pb-6 text-center">
            <div className="text-2xl mb-2">✓</div>
            <p className="text-xs text-[var(--color-success)]">Sin pausas activas</p>
          </div>
        ) : (
          <div className="divide-y divide-[var(--color-border)]">
            {items.map((p) => (
              <PauseListItem
                key={p.pause_id}
                pause={p}
                selected={selected === p.pause_id}
                onSelect={() => setSelected(p.pause_id)}
              />
            ))}
          </div>
        )}
      </Card>

      {/* Detail panel */}
      <Card className="lg:col-span-3">
        {selected ? (
          <PauseDetailPanel pauseId={selected} onResolved={() => { setSelected(null); refetch() }} />
        ) : (
          <div className="flex flex-col items-center justify-center h-full min-h-[200px] text-center">
            <div className="text-3xl mb-3 opacity-40">⏸</div>
            <p className="text-sm text-[var(--color-text-4)]">
              Selecciona una pausa para ver el análisis
            </p>
          </div>
        )}
      </Card>
    </div>
  )
}

function PauseListItem({
  pause, selected, onSelect,
}: { pause: PauseResponse; selected: boolean; onSelect: () => void }) {
  const hoursLeft = hoursUntil(pause.sla_deadline)
  const slaCls    = slaColor(pause.sla_deadline)

  return (
    <button
      onClick={onSelect}
      className={`
        w-full text-left px-4 py-3 transition-colors
        ${selected
          ? 'bg-[var(--color-accent-dim)] border-l-2 border-[var(--color-accent)]'
          : 'hover:bg-[var(--color-surface-2)]'}
      `}
    >
      <div className="flex items-center gap-2 mb-1">
        <SeverityBadge severity={pause.severity} />
        <span className={`ml-auto text-xs font-mono font-bold ${
          slaCls === 'danger'  ? 'text-[var(--color-danger)]' :
          slaCls === 'warning' ? 'text-[var(--color-warning)]' :
                                  'text-[var(--color-success)]'
        }`}>
          {hoursLeft < 0 ? 'VENCIDO' : `${Math.round(hoursLeft)}h`}
        </span>
      </div>
      <p className="text-xs text-[var(--color-text-2)] line-clamp-2">{pause.reason}</p>
      <div className="text-[10px] text-[var(--color-text-4)] font-mono mt-1">
        {pause.transaction_id.slice(0, 14)}…
      </div>
    </button>
  )
}

function PauseDetailPanel({ pauseId, onResolved }: { pauseId: string; onResolved: () => void }) {
  const { data: pause, isLoading, error } = usePause(pauseId)
  const release = useReleasePause()

  const [showModal,    setShowModal]    = useState(false)
  const [note,         setNote]         = useState('')
  const [releaseError, setReleaseError] = useState<string | null>(null)

  if (isLoading) return <CardSkeleton rows={8} />
  if (error)     return <NetworkError message="No se pudo cargar la pausa" />
  if (!pause)    return null

  async function handleRelease() {
    if (!note.trim()) return
    setReleaseError(null)
    try {
      await release.mutateAsync({ id: pauseId, resolution_note: note })
      setShowModal(false)
      onResolved()
    } catch (err) {
      setReleaseError(err instanceof ApiRequestError ? err.detail : 'Error al liberar')
    }
  }

  const hoursLeft = hoursUntil(pause.sla_deadline)
  const slaCls    = slaColor(pause.sla_deadline)

  return (
    <div className="space-y-4 animate-fade-in">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <SeverityBadge severity={pause.severity} />
            <Badge color={pause.status === 'ACTIVE' ? 'warning' : 'success'} dot>
              {pause.status}
            </Badge>
          </div>
          <p className="text-sm font-medium text-[var(--color-text-2)]">{pause.reason}</p>
        </div>
        {pause.status === 'ACTIVE' && (
          <Button
            variant="primary"
            size="sm"
            onClick={() => setShowModal(true)}
          >
            Liberar pausa
          </Button>
        )}
      </div>

      {/* SLA */}
      <div className={`
        flex items-center justify-between px-3 py-2 rounded-lg text-xs
        ${slaCls === 'danger'  ? 'bg-[var(--color-danger-dim)]  text-[var(--color-danger)]' :
          slaCls === 'warning' ? 'bg-[var(--color-warning-dim)] text-[var(--color-warning)]' :
                                  'bg-[var(--color-success-dim)] text-[var(--color-success)]'}
      `}>
        <span>SLA: {formatDateTime(pause.sla_deadline)}</span>
        <span className="font-mono font-bold">
          {hoursLeft < 0 ? `Vencido hace ${Math.abs(Math.round(hoursLeft))}h` : `${Math.round(hoursLeft)}h restantes`}
        </span>
      </div>

      {/* Info grid */}
      <div className="grid grid-cols-2 gap-3 text-xs">
        <InfoRow label="ID Pausa"     value={pause.pause_id.slice(0, 16) + '…'} mono />
        <InfoRow label="Transacción"  value={pause.transaction_id.slice(0, 16) + '…'} mono />
        <InfoRow label="Confianza"    value={`${Math.round(pause.confidence * 100)}%`} mono />
        <InfoRow label="SLA (horas)"  value={`${pause.sla_hours}h`} />
        <InfoRow label="Creada"       value={formatDateTime(pause.created_at)} />
        {pause.resolved_at && <InfoRow label="Resuelta" value={formatDateTime(pause.resolved_at)} />}
      </div>

      {/* Agent analysis */}
      {pause.agent_analysis && (
        <div className="bg-[var(--color-accent-dim)] rounded-lg p-3">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-[var(--color-accent)] mb-2">
            Análisis pre-procesado del CENTINELA
          </div>
          <p className="text-xs text-[var(--color-text-2)] leading-relaxed">
            {pause.agent_analysis}
          </p>
        </div>
      )}

      {/* Resolution note */}
      {pause.resolution_note && (
        <div className="bg-[var(--color-success-dim)] rounded-lg p-3">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-[var(--color-success)] mb-1">
            Nota de resolución
          </div>
          <p className="text-xs text-[var(--color-text-2)]">{pause.resolution_note}</p>
        </div>
      )}

      {/* Release modal */}
      <Modal
        open={showModal}
        onClose={() => setShowModal(false)}
        title="Liberar pausa CENTINELA"
        actions={
          <>
            <Button variant="secondary" onClick={() => setShowModal(false)}>Cancelar</Button>
            <Button
              variant="primary"
              loading={release.isPending}
              disabled={!note.trim()}
              onClick={handleRelease}
            >
              Confirmar liberación
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Alert type="warning">
            Al liberar esta pausa, la transacción continuará el flujo automático.
            Asegúrate de haber revisado todos los detalles.
          </Alert>
          <div>
            <label className="block text-xs font-medium text-[var(--color-text-2)] mb-1.5">
              Nota de resolución <span className="text-[var(--color-danger)]">*</span>
            </label>
            <textarea
              rows={4}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Describe la decisión tomada y el fundamento…"
              className="
                w-full px-3 py-2 text-sm rounded-lg resize-none
                border border-[var(--color-border)] bg-[var(--color-surface-2)]
                focus:outline-none focus:border-[var(--color-accent)]
              "
            />
          </div>
          {releaseError && <Alert type="error">{releaseError}</Alert>}
        </div>
      </Modal>
    </div>
  )
}

function InfoRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="bg-[var(--color-surface-2)] rounded-lg px-3 py-2">
      <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-4)] mb-0.5">{label}</div>
      <div className={`text-xs text-[var(--color-text-2)] truncate ${mono ? 'font-mono' : ''}`}>{value}</div>
    </div>
  )
}
