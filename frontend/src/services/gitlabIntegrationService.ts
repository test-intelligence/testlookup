import type {
  GitLabConfig,
  GitLabConfigWrite,
  GitLabConnectionTest,
} from '@/types/gitlab'
import { getData, postData, putData } from './http'

/**
 * GitLab integration (PMF backlog Epic 3) — per-project config + connection
 * test. Pinned config contract; PUT is QA_LEAD+ and carries an OPTIONAL
 * write-only `token` field (omit/null = unchanged, "" = clear, value = rotate).
 *
 * Rides the shared Axios base in `services/api.ts` via the `http` helpers.
 */
export const gitlabIntegrationService = {
  get: (projectId: string) =>
    getData<GitLabConfig>(`/api/v1/projects/${projectId}/integrations/gitlab`),

  update: (projectId: string, payload: GitLabConfigWrite) =>
    putData<GitLabConfig, GitLabConfigWrite>(
      `/api/v1/projects/${projectId}/integrations/gitlab`,
      payload,
    ),

  test: (projectId: string) =>
    postData<GitLabConnectionTest>(
      `/api/v1/projects/${projectId}/integrations/gitlab/test`,
      {},
    ),
}
