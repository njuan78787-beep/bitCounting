import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { centinelaApi, type PauseListParams } from '../api/endpoints'
import { useWsStore } from '../store/wsStore'
import { useEffect } from 'react'
import type { WsMessage } from '../types/api'

export const PAUSES_KEY = 'pauses'

export function usePauses(params: PauseListParams = {}) {
  const qc = useQueryClient()

  useEffect(() => {
    const unsub1 = useWsStore.getState().subscribe('pause.created', (_msg: WsMessage) => {
      qc.invalidateQueries({ queryKey: [PAUSES_KEY] })
    })
    const unsub2 = useWsStore.getState().subscribe('pause.resolved', (_msg: WsMessage) => {
      qc.invalidateQueries({ queryKey: [PAUSES_KEY] })
    })
    return () => { unsub1(); unsub2() }
  }, [qc])

  return useQuery({
    queryKey: [PAUSES_KEY, params],
    queryFn:  () => centinelaApi.listPauses(params),
    refetchInterval: 30_000,
  })
}

export function usePause(id: string) {
  return useQuery({
    queryKey: [PAUSES_KEY, id],
    queryFn:  () => centinelaApi.getPause(id),
    enabled:  Boolean(id),
  })
}

export function useReleasePause() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, resolution_note }: { id: string; resolution_note: string }) =>
      centinelaApi.releasePause(id, resolution_note),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [PAUSES_KEY] })
    },
  })
}
