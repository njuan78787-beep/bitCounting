// =============================================================================
// src/types/api.ts
// Strict TypeScript types mirroring all Bit-Counting API responses.
// No `any`, no `as` casts — everything typed at the boundary.
// =============================================================================

// ─── Auth ─────────────────────────────────────────────────────────────────────

export type Role =
  | 'CLIENT'
  | 'CPA_PARTNER'
  | 'CPA_SENIOR'
  | 'EXIMIA_ADMIN'

export interface AuthTokenResponse {
  access_token: string
  token_type:   'bearer'
  mfa_required: boolean
  mfa_pending:  boolean
}

export interface MfaVerifyResponse {
  access_token:  string
  refresh_token: string
  token_type:    'bearer'
  expires_in:    number
}

export interface RefreshResponse {
  access_token: string
  token_type:   'bearer'
  expires_in:   number
}

export interface MeResponse {
  user_id:     string
  username:    string
  role:        Role
  client_id:   string | null
  permissions: string[]
}

// ─── Transactions ─────────────────────────────────────────────────────────────

export type TransactionStatus =
  | 'pending'
  | 'processed'
  | 'paused'
  | 'rejected'

export interface TransactionResponse {
  transaction_id: string
  client_id:      string
  vendor:         string | null
  amount:         string          // Decimal serialized as string
  date:           string | null   // ISO date YYYY-MM-DD
  status:         TransactionStatus
  account_code:   string | null
  account_name:   string | null
  confidence:     string | null   // Decimal as string, 0-1
  created_at:     string          // ISO datetime
  updated_at:     string | null
}

export interface TransactionListResponse {
  items:     TransactionResponse[]
  total:     number
  page:      number
  page_size: number
}

export interface JournalEntryResponse {
  entry_id:             string
  transaction_id:       string
  account_code:         string
  account_name:         string
  entry_type:           'debit' | 'credit'
  amount:               string
  rule_ref:             string
  reasoning:            string
  contra_account_code:  string
  contra_account_name:  string
  created_at:           string
}

export interface TaxAnalysisResponse {
  transaction_id:   string
  tax_type:         string
  tax_liability:    string
  taxable_base:     string
  form_id:          string
  rule_ref:         string
  rate_version:     string
  calc_hash:        string
  period_from:      string
  period_to:        string
  exemptions_applied: string[]
}

// ─── Reports ──────────────────────────────────────────────────────────────────

export interface CurrentAssets {
  cash_and_equivalents: string
  accounts_receivable:  string
  inventory:            string
  prepaid_expenses:     string
  total_current_assets: string
}

export interface NonCurrentAssets {
  property_plant_equipment:  string
  accumulated_depreciation:  string
  intangible_assets:         string
  total_non_current_assets:  string
}

export interface CurrentLiabilities {
  accounts_payable:              string
  accrued_expenses:              string
  ivu_payable:                   string
  current_portion_long_term_debt: string
  total_current_liabilities:     string
}

export interface LongTermLiabilities {
  long_term_debt:              string
  deferred_tax_liability:      string
  total_long_term_liabilities: string
}

export interface Equity {
  common_stock:     string
  retained_earnings: string
  total_equity:     string
}

export interface BalanceSheetResponse {
  client_id:        string
  as_of_date:       string
  assets:           { current_assets: CurrentAssets; non_current_assets: NonCurrentAssets }
  liabilities:      { current_liabilities: CurrentLiabilities; long_term_liabilities: LongTermLiabilities }
  equity:           Equity
  total_assets:     string
  total_liabilities: string
  total_equity:     string
  generated_at:     string
}

export interface IncomeStatementResponse {
  client_id:              string
  period_start:           string
  period_end:             string
  revenue:                Record<string, string>
  cost_of_goods_sold:     Record<string, string>
  gross_profit:           string
  operating_expenses:     Record<string, string>
  operating_income:       string
  other_income_expense:   Record<string, string>
  net_income:             string
  generated_at:           string
}

export interface CashFlowResponse {
  client_id:             string
  period_start:          string
  period_end:            string
  operating_activities:  Record<string, unknown>
  investing_activities:  Record<string, string>
  financing_activities:  Record<string, string>
  net_change_in_cash:    string
  beginning_cash:        string
  ending_cash:           string
  generated_at:          string
}

