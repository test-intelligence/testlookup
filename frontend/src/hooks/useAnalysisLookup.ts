import useSWR from 'swr'
import {
  aiFeedbackService,
  type AnalysisLookupResponse,
} from '@/services/aiFeedbackService'

/**
 * Latest AI analysis for a (project, test fingerprint) pair — US-2.4.
 *
 * Used by the Failure Analysis "correct classification" dialog to resolve
 * the ``analysis_id`` that the feedback endpoints key on. Pass null/undefined
 * for either argument to suspend the fetch (SWR null-key convention), so the
 * request only fires while the dialog is actually open.
 */
export function useAnalysisLookup(
  projectId: string | null | undefined,
  fingerprint: string | null | undefined,
) {
  const key =
    projectId && fingerprint ? ['analysis-lookup', projectId, fingerprint] : null
  const { data, error, isLoading } = useSWR<AnalysisLookupResponse>(
    key,
    () => aiFeedbackService.lookupAnalysis(projectId as string, fingerprint as string),
  )
  return { lookup: data, isLoading, isError: !!error }
}
