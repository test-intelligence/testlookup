import { deleteData, getData, postData, putData } from './http'

/** Sticky PR summary comment mode (PMF US-4.1). */
export type PrCommentMode = 'off' | 'failures_only' | 'always'

/** W19 INT-06: a personal access token, or the deployment's GitHub App. */
export type GitHubAuthKind = 'pat' | 'app'

export interface GitHubIntegrationRead {
  id: string
  project_id: string
  enabled: boolean
  repo_owner: string
  repo_name: string
  api_base_url: string
  has_pat: boolean
  pr_comment_mode: PrCommentMode
  /** W19 INT-06 (an older server omits them). */
  auth_kind?: GitHubAuthKind
  installation_id?: number | null
  last_posted_at: string | null
  last_error: string | null
  last_error_at: string | null
  created_at: string
  updated_at: string
}

export interface GitHubIntegrationWrite {
  enabled: boolean
  repo_owner: string
  repo_name: string
  api_base_url: string
  pat?: string | null
  pr_comment_mode: PrCommentMode
  auth_kind?: GitHubAuthKind
}

export interface GitHubConnectionTestResponse {
  success: boolean
  status_code: number | null
  message: string
  repo_html_url: string | null
  /** `pat` | `app_installation` (`app` when the App path failed). */
  auth_kind?: string | null
}

export const githubIntegrationService = {
  get: (projectId: string) =>
    getData<GitHubIntegrationRead>(
      `/api/v1/projects/${projectId}/github-integration`,
    ),

  upsert: (projectId: string, payload: GitHubIntegrationWrite) =>
    putData<GitHubIntegrationRead>(
      `/api/v1/projects/${projectId}/github-integration`,
      payload,
    ),

  test: (projectId: string) =>
    postData<GitHubConnectionTestResponse>(
      `/api/v1/projects/${projectId}/github-integration/test`,
      {},
    ),

  remove: (projectId: string) =>
    deleteData(`/api/v1/projects/${projectId}/github-integration`),
}

// ── W19 INT-06: the deployment's GitHub App ──────────────────────────────────

export interface GitHubAppSettingsRead {
  app_id: string | null
  slug: string | null
  private_key_set: boolean
  webhook_secret_set: boolean
  configured: boolean
}

export interface GitHubAppSettingsWrite {
  app_id: string
  slug: string
  /** null keeps the stored value, '' clears it. */
  private_key: string | null
  webhook_secret: string | null
}

export const githubAppService = {
  getSettings: () => getData<GitHubAppSettingsRead>('/api/v1/settings/github-app', { suppressToast: true }),
  putSettings: (payload: GitHubAppSettingsWrite) =>
    putData<GitHubAppSettingsRead>('/api/v1/settings/github-app', payload, { suppressToast: true }),
  installUrl: (projectId: string) =>
    getData<{ url: string; expires_in: number }>('/api/v1/github/app/install-url', {
      params: { project_id: projectId },
      suppressToast: true,
    }),
  setupCallback: (installationId: number, state: string) =>
    postData<{ project_id: string; installation_id: number; auth_kind: GitHubAuthKind }>(
      '/api/v1/github/app/setup-callback',
      { installation_id: installationId, state },
      { suppressToast: true },
    ),
}
