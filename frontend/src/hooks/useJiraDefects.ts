import useSWR from 'swr'
import {
  defectJiraService,
  type JiraDefectMetadata,
  type JiraDefectPreview,
} from '@/services/defectJiraService'

/**
 * Jira picker metadata + availability for the one-click defect dialog
 * (PMF US-6.1). The backend answers gracefully (``available=false`` +
 * ``reason``) when Jira is offline-gated / unconfigured / unreachable, so
 * this hook doubles as the "is the action enabled?" probe. Cached ~5 min
 * server-side; SWR dedupes on top.
 */
export function useJiraDefectMetadata(projectId: string | null | undefined) {
  const key = projectId ? ['jira-defect-metadata', projectId] : null
  const { data, error, isLoading } = useSWR<JiraDefectMetadata>(
    key,
    () => defectJiraService.metadata(projectId as string),
    { revalidateOnFocus: false },
  )
  return { metadata: data, isLoading, isError: !!error }
}

/**
 * Server-side prefilled Jira payload for a failure signature. Null-key
 * suspended until the dialog is actually open (pass null while closed).
 * Exactly one of ``fingerprint`` / ``clusterId`` identifies the failure.
 */
export function useJiraDefectPreview(
  projectId: string | null | undefined,
  fingerprint: string | null | undefined,
  clusterId?: string | null,
) {
  const signature = fingerprint || clusterId
  const key =
    projectId && signature
      ? ['jira-defect-preview', projectId, fingerprint ?? '', clusterId ?? '']
      : null
  const { data, error, isLoading } = useSWR<JiraDefectPreview>(
    key,
    () =>
      defectJiraService.preview(projectId as string, {
        ...(fingerprint ? { fingerprint } : {}),
        ...(clusterId && !fingerprint ? { cluster_id: clusterId } : {}),
      }),
    { revalidateOnFocus: false },
  )
  return { preview: data, isLoading, isError: !!error }
}
