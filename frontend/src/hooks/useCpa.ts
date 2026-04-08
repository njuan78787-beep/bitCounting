import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { cpaApi } from '../api/endpoints'
import { useWsStore } from '../store/wsStore'
import { useEffect } from 'react'
import type { WsMessage } from '../types/api'

export const REVIEW_QUEUE_KEY = 'cpa-review-queue'
export const INSTRUCTIONS_KEY = 'cpa-instructions'
export const CPA_METRICS_KEY  = 'cpa-metrics'

export function useReviewQueue(page = 1, page_size = 20) {
  const qc = useQueryClient()

  useEffect(() => {
    const unsub = useWsStore.getState().subscribe('pause.created', (_msg: WsMessage) => {
      qc.invalidateQueries({ queryKey: [REVIEW_QUEUE_KEY] })
    })
    return unsub
  }, [qc])

  return useQuery({
    queryKey: [REVIEW_QUEUE_KEY, page, page_size],
    queryFn:  () => cpaApi.reviewQueue(page, page_size),
    refetchInterval: 20_000,
  })
}

export function useApproveItem() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ queue_id, decision, notes }: {
      queue_id: string; decision: string; notes?: string
    }) => cpaApi.approveItem(queue_id, decision, notes),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [REVIEW_QUEUE_KEY] })
      qc.invalidateQueries({ queryKey: [CPA_METRICS_KEY] })
    },
  })
}

export function useCreateInstruction() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ client_id, content }: { client_id: string; content: string }) =>
      cpaApi.createInstruction(client_id, content),
    onSuccess: () => qc.invalidateQueries({ queryKey: [INSTRUCTIONS_KEY] }),
  })
}

export function usePreviewInstruction() {
  return useMutation({
    mutationFn: (instruction_id: string) => cpaApi.previewInstruction(instruction_id),
  })
}

export function useConfirmInstruction() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (instruction_id: string) => cpaApi.confirmInstruction(instruction_id),
    onSuccess: () => qc.invalidateQueries({ queryKey: [INSTRUCTIONS_KEY] }),
  })
}

export function useCpaMetrics() {
  return useQuery({
    queryKey:     [CPA_METRICS_KEY],
    queryFn:      cpaApi.metrics,
    refetchInterval: 60_000,
  })
}
