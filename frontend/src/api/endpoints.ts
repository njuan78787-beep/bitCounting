// =============================================================================
// src/api/endpoints.ts
// Typed API endpoint functions — one function per API call.
// =============================================================================

import { api } from './client'
import type {
  AuthTokenResponse,
  MfaVerifyResponse,
  MeResponse,
  TransactionResponse,
  TransactionListResponse,
  JournalEntryResponse,
  TaxAnalysisResponse,
  BalanceSheetResponse,
  IncomeStatementResponse,
  CashFlowResponse,
  IVUSummaryResponse,
  SummaryResponse,
  ConfidenceDistributionResponse,
  PauseResponse,
  PauseListResponse,
  ReleaseResponse,
  ReviewQueueListResponse,
  CpaInstruction,
  CpaMetricsResponse,
  NormativeUpdate,
} from '../types/api'

// ─── Auth ─────────────────────────────────────────────────────────────────────

export const authApi = {
  login: (username: string, password: string) =>
    api.post<AuthTokenResponse>('/auth/token', { username, password }, { skipAuth: true }),

  mfaVerify: (tempToken: string, totp_code: string) =>
    api.post<MfaVerifyResponse>('/auth/mfa/verify', { totp_code }, {
      skipAuth: true,
      headers: { Authorization: `Bearer ${tempToken}` },
    }),

  refresh: (refresh_token: string) =>
    api.post<{ access_token: string; token_type: string; expires_in: number }>(
      '/auth/refresh', { refresh_token }, { skipAuth: true }
    ),

  logout: (refresh_token: string) =>
    api.post<void>('/auth/logout', { refresh_token }),

  me: () => api.get<MeResponse>('/auth/me'),
}

// ─── Transactions ─────────────────────────────────────────────────────────────

export interface TransactionListParams {
  client_id?:  string
  status?:     string
  date_from?:  string
  date_to?:    string
  page?:       number
  page_size?:  number
}

export const transactionsApi = {
  list: (params: TransactionListParams = {}) => {
    const q = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined) q.set(k, String(v)) })
    const qs = q.toString() ? `?${q.toString()}` : ''
    return api.get<TransactionListResponse>(`/transactions${qs}`)
  },

  get: (id: string) =>
    api.get<TransactionResponse>(`/transactions/${id}`),

  upload: (raw_text: string, client_id: string, source_format = 'UNKNOWN') =>
    api.post<TransactionResponse>('/transactions/upload', { raw_text, client_id, source_format }),

  create: (data: { client_id: string; vendor: string; amount: string; date: string }) =>
    api.post<TransactionResponse>('/transactions', data),

  journalEntries: (id: string) =>
    api.get<JournalEntryResponse[]>(`/transactions/${id}/journal-entries`),

  taxAnalysis: (id: string) =>
    api.get<TaxAnalysisResponse>(`/transactions/${id}/tax-analysis`),
}

// ─── Reports ──────────────────────────────────────────────────────────────────

export const reportsApi = {
  balanceSheet: (client_id: string, as_of_date?: string) => {
    const q = new URLSearchParams({ client_id })
    if (as_of_date) q.set('as_of_date', as_of_date)
    return api.get<BalanceSheetResponse>(`/reports/balance-sheet?${q}`)
  },

  incomeStatement: (client_id: string, period_start: string, period_end: string) => {
    const q = new URLSearchParams({ client_id, period_start, period_end })
    return api.get<IncomeStatementResponse>(`/reports/income-statement?${q}`)
  },

  cashFlow: (client_id: string, period_start: string, period_end: string) => {
    const q = new URLSearchParams({ client_id, period_start, period_end })
    return api.get<CashFlowResponse>(`/reports/cash-flow?${q}`)
  },

  ivuSummary: (client_id: string, period: string) => {
    const q = new URLSearchParams({ client_id, period })
    return api.get<IVUSummaryResponse>(`/reports/ivu-summary?${q}`)
  },

  summary: () =>
    api.get<SummaryResponse>('/reports/summary'),

  confidenceDistribution: () =>
    api.get<ConfidenceDistributionResponse>('/reports/confidence'),
}

// ─── Centinela ────────────────────────────────────────────────────────────────

export interface PauseListParams {
  client_id?: string
  status?:    string
  severity?:  string
  page?:      number
  page_size?: number
}

export const centinelaApi = {
  listPauses: (params: PauseListParams = {}) => {
    const q = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined) q.set(k, String(v)) })
    const qs = q.toString() ? `?${q.toString()}` : ''
    return api.get<PauseListResponse>(`/centinela/pauses${qs}`)
  },

  getPause: (id: string) =>
    api.get<PauseResponse>(`/centinela/pauses/${id}`),

  releasePause: (id: string, resolution_note: string) =>
    api.post<ReleaseResponse>(`/centinela/pauses/${id}/release`, { resolution_note }),
}

// ─── CPA Dashboard ────────────────────────────────────────────────────────────

export const cpaApi = {
  reviewQueue: (page = 1, page_size = 20) => {
    const q = new URLSearchParams({ page: String(page), page_size: String(page_size) })
    return api.get<ReviewQueueListResponse>(`/cpa/review-queue?${q}`)
  },

  approveItem: (queue_id: string, decision: string, notes?: string) =>
    api.post<{ queue_id: string; status: string }>(`/cpa/review-queue/${queue_id}/approve`, {
      decision, notes,
    }),

  createInstruction: (client_id: string, content: string) =>
    api.post<CpaInstruction>('/cpa/instructions', { client_id, content }),

  previewInstruction: (instruction_id: string) =>
    api.get<{ preview: string }>(`/cpa/instructions/${instruction_id}/preview`),

  confirmInstruction: (instruction_id: string) =>
    api.post<CpaInstruction>(`/cpa/instructions/${instruction_id}/confirm`),

  metrics: () =>
    api.get<CpaMetricsResponse>('/cpa/metrics'),

  listPauses: (page = 1) =>
    api.get<PauseListResponse>(`/cpa/pauses?page=${page}`),
}

// ─── Admin ────────────────────────────────────────────────────────────────────

export const adminApi = {
  listNormativeUpdates: () =>
    api.get<NormativeUpdate[]>('/admin/normative-updates'),

  getUpdate: (id: string) =>
    api.get<NormativeUpdate>(`/admin/normative-updates/${id}`),

  activateUpdate: (
    id: string,
    data: {
      cpa_license:        string
      digital_signature:  string
      effective_date:     string
      interpretation_note: string
    },
  ) => api.post<Record<string, string>>(`/admin/normative-updates/${id}/activate`, data),
}
