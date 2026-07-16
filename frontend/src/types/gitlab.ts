/**
 * Types for the GitLab integration (PMF backlog Epic 3 — UI).
 *
 * These mirror the pinned Epic-3 config contract verbatim — the backend
 * implements the same shapes on a parallel branch. Do not extend without
 * reconciling with `backend` (see docs/PMF_BACKLOG.md, Epic 3).
 *
 * Contract endpoints:
 *   GET/PUT /api/v1/projects/{project_id}/integrations/gitlab      → GitLabConfig
 *   POST    /api/v1/projects/{project_id}/integrations/gitlab/test → GitLabConnectionTest
 */

/** Sticky merge-request summary comment mode (mirrors the GitHub PR mode). */
export type MrCommentMode = 'off' | 'failures_only' | 'always'

/**
 * GitLabConfig — the read shape returned by GET/PUT.
 *
 * The PAT is write-only: it is NEVER returned. `has_token` is the only
 * signal that a token is stored. Defaults are returned even when the
 * integration is unconfigured (disabled, base_url gitlab.com, empty
 * project_path, failures_only, commit_status_enabled true, has_token false).
 */
export interface GitLabConfig {
  enabled: boolean
  base_url: string
  project_path: string
  mr_comment_mode: MrCommentMode
  commit_status_enabled: boolean
  has_token: boolean
  last_error: string | null
  last_error_at: string | null
}

/**
 * GitLabConfigWrite — the PUT payload.
 *
 * `token` is OPTIONAL and write-only:
 *   - omitted / `null`  → leave the stored PAT unchanged
 *   - `""` (empty)      → clear the stored PAT
 *   - a value           → set / rotate the PAT
 */
export interface GitLabConfigWrite {
  enabled: boolean
  base_url: string
  project_path: string
  mr_comment_mode: MrCommentMode
  commit_status_enabled: boolean
  token?: string | null
}

/** POST …/test response. */
export interface GitLabConnectionTest {
  ok: boolean
  detail: string
  project_id_resolved: string | null
}

/** Contract-default config returned by the backend when unconfigured. */
export const DEFAULT_GITLAB_CONFIG: GitLabConfig = {
  enabled: false,
  base_url: 'https://gitlab.com',
  project_path: '',
  mr_comment_mode: 'failures_only',
  commit_status_enabled: true,
  has_token: false,
  last_error: null,
  last_error_at: null,
}
