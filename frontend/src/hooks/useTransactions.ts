import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { transactionsApi, type TransactionListParams } from '../api/endpoints'
import { useWsStore } from '../store/wsStore'
import { useEffect } from 'react'
import type { TransactionUpdatedPayload, WsMessage } from '../types/api'

export const TRANSACTIONS_KEY = 'transactions'

export function useTransactions(params: TransactionListParams = {}) {
  const qc = useQueryClient()

  // Invalidate on WS update
  useEffect(() => {
    return useWsStore.getState().subscribe('transaction.updated', (msg: WsMessage) => {
      const payload = msg.payload as TransactionUpdatedPayload
      qc.invalidateQueries({ queryKey: [TRANSACTIONS_KEY] })
      qc.invalidateQueries({ queryKey: [TRANSACTIONS_KEY, payload.transaction_id] })
    })
  }, [qc])

  return useQuery({
    queryKey: [TRANSACTIONS_KEY, params],
    queryFn:  () => transactionsApi.list(params),
  })
}

export function useTransaction(id: string) {
  return useQuery({
    queryKey: [TRANSACTIONS_KEY, id],
    queryFn:  () => transactionsApi.get(id),
    enabled:  Boolean(id),
  })
}

export function useUploadDocument() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ raw_text, client_id, source_format }: {
      raw_text: string; client_id: string; source_format?: string
    }) => transactionsApi.upload(raw_text, client_id, source_format),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [TRANSACTIONS_KEY] })
    },
  })
}

export function useCreateTransaction() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: transactionsApi.create,
    onSuccess:  () => qc.invalidateQueries({ queryKey: [TRANSACTIONS_KEY] }),
  })
}

export function useJournalEntries(transactionId: string) {
  return useQuery({
    queryKey: [TRANSACTIONS_KEY, transactionId, 'journal-entries'],
    queryFn:  () => transactionsApi.journalEntries(transactionId),
    enabled:  Boolean(transactionId),
  })
}

export function useTaxAnalysis(transactionId: string) {
  return useQuery({
    queryKey: [TRANSACTIONS_KEY, transactionId, 'tax-analysis'],
    queryFn:  () => transactionsApi.taxAnalysis(transactionId),
    enabled:  Boolean(transactionId),
  })
}
