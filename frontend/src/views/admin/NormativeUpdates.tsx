import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { adminApi } from '../../api/endpoints'
import { Header } from '../../components/layout/Header'
import { Card, CardSkeleton } from '../../components/ui/Card'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { Modal } from '../../components/ui/Modal'
import { Alert, NetworkError } from '../../components/ui/Alert'
import { formatDateTime } from '../../utils/dates'
import { ApiRequestError } from '../../api/client'
import type { NormativeUpdate } from '../../types/api'

const NORMATIVE_KEY = 'admin-normative'

function useNormativeUpdates() {
  return useQuery({
    queryKey: [NORMATIVE_KEY],
    queryFn:  adminApi.listNormativeUpdates,
    refetchInterval: 60_000,
  })
}

function useActivateUpdate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (args: {
      id: string
      cpa_license: string
      digital_signature: string
      effective_date: string
      interpretation_note: string
    }) => adminApi.activateUpdate(args.id, {
      cpa_license:        args.cpa_license,
      digital_signature:  args.digital_signature,
      effective_date:     args.effective_date,
      interpretation_note: args.interpretation_note,
    }),
    onSuccess: () => qc.invalidateQueries({ queryKey: [NORMATIVE_KEY] }),
  })
}

const IMPACT_COLOR: Record<string, string> = {
  LOW:      'success',
  MEDIUM:   'warning',
  HIGH:     'danger',
  CRITICAL: 'danger',
}
const STATUS_COLOR: Record<string, string> = {
  PENDING_REVIEW: 'warning',
  UNDER_REVIEW:   'accent',
  APPROVED:       'success',
  APPLIED:        'neutral',
}

