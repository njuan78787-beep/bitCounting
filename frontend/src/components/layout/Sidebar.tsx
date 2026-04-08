import { NavLink } from 'react-router-dom'
import { useAuthStore, useIsCpa, useIsAdmin, useUser } from '../../store/authStore'
import { WsStatusIndicator } from '../shared/WsStatusIndicator'

interface NavItem {
  to:    string
  label: string
  icon:  string
}

const CLIENT_NAV: NavItem[] = [
  { to: '/dashboard',      label: 'Dashboard',       icon: '▦' },
  { to: '/upload',         label: 'Subir Documento',  icon: '⇪' },
  { to: '/transactions',   label: 'Transacciones',    icon: '↔' },
  { to: '/reports',        label: 'Estados Financieros', icon: '▤' },
]

const CPA_NAV: NavItem[] = [
  { to: '/cpa',            label: 'Dashboard CPA',    icon: '▦' },
  { to: '/cpa/queue',      label: 'Cola de Revisión', icon: '▤' },
  { to: '/cpa/pauses',     label: 'Pausas CENTINELA', icon: '⏸' },
  { to: '/cpa/instruct',   label: 'Instrucciones',    icon: '✎' },
  { to: '/cpa/metrics',    label: 'Métricas',         icon: '◐' },
]

const ADMIN_NAV: NavItem[] = [
  { to: '/admin',          label: 'Administración',   icon: '⚙' },
  { to: '/admin/normative', label: 'Normativa',       icon: '§' },
]

export function Sidebar({ mobile = false }: { mobile?: boolean }) {
  const user    = useUser()
  const isCpa   = useIsCpa()
  const isAdmin = useIsAdmin()
  const logout  = useAuthStore((s) => s.logout)

  const nav = isAdmin
    ? [...CPA_NAV, ...ADMIN_NAV]
    : isCpa
      ? CPA_NAV
      : CLIENT_NAV

  return (
    <aside
      className={`
        flex flex-col h-full
        bg-[var(--color-surface)] border-r border-[var(--color-border)]
        ${mobile ? 'w-full' : 'w-56 flex-shrink-0'}
      `}
    >
      {/* Logo */}
      <div className="px-4 py-5 border-b border-[var(--color-border)]">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-[var(--color-accent)] flex items-center justify-center">
            <span className="text-white text-xs font-bold font-mono">BC</span>
          </div>
          <div>
            <div className="text-sm font-bold text-[var(--color-text)] tracking-tight">Bit-Counting</div>
            <div className="text-[10px] text-[var(--color-text-4)] uppercase tracking-widest">
              {user?.role ?? 'Sistema'}
            </div>
          </div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-3 py-3 space-y-0.5">
        {nav.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) => `
              flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors duration-100
              ${isActive
                ? 'bg-[var(--color-accent-dim)] text-[var(--color-accent)] font-medium'
                : 'text-[var(--color-text-3)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text-2)]'
              }
            `}
          >
            <span className="text-base w-4 text-center flex-shrink-0">{item.icon}</span>
            <span className="truncate">{item.label}</span>
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="px-4 py-3 border-t border-[var(--color-border)] space-y-2">
        <WsStatusIndicator />
        <div className="flex items-center justify-between">
          <div className="text-xs text-[var(--color-text-4)] truncate min-w-0 mr-2">
            {user?.username}
          </div>
          <button
            onClick={logout}
            className="text-xs text-[var(--color-text-4)] hover:text-[var(--color-danger)] transition-colors cursor-pointer flex-shrink-0"
          >
            Salir
          </button>
        </div>
      </div>
    </aside>
  )
}
