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
  return useSWR<GitLabConfig>(
    projectId ? `/api/v1/projects/${projectId}/integrations/gitlab` : null,
    () => gitlabIntegrationService.get(projectId ?? ''),
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