export function NormativeUpdatesView() {
  const { data, isLoading, error, refetch } = useNormativeUpdates()
  const activate = useActivateUpdate()

  const [selected,  setSelected]  = useState<NormativeUpdate | null>(null)
  const [showModal, setShowModal]  = useState(false)

  // 3-factor form state
  const [cpaLicense,   setCpaLicense]   = useState('')
  const [digitalSig,   setDigitalSig]   = useState('')
  const [effectiveDate, setEffDate]     = useState('')
  const [interpNote,   setInterpNote]   = useState('')
  const [activateErr,  setActivateErr]  = useState<string | null>(null)
  const [activateDone, setActivateDone] = useState(false)

  function openActivate(u: NormativeUpdate) {
    setSelected(u)
    setActivateErr(null)
    setActivateDone(false)
    setCpaLicense('')
    setDigitalSig('')
    setEffDate('')
    setInterpNote('')
    setShowModal(true)
  }

  const canActivate =
    cpaLicense.trim().length >= 3 &&
    /^[0-9a-fA-F]{16,}$/.test(digitalSig) &&
    effectiveDate.length === 10 &&
    new Date(effectiveDate) > new Date() &&
    interpNote.trim().length >= 20

  async function handleActivate() {
    if (!selected || !canActivate) return
    setActivateErr(null)
    try {
      await activate.mutateAsync({
        id: selected.update_id,
        cpa_license:        cpaLicense,
        digital_signature:  digitalSig,
        effective_date:     effectiveDate,
        interpretation_note: interpNote,
      })
      setActivateDone(true)
    } catch (err) {
      setActivateErr(err instanceof ApiRequestError ? err.detail : 'Error al activar')
    }
  }

  return (
    <div className="flex flex-col h-full">
      <Header
        title="Actualizaciones Normativas"
        subtitle="EXIMIA_ADMIN · Motor ACTUALIZADOR V2"
        actions={<Button variant="ghost" size="sm" onClick={() => refetch()}>↻ Actualizar</Button>}
      />

      <div className="flex-1 p-4 sm:p-6 overflow-y-auto space-y-4">
        {isLoading ? (
          <div className="space-y-3">
            {[...Array(3)].map((_, i) => <CardSkeleton key={i} rows={4} />)}
          </div>
        ) : error ? (
          <NetworkError message="No se pudieron cargar las actualizaciones normativas" onRetry={refetch} />
        ) : !data || data.length === 0 ? (
          <Card>
            <div className="py-10 text-center">
              <div className="text-3xl mb-3">§</div>
              <p className="text-sm text-[var(--color-text-4)]">Sin actualizaciones normativas detectadas</p>
            </div>
          </Card>
        ) : (
          <>
            <Alert type="warning" title="Zona de administración">
              Las activaciones normativas son permanentes y requieren los 3 factores de validación.
              Cada acción queda registrada con usuario, timestamp y firma digital.
            </Alert>

            <div className="grid grid-cols-1 gap-4">
              {data.map((u) => (
                <NormativeCard
                  key={u.update_id}
                  update={u}
                  onActivate={() => openActivate(u)}
                />
              ))}
            </div>
          </>
        )}
      </div>

      {/* 3-factor activation modal */}
      <Modal
        open={showModal}
        onClose={() => setShowModal(false)}
        title="Activar actualización normativa"
        size="lg"
        actions={
          activateDone ? (
            <Button onClick={() => setShowModal(false)}>Cerrar</Button>
          ) : (
            <>
              <Button variant="secondary" onClick={() => setShowModal(false)}>Cancelar</Button>
              <Button
                variant="danger"
                loading={activate.isPending}
                disabled={!canActivate}
                onClick={handleActivate}
              >
                Activar (3 factores)
              </Button>
            </>
          )
        }
      >
        {activateDone ? (
          <div className="text-center py-4 space-y-3">
            <div className="text-4xl">✓</div>
            <Badge color="success">Activada exitosamente</Badge>
            <p className="text-sm text-[var(--color-text-2)]">
              La actualización normativa ha sido activada y registrada.
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            {selected && (
              <div className="bg-[var(--color-surface-2)] rounded-lg p-3 text-xs">
                <div className="font-semibold text-[var(--color-text-2)] mb-1">{selected.description}</div>
                <div className="text-[var(--color-text-4)]">Fuente: {selected.source_name}</div>
              </div>
            )}

            <Alert type="error" title="Los 3 factores son obligatorios y simultáneos">
              1. Licencia CPA + Firma digital (hex ≥16 chars) &nbsp;·&nbsp;
              2. Fecha de vigencia futura &nbsp;·&nbsp;
              3. Nota de interpretación (≥20 chars)
            </Alert>

            <div className="grid grid-cols-2 gap-3">
              {/* Factor 1a: CPA license */}
              <div>
                <label className="block text-xs font-medium text-[var(--color-text-2)] mb-1.5">
                  Licencia CPA <span className="text-[var(--color-danger)]">*</span>
                </label>
                <input
                  type="text"
                  value={cpaLicense}
                  onChange={e => setCpaLicense(e.target.value)}
                  placeholder="CPA-XXXX-000"
                  className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] focus:outline-none focus:border-[var(--color-accent)] font-mono"
                />
              </div>

              {/* Factor 2: effective date */}
              <div>
                <label className="block text-xs font-medium text-[var(--color-text-2)] mb-1.5">
                  Fecha de vigencia (futura) <span className="text-[var(--color-danger)]">*</span>
                </label>
                <input
                  type="date"
                  value={effectiveDate}
                  onChange={e => setEffDate(e.target.value)}
                  min={new Date().toISOString().slice(0, 10)}
                  className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] focus:outline-none focus:border-[var(--color-accent)]"
                />
              </div>
            </div>

            {/* Factor 1b: digital signature */}
            <div>
              <label className="block text-xs font-medium text-[var(--color-text-2)] mb-1.5">
                Firma digital hexadecimal (mín. 16 chars)
                <span className="text-[var(--color-danger)]"> *</span>
              </label>
              <input
                type="text"
                value={digitalSig}
                onChange={e => setDigitalSig(e.target.value.replace(/[^0-9a-fA-F]/g, ''))}
                placeholder="a1b2c3d4e5f6789012345678…"
                className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] focus:outline-none focus:border-[var(--color-accent)] font-mono"
              />
              {digitalSig.length > 0 && digitalSig.length < 16 && (
                <p className="text-[10px] text-[var(--color-danger)] mt-1">
                  Mínimo 16 caracteres hex ({16 - digitalSig.length} restantes)
                </p>
              )}
              {digitalSig.length >= 16 && (
                <p className="text-[10px] text-[var(--color-success)] mt-1">✓ Firma válida</p>
              )}
            </div>

            {/* Factor 3: interpretation note */}
            <div>
              <label className="block text-xs font-medium text-[var(--color-text-2)] mb-1.5">
                Nota de interpretación (mín. 20 chars)
                <span className="text-[var(--color-danger)]"> *</span>
              </label>
              <textarea
                rows={4}
                value={interpNote}
                onChange={e => setInterpNote(e.target.value)}
                placeholder="Explica el impacto de esta actualización normativa y cómo debe ser aplicada en el contexto de los clientes…"
                className="w-full px-3 py-2 text-sm rounded-lg resize-none border border-[var(--color-border)] bg-[var(--color-surface-2)] focus:outline-none focus:border-[var(--color-accent)]"
              />
              <p className={`text-[10px] mt-1 ${interpNote.length >= 20 ? 'text-[var(--color-success)]' : 'text-[var(--color-text-4)]'}`}>
                {interpNote.length}/20 caracteres mínimos
              </p>
            </div>

            {activateErr && <Alert type="error">{activateErr}</Alert>}
          </div>
        )}
      </Modal>
    </div>
  )
}

