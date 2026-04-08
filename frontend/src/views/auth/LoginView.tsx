import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { authApi } from '../../api/endpoints'
import { useAuthStore } from '../../store/authStore'
import { useWsStore } from '../../store/wsStore'
import { setAccessToken } from '../../api/client'
import { Button } from '../../components/ui/Button'
import { Alert } from '../../components/ui/Alert'
import { ApiRequestError } from '../../api/client'

type Step = 'credentials' | 'mfa'

export function LoginView() {
  const navigate  = useNavigate()
  const { setTempToken, setFullAuth } = useAuthStore()
  const connectWs = useWsStore((s) => s.connect)

  const [step,     setStep]     = useState<Step>('credentials')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [totp,     setTotp]     = useState('')
  const [tempTok,  setTempTok]  = useState('')
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState<string | null>(null)

  // ── Step 1: username + password ──────────────────────────────────────
  async function handleLogin(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const res = await authApi.login(username, password)
      if (res.mfa_required) {
        setTempTok(res.access_token)
        setTempToken(res.access_token)
        setStep('mfa')
      } else {
        // No MFA required (shouldn't happen in prod)
        setAccessToken(res.access_token)
        const me = await authApi.me()
        setFullAuth(me, res.access_token, '')
        navigate(me.role === 'CLIENT' ? '/dashboard' : '/cpa')
      }
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.detail : 'Error al conectar con el servidor')
    } finally {
      setLoading(false)
    }
  }

  // ── Step 2: TOTP ─────────────────────────────────────────────────────
  async function handleMfa(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const res = await authApi.mfaVerify(tempTok, totp)
      setAccessToken(res.access_token)
      const me = await authApi.me()
      setFullAuth(me, res.access_token, res.refresh_token)
      connectWs(res.access_token)
      navigate(me.role === 'CLIENT' ? '/dashboard' : '/cpa')
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.detail : 'Código incorrecto')
      setTotp('')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-grid flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="flex flex-col items-center mb-8">
          <div className="w-12 h-12 rounded-2xl bg-[var(--color-accent)] flex items-center justify-center mb-4 shadow-lg">
            <span className="text-white text-xl font-bold font-mono">BC</span>
          </div>
          <h1 className="text-xl font-bold text-[var(--color-text)] tracking-tight">Bit-Counting</h1>
          <p className="text-xs text-[var(--color-text-4)] mt-1 uppercase tracking-widest">
            {step === 'credentials' ? 'Acceso Seguro' : 'Verificación MFA'}
          </p>
        </div>

        {/* Card */}
        <div className="card card-elevated p-6">
          {/* Progress steps */}
          <div className="flex items-center gap-2 mb-6">
            <StepDot active={step === 'credentials'} done={step === 'mfa'} label="1" />
            <div className="flex-1 h-px bg-[var(--color-border)]" />
            <StepDot active={step === 'mfa'} done={false} label="2" />
          </div>

          {error && (
            <Alert type="error" className="mb-4" onDismiss={() => setError(null)}>
              {error}
            </Alert>
          )}

          {step === 'credentials' ? (
            <form onSubmit={handleLogin} className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-[var(--color-text-2)] mb-1.5">
                  Usuario
                </label>
                <input
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  autoComplete="username"
                  required
                  placeholder="nombre.usuario"
                  className="
                    w-full px-3 py-2 text-sm rounded-lg
                    border border-[var(--color-border)] bg-[var(--color-surface-2)]
                    text-[var(--color-text)] placeholder:text-[var(--color-text-4)]
                    focus:outline-none focus:border-[var(--color-accent)] focus:ring-2 focus:ring-[var(--color-accent-dim)]
                    transition-colors
                  "
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-[var(--color-text-2)] mb-1.5">
                  Contraseña
                </label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                  required
                  placeholder="••••••••"
                  className="
                    w-full px-3 py-2 text-sm rounded-lg
                    border border-[var(--color-border)] bg-[var(--color-surface-2)]
                    text-[var(--color-text)] placeholder:text-[var(--color-text-4)]
                    focus:outline-none focus:border-[var(--color-accent)] focus:ring-2 focus:ring-[var(--color-accent-dim)]
                    transition-colors
                  "
                />
              </div>
              <Button type="submit" loading={loading} className="w-full mt-2">
                Continuar →
              </Button>
            </form>
          ) : (
            <form onSubmit={handleMfa} className="space-y-4">
              <div className="text-center py-2">
                <div className="text-2xl mb-2">📱</div>
                <p className="text-sm text-[var(--color-text-2)]">
                  Ingresa el código de tu aplicación autenticadora
                </p>
              </div>
              <div>
                <label className="block text-xs font-medium text-[var(--color-text-2)] mb-1.5 text-center">
                  Código de 6 dígitos
                </label>
                <input
                  type="text"
                  inputMode="numeric"
                  pattern="[0-9]{6}"
                  maxLength={6}
                  value={totp}
                  onChange={(e) => setTotp(e.target.value.replace(/\D/g, ''))}
                  autoFocus
                  required
                  placeholder="000000"
                  className="
                    w-full px-3 py-3 text-2xl text-center font-mono tracking-[0.5em] rounded-lg
                    border border-[var(--color-border)] bg-[var(--color-surface-2)]
                    text-[var(--color-text)] placeholder:text-[var(--color-text-4)]
                    focus:outline-none focus:border-[var(--color-accent)] focus:ring-2 focus:ring-[var(--color-accent-dim)]
                    transition-colors
                  "
                />
              </div>
              <Button type="submit" loading={loading} disabled={totp.length !== 6} className="w-full">
                Verificar
              </Button>
              <button
                type="button"
                onClick={() => { setStep('credentials'); setError(null) }}
                className="w-full text-xs text-[var(--color-text-4)] hover:text-[var(--color-text-2)] cursor-pointer"
              >
                ← Volver
              </button>
            </form>
          )}
        </div>

        {/* Security note */}
        <p className="text-center text-[10px] text-[var(--color-text-4)] mt-4">
          🔒 Conexión cifrada · JWT + MFA obligatorio · Cumple HIPAA/PR
        </p>
      </div>
    </div>
  )
}

function StepDot({ active, done, label }: { active: boolean; done: boolean; label: string }) {
  return (
    <div className={`
      w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0
      ${done   ? 'bg-[var(--color-success)] text-white' :
        active ? 'bg-[var(--color-accent)] text-white' :
                 'bg-[var(--color-surface-3)] text-[var(--color-text-4)]'}
    `}>
      {done ? '✓' : label}
    </div>
  )
}
