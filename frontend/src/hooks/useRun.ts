import { useQuery } from '@tanstack/react-query'
import { api } from '../services/api'
import { TERMINAL } from '../types/api'

/** Polls a run every 1.5 s until it reaches a terminal status. */
export function useRun(runId: string | null | undefined) {
  return useQuery({
    queryKey: ['run', runId],
    queryFn: () => api.run(runId as string),
    enabled: !!runId,
    refetchInterval: (q) => {
      const s = q.state.data?.status
      return s && TERMINAL.includes(s) ? false : 1500
    },
  })
}
