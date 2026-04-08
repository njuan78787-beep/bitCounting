import { Routes, Route, Navigate } from 'react-router-dom'
import { useUser, useIsCpa, useIsAdmin } from './store/authStore'
import { Layout } from './components/layout/Layout'

// Auth
import { LoginView } from './views/auth/LoginView'

// Client views
import { ClientDashboard } from './views/client/ClientDashboard'
import { TransactionList } from './views/client/TransactionList'
import { DocumentUpload }  from './views/client/DocumentUpload'
import { FinancialSummary } from './views/client/FinancialSummary'

// CPA views
import { CpaDashboard }       from './views/cpa/CpaDashboard'
import { ReviewQueue }        from './views/cpa/ReviewQueue'
import { PauseListPanel }     from './views/cpa/PauseDetail'
import { InstructionModule }  from './views/cpa/InstructionModule'
import { CpaMetrics }         from './views/cpa/CpaMetrics'

// Admin views
import { NormativeUpdatesView } from './views/admin/NormativeUpdates'

// ─── Guard: redirect to login if not authenticated ───────────────────────────
function RequireAuth({ children }: { children: React.ReactNode }) {
  const user = useUser()
  if (!user) return <Navigate to="/login" replace />
  return <>{children}</>
}

// ─── Guard: CPA or Admin only ─────────────────────────────────────────────────
function RequireCpa({ children }: { children: React.ReactNode }) {
  const user  = useUser()
  const isCpa = useIsCpa()
  if (!user)   return <Navigate to="/login"     replace />
  if (!isCpa)  return <Navigate to="/dashboard" replace />
  return <>{children}</>
}

// ─── Guard: Admin only ────────────────────────────────────────────────────────
function RequireAdmin({ children }: { children: React.ReactNode }) {
  const user    = useUser()
  const isAdmin = useIsAdmin()
  if (!user)    return <Navigate to="/login" replace />
  if (!isAdmin) return <Navigate to="/cpa"   replace />
  return <>{children}</>
}

// ─── Smart redirect from "/" based on role ───────────────────────────────────
function RootRedirect() {
  const user    = useUser()
  const isCpa   = useIsCpa()
  const isAdmin = useIsAdmin()
  if (!user) return <Navigate to="/login" replace />
  if (isAdmin || isCpa) return <Navigate to="/cpa" replace />
  return <Navigate to="/dashboard" replace />
}

// ─── Thin wrappers to embed standalone views inside Layout ───────────────────
function ClientUploadPage() {
  return (
    <div className="p-4 sm:p-6 max-w-xl">
      <h1 className="text-base font-semibold text-[var(--color-text)] mb-4">Subir Documento</h1>
      <DocumentUpload />
    </div>
  )
}

function ClientReportsPage() {
  return (
    <div className="p-4 sm:p-6">
      <h1 className="text-base font-semibold text-[var(--color-text)] mb-4">Estados Financieros</h1>
      <FinancialSummary />
    </div>
  )
}

function CpaQueuePage() {
  return (
    <div className="p-4 sm:p-6">
      <h1 className="text-base font-semibold text-[var(--color-text)] mb-4">Cola de Revisión</h1>
      <ReviewQueue />
    </div>
  )
}

function CpaPausesPage() {
  return (
    <div className="p-4 sm:p-6">
      <h1 className="text-base font-semibold text-[var(--color-text)] mb-4">Pausas CENTINELA</h1>
      <PauseListPanel />
    </div>
  )
}

function CpaInstructPage() {
  return (
    <div className="p-4 sm:p-6 max-w-2xl">
      <InstructionModule />
    </div>
  )
}

function CpaMetricsPage() {
  return (
    <div className="p-4 sm:p-6">
      <h1 className="text-base font-semibold text-[var(--color-text)] mb-4">Métricas CPA</h1>
      <CpaMetrics />
    </div>
  )
}

function AdminHomePage() {
  return (
    <div className="p-4 sm:p-6">
      <h1 className="text-base font-semibold text-[var(--color-text)] mb-2">Administración</h1>
      <p className="text-sm text-[var(--color-text-4)] mb-6">Panel exclusivo EXIMIA_ADMIN</p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {[
          { label: 'Actualizaciones Normativas', desc: 'Motor ACTUALIZADOR V2', href: '/admin/normative', icon: '§' },
          { label: 'Cola de Revisión CPA',       desc: 'Supervisión de decisiones',  href: '/cpa/queue',      icon: '▤' },
          { label: 'Pausas CENTINELA',            desc: 'Gestión de interrupciones',  href: '/cpa/pauses',     icon: '⏸' },
          { label: 'Métricas del Sistema',        desc: 'KPIs y cumplimiento SLA',     href: '/cpa/metrics',    icon: '◐' },
        ].map((item) => (
          <a
            key={item.href}
            href={item.href}
            className="card p-5 flex items-start gap-4 hover:card-elevated transition-shadow no-underline group"
          >
            <div className="w-10 h-10 rounded-xl bg-[var(--color-accent-dim)] flex items-center justify-center text-[var(--color-accent)] text-lg flex-shrink-0 group-hover:bg-[var(--color-accent)] group-hover:text-white transition-colors">
              {item.icon}
            </div>
            <div>
              <div className="text-sm font-semibold text-[var(--color-text-2)]">{item.label}</div>
              <div className="text-xs text-[var(--color-text-4)] mt-0.5">{item.desc}</div>
            </div>
          </a>
        ))}
      </div>
    </div>
  )
}

// ─── App ──────────────────────────────────────────────────────────────────────

export default function App() {
  return (
    <Routes>
      {/* Public */}
      <Route path="/login" element={<LoginView />} />

      {/* Root redirect */}
      <Route path="/" element={<RootRedirect />} />

      {/* Client routes */}
      <Route path="/dashboard" element={
        <RequireAuth><Layout><ClientDashboard /></Layout></RequireAuth>
      } />
      <Route path="/upload" element={
        <RequireAuth><Layout><ClientUploadPage /></Layout></RequireAuth>
      } />
      <Route path="/transactions" element={
        <RequireAuth><Layout><div className="p-4 sm:p-6"><TransactionList /></div></Layout></RequireAuth>
      } />
      <Route path="/reports" element={
        <RequireAuth><Layout><ClientReportsPage /></Layout></RequireAuth>
      } />

      {/* CPA routes */}
      <Route path="/cpa" element={
        <RequireCpa><Layout><CpaDashboard /></Layout></RequireCpa>
      } />
      <Route path="/cpa/queue" element={
        <RequireCpa><Layout><CpaQueuePage /></Layout></RequireCpa>
      } />
      <Route path="/cpa/pauses" element={
        <RequireCpa><Layout><CpaPausesPage /></Layout></RequireCpa>
      } />
      <Route path="/cpa/instruct" element={
        <RequireCpa><Layout><CpaInstructPage /></Layout></RequireCpa>
      } />
      <Route path="/cpa/metrics" element={
        <RequireCpa><Layout><CpaMetricsPage /></Layout></RequireCpa>
      } />

      {/* Admin routes */}
      <Route path="/admin" element={
        <RequireAdmin><Layout><AdminHomePage /></Layout></RequireAdmin>
      } />
      <Route path="/admin/normative" element={
        <RequireAdmin><Layout><NormativeUpdatesView /></Layout></RequireAdmin>
      } />

      {/* 404 fallback */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