export interface IVUSummaryResponse {
  client_id:          string
  period:             string
  ivu_collected:      string
  ivu_remitted:       string
  ivu_balance:        string
  ivu_rate_municipal: string
  ivu_rate_state:     string
  transactions_count: number
  form_sc2915_ready:  boolean
  generated_at:       string
}

export interface SummaryResponse {
  total_documents_submitted:    number
  total_documents_processed:    number
  active_pauses:                number
  resolved_pauses:              number
  total_pauses:                 number
  total_orchestrator_decisions: number
  decisions_requiring_cpa_review: number
  generated_at:                 string
}

export interface ConfidenceDistributionResponse {
  total_documents_with_confidence: number
  average_confidence:  number
  min_confidence:      number
  max_confidence:      number
  distribution:        Record<'very_high' | 'high' | 'medium' | 'low' | 'very_low', number>
  distribution_pct:    Record<'very_high' | 'high' | 'medium' | 'low' | 'very_low', number>
  generated_at:        string
}

// ─── Centinela / Pauses ───────────────────────────────────────────────────────

export type PauseStatus = 'ACTIVE' | 'RESOLVED'
export type PauseSeverity = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'

export interface PauseResponse {
  pause_id:         string
  transaction_id:   string
  client_id:        string
  reason:           string
  severity:         PauseSeverity
  status:           PauseStatus
  confidence:       number
  sla_hours:        number
  sla_deadline:     string
  created_at:       string
  resolved_at:      string | null
  resolution_note:  string | null
  agent_analysis:   string | null
}

export interface PauseListResponse {
  items:       PauseResponse[]
  total:       number
  page:        number
  page_size:   number
}

export interface ReleaseResponse {
  pause_id:        string
  status:          PauseStatus
  resolution_note: string
  resolved_at:     string
  resolved_by:     string
}

// ─── CPA Dashboard ────────────────────────────────────────────────────────────

export interface ReviewQueueItem {
  queue_id:         string
  transaction_id:   string
  client_id:        string
  pause_id:         string | null
  severity:         PauseSeverity
  reason:           string
  sla_deadline:     string
  sla_hours_left:   number
  status:           'PENDING' | 'IN_REVIEW' | 'APPROVED' | 'REJECTED'
  created_at:       string
  pre_analysis:     string | null
}

export interface ReviewQueueListResponse {
  items:     ReviewQueueItem[]
  total:     number
  page:      number
  page_size: number
}

export interface CpaInstruction {
  instruction_id: string
  client_id:      string
  content:        string
  preview:        string | null
  status:         'DRAFT' | 'CONFIRMED' | 'APPLIED'
  created_at:     string
  confirmed_at:   string | null
}

export interface CpaMetricsResponse {
  avg_response_time_hours: number
  sla_compliance_pct:      number
  total_reviewed:          number
  pending_count:           number
  approved_count:          number
  rejected_count:          number
  period_days:             number
}

// ─── Admin / Normative Updates ────────────────────────────────────────────────

export interface NormativeUpdate {
  update_id:              string
  source_name:            string
  change_type:            string
  impact_level:           string
  sla_hours:              number
  sla_deadline:           string
  description:            string
  status:                 string
  detected_at:            string
  requires_human_review:  boolean
}

// ─── WebSocket messages ───────────────────────────────────────────────────────

export type WsEventType =
  | 'transaction.updated'
  | 'pause.created'
  | 'pause.resolved'
  | 'orchestrator.decision'
  | 'normative.detected'
  | 'ping'

export interface WsMessage {
  event:     WsEventType
  payload:   WsPayload
  timestamp: string
}

export type WsPayload =
  | TransactionUpdatedPayload
  | PauseCreatedPayload
  | PauseResolvedPayload
  | OrchestratorDecisionPayload
  | NormativeDetectedPayload
  | PingPayload

export interface TransactionUpdatedPayload {
  transaction_id: string
  status:         TransactionStatus
  confidence:     number | null
}

export interface PauseCreatedPayload {
  pause_id:       string
  transaction_id: string
  severity:       PauseSeverity
  reason:         string
}

export interface PauseResolvedPayload {
  pause_id:   string
  resolved_by: string
}

export interface OrchestratorDecisionPayload {
  decision_id: string
  agent:       string
  action:      string
  summary:     string
}

export interface NormativeDetectedPayload {
  update_id:   string
  source_name: string
  description: string
}

export interface PingPayload {
  server_time: string
}

// ─── API Error ────────────────────────────────────────────────────────────────

export interface ApiError {
  detail: string | Array<{ msg: string; loc: string[] }>
}