function NormativeCard({ update, onActivate }: { update: NormativeUpdate; onActivate: () => void }) {
  const [expanded, setExpanded] = useState(false)

  const isPending = update.status === 'PENDING_REVIEW' || update.status === 'UNDER_REVIEW'

  return (
    <Card elevated>
      <div className="flex items-start gap-4">
        {/* Left: impact badge */}
        <div className="flex-shrink-0 flex flex-col items-center gap-1 pt-1">
          <Badge color={(IMPACT_COLOR[update.impact_level] ?? 'neutral') as Parameters<typeof Badge>[0]['color']}>
            {update.impact_level}
          </Badge>
          <div className="text-[10px] text-[var(--color-text-4)] text-center">impacto</div>
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-1">
            <span className="text-sm font-semibold text-[var(--color-text-2)]">
              {update.description}
            </span>
            <Badge color={(STATUS_COLOR[update.status] ?? 'neutral') as Parameters<typeof Badge>[0]['color']}>
              {update.status}
            </Badge>
            {update.requires_human_review && (
              <Badge color="warning" dot>Revisión humana requerida</Badge>
            )}
          </div>

          <div className="flex flex-wrap gap-3 text-[10px] text-[var(--color-text-4)] mb-3">
            <span>Fuente: <strong className="text-[var(--color-text-3)]">{update.source_name}</strong></span>
            <span>Tipo: <strong className="text-[var(--color-text-3)]">{update.change_type}</strong></span>
            <span>Detectado: <strong className="text-[var(--color-text-3)]">{formatDateTime(update.detected_at)}</strong></span>
            <span>SLA: <strong className="text-[var(--color-text-3)]">{update.sla_hours}h · {formatDateTime(update.sla_deadline)}</strong></span>
          </div>

          {expanded && (
            <div className="mb-3 text-xs text-[var(--color-text-2)] bg-[var(--color-surface-2)] rounded-lg p-3 leading-relaxed">
              <div className="font-mono text-[10px] text-[var(--color-text-4)] mb-1">
                ID: {update.update_id}
              </div>
              <div>{update.description}</div>
            </div>
          )}

          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={() => setExpanded(x => !x)}>
              {expanded ? '▲ Contraer' : '▼ Ver detalles'}
            </Button>
            {isPending && (
              <Button variant="danger" size="sm" onClick={onActivate}>
                Activar con 3 factores
              </Button>
            )}
          </div>
        </div>
      </div>
    </Card>
  )
}
