import {
  useState, useRef, useCallback,
  type DragEvent, type ChangeEvent,
} from 'react'
import { useUploadDocument } from '../../hooks/useTransactions'
import { useUser } from '../../store/authStore'
import { Button } from '../../components/ui/Button'
import { Alert } from '../../components/ui/Alert'
import { Card, CardHeader } from '../../components/ui/Card'
import { ApiRequestError } from '../../api/client'

type UploadStage = 'idle' | 'reading' | 'uploading' | 'done' | 'error'

export function DocumentUpload() {
  const user    = useUser()
  const upload  = useUploadDocument()

  const [stage,     setStage]     = useState<UploadStage>('idle')
  const [text,      setText]      = useState('')
  const [errorMsg,  setErrorMsg]  = useState<string | null>(null)
  const [txnId,     setTxnId]     = useState<string | null>(null)
  const [dragging,  setDragging]  = useState(false)
  const [filename,  setFilename]  = useState<string | null>(null)

  const fileInputRef   = useRef<HTMLInputElement>(null)
  const cameraInputRef = useRef<HTMLInputElement>(null)

  // ── File reading ─────────────────────────────────────────────────────
  const readFile = useCallback((file: File) => {
    setFilename(file.name)
    setStage('reading')
    setErrorMsg(null)

    const reader = new FileReader()
    reader.onload = (e) => {
      const content = e.target?.result
      if (typeof content === 'string') {
        setText(content)
        setStage('idle')
      }
    }
    reader.onerror = () => {
      setErrorMsg('No se pudo leer el archivo')
      setStage('error')
    }
    reader.readAsText(file)
  }, [])

  // ── Drag & Drop ──────────────────────────────────────────────────────
  const onDragOver  = (e: DragEvent) => { e.preventDefault(); setDragging(true)  }
  const onDragLeave = (e: DragEvent) => { e.preventDefault(); setDragging(false) }
  const onDrop      = (e: DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files[0]
    if (file) readFile(file)
  }

  // ── File input ───────────────────────────────────────────────────────
  const onFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) readFile(file)
  }

  // ── Camera (mobile) ──────────────────────────────────────────────────
  const onCameraChange = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) {
      setFilename(`📷 Foto — ${new Date().toLocaleTimeString()}`)
      // For images we send a stub text; Phase 2 would OCR
      setText(`[IMAGEN CAPTURADA: ${file.name}]\nTamaño: ${(file.size / 1024).toFixed(1)} KB\nTipo: ${file.type}`)
      setStage('idle')
    }
  }

  // ── Submit ───────────────────────────────────────────────────────────
  async function handleSubmit() {
    if (!text.trim() || !user?.client_id) return
    setStage('uploading')
    setErrorMsg(null)
    try {
      const res = await upload.mutateAsync({
        raw_text:    text,
        client_id:   user.client_id,
        source_format: filename?.endsWith('.csv') ? 'CSV' : filename?.endsWith('.json') ? 'JSON' : 'TEXT',
      })
      setTxnId(res.transaction_id)
      setStage('done')
      setText('')
      setFilename(null)
    } catch (err) {
      setErrorMsg(err instanceof ApiRequestError ? err.detail : 'Error al subir el documento')
      setStage('error')
    }
  }

  function reset() {
    setStage('idle')
    setText('')
    setFilename(null)
    setErrorMsg(null)
    setTxnId(null)
  }

  return (
    <Card>
      <CardHeader
        title="Subir Documento"
        subtitle="PDF · CSV · JSON · Texto · Foto de recibo"
        icon={<span className="text-sm">⇪</span>}
      />

      {stage === 'done' && txnId ? (
        <div className="space-y-3">
          <Alert type="success" title="Documento enviado">
            El Orquestador está procesando tu documento.
            ID: <span className="font-mono text-[10px]">{txnId}</span>
          </Alert>
          <Button variant="secondary" onClick={reset} className="w-full">
            Subir otro
          </Button>
        </div>
      ) : (
        <div className="space-y-4">
          {/* Drop zone */}
          <div
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
            className={`
              relative border-2 border-dashed rounded-xl
              transition-colors duration-150 cursor-pointer
              ${dragging
                ? 'border-[var(--color-accent)] bg-[var(--color-accent-dim)]'
                : 'border-[var(--color-border-2)] hover:border-[var(--color-accent)] hover:bg-[var(--color-surface-2)]'}
            `}
            onClick={() => fileInputRef.current?.click()}
          >
            <div className="flex flex-col items-center justify-center py-10 px-4 text-center">
              <span className="text-3xl mb-3">
                {stage === 'reading' ? '⏳' : filename ? '📄' : '📂'}
              </span>
              {filename ? (
                <>
                  <p className="text-sm font-medium text-[var(--color-text-2)]">{filename}</p>
                  <p className="text-xs text-[var(--color-success)] mt-1">Listo para enviar</p>
                </>
              ) : (
                <>
                  <p className="text-sm font-medium text-[var(--color-text-2)]">
                    Arrastra tu documento aquí
                  </p>
                  <p className="text-xs text-[var(--color-text-4)] mt-1">
                    o haz clic para seleccionar
                  </p>
                </>
              )}
            </div>
            <input
              ref={fileInputRef}
              type="file"
              accept=".txt,.csv,.json,.pdf"
              className="hidden"
              onChange={onFileChange}
            />
          </div>

          {/* Camera button (mobile) */}
          <div className="flex gap-2">
            <Button
              variant="secondary"
              size="sm"
              icon={<span>📷</span>}
              onClick={() => cameraInputRef.current?.click()}
              className="flex-1"
            >
              Fotografiar recibo
            </Button>
            <input
              ref={cameraInputRef}
              type="file"
              accept="image/*"
              capture="environment"
              className="hidden"
              onChange={onCameraChange}
            />
          </div>

          {/* Manual text area */}
          {!filename && (
            <div>
              <label className="block text-xs font-medium text-[var(--color-text-3)] mb-1.5">
                O pega el texto directamente
              </label>
              <textarea
                rows={5}
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="Descripción de la transacción, extracto bancario, factura…"
                className="
                  w-full px-3 py-2 text-sm rounded-lg resize-none
                  border border-[var(--color-border)] bg-[var(--color-surface-2)]
                  text-[var(--color-text)] placeholder:text-[var(--color-text-4)]
                  focus:outline-none focus:border-[var(--color-accent)] focus:ring-2 focus:ring-[var(--color-accent-dim)]
                  font-mono
                "
              />
            </div>
          )}

          {errorMsg && (
            <Alert type="error" onDismiss={() => setErrorMsg(null)}>
              {errorMsg}
            </Alert>
          )}

          <Button
            onClick={handleSubmit}
            loading={stage === 'uploading'}
            disabled={!text.trim()}
            className="w-full"
          >
            Enviar al Orquestador →
          </Button>
        </div>
      )}
    </Card>
  )
}
