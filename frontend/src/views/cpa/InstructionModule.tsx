import { useState } from 'react'
import {
  useCreateInstruction,
  usePreviewInstruction,
  useConfirmInstruction,
} from '../../hooks/useCpa'
import { useUser } from '../../store/authStore'
import { Card, CardHeader } from '../../components/ui/Card'
import { Button } from '../../components/ui/Button'
import { Alert } from '../../components/ui/Alert'
import { Badge } from '../../components/ui/Badge'
import { ApiRequestError } from '../../api/client'
import type { CpaInstruction } from '../../types/api'

type Stage = 'compose' | 'preview' | 'confirmed'

export function InstructionModule() {
  const user = useUser()

  const createFn  = useCreateInstruction()
  const previewFn = usePreviewInstruction()
  const confirmFn = useConfirmInstruction()

  const [stage,    setStage]    = useState<Stage>('compose')
  const [content,  setContent]  = useState('')
  const [preview,  setPreview]  = useState<string | null>(null)
  const [instrId,  setInstrId]  = useState<string | null>(null)
  const [error,    setError]    = useState<string | null>(null)
  const [confirmed, setConfirmed] = useState<CpaInstruction | null>(null)

  const clientId = user?.client_id ?? ''

  async function handlePreview() {
    if (!content.trim() || !clientId) return
    setError(null)
    try {
      const instr = await createFn.mutateAsync({ client_id: clientId, content })
      setInstrId(instr.instruction_id)
      // get preview
      const prev = await previewFn.mutateAsync(instr.instruction_id)
      setPreview(prev.preview)
      setStage('preview')
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.detail : 'Error al generar preview')
    }
  }

  async function handleConfirm() {
    if (!instrId) return
    setError(null)
    try {
      const result = await confirmFn.mutateAsync(instrId)
      setConfirmed(result)
      setStage('confirmed')
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.detail : 'Error al confirmar')
    }
  }

  function reset() {
    setStage('compose')
    setContent('')
    setPreview(null)
    setInstrId(null)
    setError(null)
    setConfirmed(null)
  }

  return (
    <Card>
      <CardHeader
        title="Módulo de Instrucciones CPA"
        subtitle="Redactar → Previsualizar → Confirmar"
        icon={<span className="text-sm">✎</span>}
      />

      {/* Progress */}
      <div className="flex items-center gap-2 mb-5">
        <StageStep label="Redactar"       active={stage === 'compose'}   done={stage !== 'compose'}   />
        <div className="flex-1 h-px bg-[var(--color-border)]" />
        <StageStep label="Previsualizar"  active={stage === 'preview'}   done={stage === 'confirmed'} />
        <div className="flex-1 h-px bg-[var(--color-border)]" />
        <StageStep label="Confirmada"     active={stage === 'confirmed'} done={false}                 />
      </div>

      {error && (
        <Alert type="error" className="mb-4" onDismiss={() => setError(null)}>{error}</Alert>
      )}

      {/* Stage: compose */}
      {stage === 'compose' && (
        <div className="space-y-4 animate-fade-in">
          <div>
            <label className="block text-xs font-medium text-[var(--color-text-2)] mb-2">
              Instrucción contable para el cliente
              <span className="ml-1 text-[var(--color-text-4)]">({content.length} caracteres)</span>
            </label>
            <textarea
              rows={8}
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder={`Ejemplo:
Reclasificar gastos de "Equipos de Computación" (cuenta 1500) a "Gastos de Operación" (cuenta 5900) para todas las compras menores de $500.

Aplicar desde el período 2026-01 en adelante.
Fundamento: IRS Publication 946, Section 179 expensing.`}
              className="
                w-full px-3 py-2 text-sm rounded-lg resize-none
                border border-[var(--color-border)] bg-[var(--color-surface-2)]
                text-[var(--color-text)] placeholder:text-[var(--color-text-4)]
                focus:outline-none focus:border-[var(--color-accent)] focus:ring-2 focus:ring-[var(--color-accent-dim)]
                font-mono leading-relaxed
              "
            />
          </div>
          <div className="flex gap-2">
            <Button
              onClick={handlePreview}
              loading={createFn.isPending || previewFn.isPending}
              disabled={content.trim().length < 20 || !clientId}
              className="flex-1"
            >
              Previsualizar →
            </Button>
          </div>
          {content.trim().length < 20 && content.length > 0 && (
            <p className="text-xs text-[var(--color-text-4)]">
              Mínimo 20 caracteres ({20 - content.trim().length} restantes)
            </p>
          )}
        </div>
      )}

      {/* Stage: preview */}
      {stage === 'preview' && (
        <div className="space-y-4 animate-fade-in">
          <div className="bg-[var(--color-surface-2)] rounded-xl p-4 border border-[var(--color-border)]">
            <div className="flex items-center gap-2 mb-3">
              <Badge color="accent">Preview</Badge>
              <span className="text-xs text-[var(--color-text-4)]">
                ID: <span className="font-mono">{instrId?.slice(0, 16)}…</span>
              </span>
            </div>

            {/* Original */}
            <div className="mb-4">
              <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-4)] mb-1.5">
                Instrucción original
              </div>
              <p className="text-xs text-[var(--color-text-2)] leading-relaxed whitespace-pre-wrap border-l-2 border-[var(--color-border-2)] pl-3">
                {content}
              </p>
            </div>

            {/* Preview */}
            <div>
              <div className="text-[10px] uppercase tracking-wider text-[var(--color-accent)] mb-1.5">
                ↳ Como será aplicado
              </div>
              <p className="text-xs text-[var(--color-text-2)] leading-relaxed whitespace-pre-wrap bg-white rounded-lg p-3 border border-[var(--color-border)]">
                {preview ?? 'El sistema interpretará y aplicará esta instrucción a las transacciones del cliente según las reglas contables vigentes.'}
              </p>
            </div>
          </div>

          <Alert type="warning" title="Revisa antes de confirmar">
            Una vez confirmada, esta instrucción será aplicada permanentemente a las transacciones del cliente.
            Verifica que refleja exactamente tu intención profesional.
          </Alert>

          <div className="flex gap-2">
            <Button variant="secondary" onClick={() => setStage('compose')} className="flex-1">
              ← Editar
            </Button>
            <Button
              variant="success"
              loading={confirmFn.isPending}
              onClick={handleConfirm}
              className="flex-1"
            >
              ✓ Confirmar instrucción
            </Button>
          </div>
        </div>
      )}

      {/* Stage: confirmed */}
      {stage === 'confirmed' && confirmed && (
        <div className="space-y-4 animate-fade-in text-center">
          <div className="text-4xl mb-3">✓</div>
          <div>
            <Badge color="success">Confirmada</Badge>
            <p className="text-sm font-medium text-[var(--color-text-2)] mt-2">
              Instrucción aplicada exitosamente
            </p>
            <p className="text-xs text-[var(--color-text-4)] mt-1 font-mono">
              {confirmed.instruction_id}
            </p>
          </div>
          <Button variant="secondary" onClick={reset} className="w-full">
            Nueva instrucción
          </Button>
        </div>
      )}
    </Card>
  )
}

function StageStep({ label, active, done }: { label: string; active: boolean; done: boolean }) {
  return (
    <div className="flex flex-col items-center gap-1 flex-shrink-0">
      <div className={`
        w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold
        ${done   ? 'bg-[var(--color-success)] text-white' :
          active ? 'bg-[var(--color-accent)] text-white ring-2 ring-[var(--color-accent-dim)]' :
                   'bg-[var(--color-surface-3)] text-[var(--color-text-4)]'}
      `}>
        {done ? '✓' : active ? '●' : '○'}
      </div>
      <span className="text-[10px] text-[var(--color-text-4)] whitespace-nowrap">{label}</span>
    </div>
  )
}
