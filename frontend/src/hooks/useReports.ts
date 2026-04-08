import { useQuery } from '@tanstack/react-query'
import { reportsApi } from '../api/endpoints'

export const REPORTS_KEY = 'reports'

export function useBalanceSheet(client_id: string, as_of_date?: string) {
  return useQuery({
    queryKey:  [REPORTS_KEY, 'balance-sheet', client_id, as_of_date],
    queryFn:   () => reportsApi.balanceSheet(client_id, as_of_date),
    enabled:   Boolean(client_id),
    staleTime: 5 * 60_000, // 5 min
  })
}

export function useIncomeStatement(client_id: string, period_start: string, period_end: string) {
  return useQuery({
    queryKey:  [REPORTS_KEY, 'income-statement', client_id, period_start, period_end],
    queryFn:   () => reportsApi.incomeStatement(client_id, period_start, period_end),
    enabled:   Boolean(client_id && period_start && period_end),
    staleTime: 5 * 60_000,
  })
}

export function useCashFlow(client_id: string, period_start: string, period_end: string) {
  return useQuery({
    queryKey:  [REPORTS_KEY, 'cash-flow', client_id, period_start, period_end],
    queryFn:   () => reportsApi.cashFlow(client_id, period_start, period_end),
    enabled:   Boolean(client_id && period_start && period_end),
    staleTime: 5 * 60_000,
  })
}

export function useIvuSummary(client_id: string, period: string) {
  return useQuery({
    queryKey:  [REPORTS_KEY, 'ivu-summary', client_id, period],
    queryFn:   () => reportsApi.ivuSummary(client_id, period),
    enabled:   Boolean(client_id && period),
    staleTime: 60_000,
  })
}

export function useSystemSummary() {
  return useQuery({
    queryKey:     [REPORTS_KEY, 'summary'],
    queryFn:      reportsApi.summary,
    refetchInterval: 30_000,
  })
}

export function useConfidenceDistribution() {
  return useQuery({
    queryKey:     [REPORTS_KEY, 'confidence'],
    queryFn:      reportsApi.confidenceDistribution,
    refetchInterval: 60_000,
  })
}
