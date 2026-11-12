import { createContext, useContext } from 'react'
import type { PipelineStatus } from './api'

/**
 * The pipeline's stage states (G12), fetched once by PipelinePage and read by
 * every stage tab, so switching tabs does not refetch and every tab agrees.
 */
export type PipelineStatusValue = {
  status: PipelineStatus | null
  error: string | null
  reload: () => void
}

export const PipelineStatusContext = createContext<PipelineStatusValue>({
  status: null,
  error: null,
  reload: () => {},
})

export function usePipelineStatus(): PipelineStatusValue {
  return useContext(PipelineStatusContext)
}
