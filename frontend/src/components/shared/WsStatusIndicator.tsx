import { useWsStatus, useWsEvents } from '../../store/wsStore'
import { relativeTime } from '../../utils/dates'

export function WsStatusIndicator() {
  const status = useWsStatus()
  const events = useWsEvents()
  const last   = events[0]

  const cfg = {
    connected:    { color: 'bg-[var(--color-success)]', label: 'En vivo',        pulse: true  },
    connecting:   { color: 'bg-[var(--color-warning)]', label: 'Conectando…',    pulse: false },
    disconnected: { color: 'bg-[var(--color-text-4)]',  label: 'Desconectado',   pulse: false },
    error:        { color: 'bg-[var(--color-danger)]',  label: 'Sin conexión',   pulse: false },
  }[status]

  return (
    <div className="flex items-center gap-2 text-xs text-[var(--color-text-4)]">
      <span
        className={`
          w-2 h-2 rounded-full flex-shrink-0
          ${cfg.color}
          ${cfg.pulse ? 'animate-pulse-dot' : ''}
        `}
      />
      <span>{cfg.label}</span>
      {last && (
        <span className="hidden sm:inline opacity-60">· {relativeTime(last.timestamp)}</span>
      )}
    </div>
  )
}

export function ActivityFeed() {
  const events = useWsEvents().slice(0, 8)

  const eventLabel: Record<string, string> = {
    'transaction.updated':     'Transacción actualizada',
    'pause.created':           'Nueva pausa CENTINELA',
    'pause.resolved':          'Pausa resuelta',
    'orchestrator.decision':   'Decisión del Orquestador',
    'normative.detected':      'Cambio normativo detectado',
  }

  if (events.length === 0) return (
    <div className="text-xs text-[var(--color-text-4)] italic py-2">
      Sin actividad reciente
    </div>
  )

  return (
    <ul className="space-y-1.5">
      {events.map((ev, i) => (
        <li key={i} className="flex items-start gap-2 text-xs animate-fade-in">
          <span className="flex-shrink-0 w-1.5 h-1.5 mt-1.5 rounded-full bg-[var(--color-accent-2)]" />
          <div>
            <span className="text-[var(--color-text-2)]">
              {eventLabel[ev.event] ?? ev.event}
            </span>
            <span className="ml-1 text-[var(--color-text-4)]">
              · {relativeTime(ev.timestamp)}
            </span>
          </div>
        </li>
      ))}
    </ul>
  )
}
