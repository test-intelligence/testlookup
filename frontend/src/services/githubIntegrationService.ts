import { deleteData, getData, postData, putData } from './http'

export interface GitHubIntegrationRead {
  id: string
  project_id: string
  enabled: boolean
  repo_owner: string
  repo_name: string
  api_base_url: string
  has_pat: boolean
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
}

export interface GitHubConnectionTestResponse {
  success: boolean
  status_code: number | null
  message: string
  repo_html_url: string | null
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
