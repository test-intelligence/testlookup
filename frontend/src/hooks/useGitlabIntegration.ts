import useSWR from 'swr'
import { gitlabIntegrationService } from '@/services/gitlabIntegrationService'
import type { GitLabConfig, GitLabConnectionTest } from '@/types/gitlab'

/**
 * Per-project GitLab integration config (Settings → GitLab card, Epic 3).
 *
 * SWR read of the pinned config contract. The backend returns contract
 * defaults even when unconfigured, so this never 404s for a valid project;
 * the key is null (no fetch) in All-Projects mode.
 */
export function useGitlabIntegration(projectId: string | null) {
  // Narrow once into a const so the closure keeps the non-null type — no
  // unreachable `?? ''` fallback in the fetcher.
  const id = projectId
  return useSWR<GitLabConfig>(
    id === null ? null : `/api/v1/projects/${id}/integrations/gitlab`,
    id === null ? null : () => gitlabIntegrationService.get(id),
    { revalidateOnFocus: false },
  )
}

/**
 * Connection-test mutation. Not an SWR resource (it is an imperative POST
 * with side-effecting semantics), so it is exposed as a thin async helper
 * that pages call on button press.
 */
export function testGitlabConnection(projectId: string): Promise<GitLabConnectionTest> {
  return gitlabIntegrationService.test(projectId)
}
