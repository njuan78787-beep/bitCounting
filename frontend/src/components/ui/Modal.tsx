import { useEffect, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

interface ModalProps {
  open:       boolean
  onClose:    () => void
  title:      string
  children:   ReactNode
  actions?:   ReactNode
  size?:      'sm' | 'md' | 'lg'
}

const sizes = { sm: 'max-w-sm', md: 'max-w-md', lg: 'max-w-2xl' }

export function Modal({ open, onClose, title, children, actions, size = 'md' }: ModalProps) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      aria-modal="true"
      role="dialog"
    >
      {/* backdrop */}
      <div
        className="absolute inset-0 bg-[var(--color-text)]/20 backdrop-blur-sm"
        onClick={onClose}
      />
      {/* panel */}
      <div
        className={`
          relative card card-elevated w-full ${sizes[size]}
          animate-fade-in overflow-hidden
        `}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--color-border)]">
          <h2 className="text-sm font-semibold text-[var(--color-text)]">{title}</h2>
          <button
            onClick={onClose}
            className="text-[var(--color-text-4)] hover:text-[var(--color-text-2)] text-xl leading-none cursor-pointer"
            aria-label="Cerrar"
          >
            ×
          </button>
        </div>
        <div className="px-5 py-4">{children}</div>
        {actions && (
          <div className="flex justify-end gap-2 px-5 pb-4">
            {actions}
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}
