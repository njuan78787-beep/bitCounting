import { useState } from 'react'
import { useReviewQueue, useApproveItem } from '../../hooks/useCpa'
import { Card, CardHeader, CardSkeleton } from '../../components/ui/Card'
import { SeverityBadge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { NetworkError, Alert } from '../../components/ui/Alert'
import { Modal } from '../../components/ui/Modal'
import { formatDateTime, hoursUntil, slaColor } from '../../utils/dates'
import { ApiRequestError } from '../../api/client'
import type { ReviewQueueItem, PauseSeverity } from '../../types/api'

// ─── Friction questions by severity ───────────────────────────────────────────
const HIGH_FRICTION_QUESTIONS: Record<string, string> = {
  default: '¿Has revisado la documentación de soporte completa antes de aprobar?',
  CRITICAL: '¿Confirmas que este caso ha sido revisado con supervisión senior y tienes autoridad para aprobar?',
  HIGH: '¿Has verificado que el monto y la clasificación contable son correctos según las reglas actuales?',
}

export function ReviewQueue() {
  const [page, setPage] = useState(1)
  const { data, isLoading, error, refetch } = useReviewQueue(page)
  const items = data?.items ?? []

  // Sort by SLA ascending (most urgent first)
  const sorted = [...items].sort((a, b) =>
    new Date(a.sla_deadline).getTime() - new Date(b.sla_deadline).getTime()
  )

  return (
    <Card padding="none">
      <CardHeader
        title={`Cola de Revisión ${data ? `· ${data.total} items` : ''}`}
        subtitle="Clasificada por SLA · Fricción proporcional a severidad"
        icon={<span className="text-sm">▤</span>}
        actions={<Button variant="ghost" size="sm" onClick={() => refetch()}>↻</Button>}
      />

      {isLoading ? (
        <div className="px-4 pb-4 space-y-3">
          {[...Array(4)].map((_, i) => <CardSkeleton key={i} rows={2} />)}
        </div>
      ) : error ? (
        <div className="px-4 pb-4">
          <NetworkError message="No se pudo cargar la cola" onRetry={refetch} />
        </div>
      ) : sorted.length === 0 ? (
        <div className="px-4 pb-8 text-center">
          <div className="text-3xl mb-3">✓</div>
          <p className="text-sm font-medium text-[var(--color-success)]">Cola vacía</p>
          <p className="text-xs text-[var(--color-text-4)] mt-1">No hay items pendientes de revisión</p>
        </div>
      ) : (
        <div className="divide-y divide-[var(--color-border)]">
          {sorted.map((item) => (
            <QueueRow key={item.queue_id} item={item} onDone={refetch} />
          ))}
        </div>
      )}

      {data && data.total > 20 && (
        <div className="flex justify-end gap-2 px-4 py-3 border-t border-[var(--color-border)]">
          <Button variant="secondary" size="sm" disabled={page === 1} onClick={() => setPage(p => p - 1)}>←</Button>
          <Button variant="secondary" size="sm" disabled={page * 20 >= data.total} onClick={() => setPage(p => p + 1)}>→</Button>
        </div>
      )}
    </Card>
  )
}

// ─── Single queue row with proportional friction ──────────────────────────────

function QueueRow({ item, onDone }: { item: ReviewQueueItem; onDone: () => void }) {
  const approve   = useApproveItem()
  const hoursLeft = hoursUntil(item.sla_deadline)
  const slaCls    = slaColor(item.sla_deadline)

  const [expanded,    setExpanded]    = useState(false)
  const [showModal,   setShowModal]   = useState(false)
  const [decision,    setDecision]    = useState<'APPROVED' | 'REJECTED'>('APPROVED')
  const [notes,       setNotes]       = useState('')
  const [frictionAns, setFrictionAns] = useState('')
  const [error,       setError]       = useState<string | null>(null)

  const sev = item.severity as PauseSeverity

  // Friction level:
  // LOW    → direct button
  // MEDIUM → must expand to see button
  // HIGH/CRITICAL → must expand + answer question

  const needsExpand   = sev !== 'LOW'
  const needsQuestion = sev === 'HIGH' || sev === 'CRITICAL'
  const question      = HIGH_FRICTION_QUESTIONS[sev] ?? HIGH_FRICTION_QUESTIONS.default

  const canApprove =
    sev === 'LOW'
      ? true
      : expanded && (!needsQuestion || frictionAns.trim().length >= 10)

  async function handleApprove() {
    setError(null)
    try {
      await approve.mutateAsync({ queue_id: item.queue_id, decision, notes: notes || undefined })
      setShowModal(false)
      onDone()
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.detail : 'Error al procesar')
    }
  }

  return (
    <>
      <div className={`px-4 py-3 ${expanded ? 'bg-[var(--color-surface-2)]' : ''}`}>
        {/* Row header */}
        <div className="flex items-start gap-3">
          {/* SLA indicator */}
          <div className="flex-shrink-0 w-12 text-center">
            <div className={`text-xs font-bold font-mono ${
              slaCls === 'danger'  ? 'text-[var(--color-danger)]' :
              slaCls === 'warning' ? 'text-[var(--color-warning)]' :
                                     'text-[var(--color-success)]'
            }`}>
              {hoursLeft < 0 ? 'VENC.' : `${Math.round(hoursLeft)}h`}
            </div>
            <div className="text-[10px] text-[var(--color-text-4)]">SLA</div>
          </div>

          {/* Content */}
          <div className="flex-1 min-w-0">
            <div className="flex flex-wrap items-center gap-2 mb-1">
              <SeverityBadge severity={sev} />
              <span className="text-xs font-medium text-[var(--color-text-2)] truncate">
                {item.reason}
              </span>
            </div>
            <div className="text-[10px] text-[var(--color-text-4)] font-mono">
              {item.transaction_id.slice(0, 16)}… · {formatDateTime(item.sla_deadline)}
            </div>
            {item.pre_analysis && (
              <div className="text-[10px] text-[var(--color-accent)] mt-0.5 truncate">
                ↳ {item.pre_analysis}
              </div>
            )}
          </div>

          {/* Actions */}
          <div className="flex-shrink-0 flex items-center gap-2">
            {/* LOW: direct approve button */}
            {sev === 'LOW' && (
              <Button
                size="sm"
                variant="success"
                onClick={() => { setDecision('APPROVED'); setShowModal(true) }}
              >
                Aprobar
              </Button>
            )}
            {/* MEDIUM+: expand to unlock */}
            {needsExpand && (
              <Button
                size="sm"
                variant="secondary"
                onClick={() => setExpanded((x) => !x)}
              >
                {expanded ? '▲ Contraer' : '▼ Expandir'}
              </Button>
            )}
          </div>
        </div>

        {/* Expanded detail (MEDIUM+) */}
        {expanded && (
          <div className="mt-4 space-y-3 pl-15 animate-fade-in">
            {/* Pre-analysis */}
            {item.pre_analysis && (
              <div className="bg-[var(--color-accent-dim)] rounded-lg p-3 text-xs text-[var(--color-accent)]">
                <div className="font-semibold mb-1">Análisis pre-procesado del CENTINELA</div>
                {item.pre_analysis}
              </div>
            )}

            {/* Friction question for HIGH/CRITICAL */}
            {needsQuestion && (
              <div className="bg-[var(--color-warning-dim)] rounded-lg p-3 border border-amber-200">
                <div className="text-xs font-semibold text-[var(--color-warning)] mb-2">
                  ⚠ Pregunta requerida antes de aprobar
                </div>
                <p className="text-xs text-[var(--color-text-2)] mb-2">{question}</p>
                <textarea
                  rows={2}
                  value={frictionAns}
                  onChange={(e) => setFrictionAns(e.target.value)}
                  placeholder="Responde aquí (mínimo 10 caracteres)…"
                  className="
                    w-full px-2 py-1.5 text-xs rounded-lg resize-none
                    border border-amber-200 bg-white
                    focus:outline-none focus:border-[var(--color-warning)]
                  "
                />
                {frictionAns.length > 0 && frictionAns.length < 10 && (
                  <p className="text-[10px] text-[var(--color-danger)] mt-1">
                    Mínimo 10 caracteres ({10 - frictionAns.length} restantes)
                  </p>
                )}
              </div>
            )}

            {/* Action buttons */}
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="success"
                disabled={!canApprove}
                onClick={() => { setDecision('APPROVED'); setShowModal(true) }}
              >
                ✓ Aprobar
              </Button>
              <Button
                size="sm"
                variant="danger"
                disabled={!expanded}
                onClick={() => { setDecision('REJECTED'); setShowModal(true) }}
              >
                ✕ Rechazar
              </Button>
            </div>
          </div>
        )}
      </div>

      {/* Confirmation modal */}
      <Modal
        open={showModal}
        onClose={() => setShowModal(false)}
        title={decision === 'APPROVED' ? 'Confirmar aprobación' : 'Confirmar rechazo'}
        actions={
          <>
            <Button variant="secondary" onClick={() => setShowModal(false)}>Cancelar</Button>
            <Button
              variant={decision === 'APPROVED' ? 'success' : 'danger'}
              loading={approve.isPending}
              onClick={handleApprove}
            >
              Confirmar {decision === 'APPROVED' ? 'Aprobación' : 'Rechazo'}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Alert type={decision === 'APPROVED' ? 'success' : 'error'}>
            Estás a punto de <strong>{decision === 'APPROVED' ? 'aprobar' : 'rechazar'}</strong> este item.
            Esta acción queda registrada con tu usuario y timestamp.
          </Alert>
          <div>
            <label className="block text-xs font-medium text-[var(--color-text-2)] mb-1.5">
              Notas (opcional)
            </label>
            <textarea
              rows={3}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Comentarios adicionales…"
              className="
                w-full px-3 py-2 text-sm rounded-lg resize-none
                border border-[var(--color-border)] bg-[var(--color-surface-2)]
                focus:outline-none focus:border-[var(--color-accent)]
              "
            />
          </div>
          {error && <Alert type="error">{error}</Alert>}
        </div>
      </Modal>
    </>
  )
}
