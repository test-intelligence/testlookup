import useSWR from 'swr'
import {
  clusterEpicService,
  defectJiraService,
  type ClusterEpicPreview,
  type ClusterEpicsStatus,
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

/** The SWR key of a project's cluster-epic status: one request per project, shared by every cluster row. */
export const clusterEpicsKey = (projectId: string) => ['cluster-epics', projectId] as const

/**
 * STRAT-03: the project's epic opt-in, Jira availability and stored links, asked ONCE per project (every
 * cluster row reads the same key and SWR dedupes them). Null key, nothing asked, without a real project.
 */
export function useClusterEpics(projectId: string | null | undefined) {
  const { data, error, isLoading, mutate } = useSWR<ClusterEpicsStatus>(
    projectId ? clusterEpicsKey(projectId) : null,
    () => clusterEpicService.status(projectId as string),
    { revalidateOnFocus: false },
  )
  return { status: data, isLoading, isError: !!error, mutate }
}

/** STRAT-03: a cluster epic's prefilled fields; a null key (nothing asked) while the form is closed. */
export function useClusterEpicPreview(projectId: string | null | undefined, membershipKey: string | null | undefined) {
  const { data, error, isLoading } = useSWR<ClusterEpicPreview>(
    projectId && membershipKey ? ['cluster-epic-preview', projectId, membershipKey] : null,
    () => clusterEpicService.preview(projectId as string, membershipKey as string),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )
  return { preview: data, isLoading, error: error as unknown }
}
