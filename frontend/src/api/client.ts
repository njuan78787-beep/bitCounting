// =============================================================================
// src/api/client.ts
// Typed fetch-based API client with JWT auth, error handling, and retry.
// Never fails silently — always surfaces errors to the caller.
// =============================================================================

import type { ApiError } from '../types/api'

const BASE_URL = '/api/v1'

// Token storage (memory-only for access token, sessionStorage for refresh)
let _accessToken: string | null = null

export function setAccessToken(token: string): void {
  _accessToken = token
}

export function clearTokens(): void {
  _accessToken = null
  sessionStorage.removeItem('refresh_token')
}

export function getRefreshToken(): string | null {
  return sessionStorage.getItem('refresh_token')
}

export function setRefreshToken(token: string): void {
  sessionStorage.setItem('refresh_token', token)
}

// ─── Error class ──────────────────────────────────────────────────────────────

export class ApiRequestError extends Error {
  readonly status: number
  readonly detail: string
  readonly raw:    ApiError

  constructor(status: number, detail: string, raw: ApiError) {
    super(detail)
    this.name   = 'ApiRequestError'
    this.status = status
    this.detail = detail
    this.raw    = raw
  }
}

function extractDetail(error: ApiError): string {
  if (typeof error.detail === 'string') return error.detail
  if (Array.isArray(error.detail)) {
    return error.detail.map((e) => `${e.loc.join('.')}: ${e.msg}`).join('; ')
  }
  return 'Error desconocido'
}

// ─── Core fetch ───────────────────────────────────────────────────────────────

interface FetchOptions extends Omit<RequestInit, 'body'> {
  body?:        unknown
  skipAuth?:    boolean
  skipRefresh?: boolean
}

async function apiFetch<T>(path: string, opts: FetchOptions = {}): Promise<T> {
  const { body, skipAuth = false, skipRefresh = false, ...rest } = opts

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(rest.headers as Record<string, string> | undefined),
  }

  if (!skipAuth && _accessToken) {
    headers['Authorization'] = `Bearer ${_accessToken}`
  }

  const res = await fetch(`${BASE_URL}${path}`, {
    ...rest,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

  // 401 → try token refresh once
  if (res.status === 401 && !skipRefresh && !skipAuth) {
    const refreshed = await tryRefresh()
    if (refreshed) {
      headers['Authorization'] = `Bearer ${_accessToken!}`
      const retry = await fetch(`${BASE_URL}${path}`, {
        ...rest,
        headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
      })
      return handleResponse<T>(retry)
    }
  }

  return handleResponse<T>(res)
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (res.ok) {
    if (res.status === 204) return undefined as T
    return res.json() as Promise<T>
  }

  let errorBody: ApiError
  try {
    errorBody = (await res.json()) as ApiError
  } catch {
    errorBody = { detail: `HTTP ${res.status}` }
  }
  throw new ApiRequestError(res.status, extractDetail(errorBody), errorBody)
}

async function tryRefresh(): Promise<boolean> {
  const rt = getRefreshToken()
  if (!rt) return false
  try {
    const res = await fetch(`${BASE_URL}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: rt }),
    })
    if (!res.ok) return false
    const data = (await res.json()) as { access_token: string }
    setAccessToken(data.access_token)
    return true
  } catch {
    return false
  }
}

// ─── Public helpers ───────────────────────────────────────────────────────────

export const api = {
  get:    <T>(path: string, opts?: FetchOptions)                => apiFetch<T>(path, { method: 'GET',    ...opts }),
  post:   <T>(path: string, body?: unknown, opts?: FetchOptions) => apiFetch<T>(path, { method: 'POST',   body, ...opts }),
  put:    <T>(path: string, body?: unknown, opts?: FetchOptions) => apiFetch<T>(path, { method: 'PUT',    body, ...opts }),
  delete: <T>(path: string, opts?: FetchOptions)                => apiFetch<T>(path, { method: 'DELETE', ...opts }),
}
