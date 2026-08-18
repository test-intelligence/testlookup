import { ReactNode } from 'react'

/** One numbered onboarding step, optionally with a copy-paste command. */
export interface Step {
  n: number
  title: string
  body: ReactNode
  command?: string
  /**
   * An optional second copy-paste command showing the raw ingest API, for CI
   * runners that `curl` the endpoint directly rather than installing the CLI.
   */
  apiCommand?: string
}

/**
 * The CLI `upload` command's `-p` flag takes the project **ID** (see
 * `cli/testlookup_cli/commands/upload.py`). When the guide is scoped to a
 * concrete project we splice that id in so the copy button yields a
 * runnable command; in "All Projects" mode (no single id) we keep the
 * `<project-id>` placeholder. `-b <build>` stays a placeholder — the build
 * label is per-run and only the user knows it.
 */
export function uploadCommand(projectId?: string): string {
  const p = projectId && projectId.trim() ? projectId : '<project-id>'
  return `testlookup upload file results.xml -p ${p} -b <build>`
}

/**
 * The default ingest endpoint used when no deployment origin is resolved (e.g.
 * pure `ingestApiCommand()` calls in tests). Real renders pass the running
 * deployment's URL via {@link buildSteps}, so this only surfaces in local dev.
 */
export const DEFAULT_INGEST_URL = 'http://localhost:8000/api/v1/ingest/file'

/**
 * The raw ingest-API equivalent of {@link uploadCommand}, for CI runners that
 * `curl` the endpoint instead of installing the CLI. Mirrors the documented
 * `POST /api/v1/ingest/file` multipart contract (see `GETTING_STARTED.md` and
 * `backend/app/routers/ingest.py`): `file`, the required `project_id` and
 * `build_number`, and `format`. `$TL_API_KEY` and `<build>` stay placeholders —
 * the credential and per-run build label are only known to the caller. The
 * project id is spliced in when the guide is scoped to a concrete project,
 * matching {@link uploadCommand}.
 *
 * Auth uses the `X-API-Key` header rather than a `Bearer` token: the ingest
 * endpoint accepts either (see `get_api_key_context` in `backend/app/core/deps.py`),
 * but a CI runner needs a long-lived, project-scoped credential — exactly what a
 * project API key is — not the short-lived login-session bearer token from the
 * quickstart. The self-host generates one under Settings → API Keys, and the UI
 * links there right below this command.
 *
 * `ingestUrl` is the absolute endpoint the running UI already reaches its
 * backend at (see {@link backendUrl} in `services/api.ts`). Passing it makes the
 * copy-paste command work on any self-host served behind an ingress, instead of
 * the hardcoded `localhost:8000` that is unreachable off the dev machine.
 */
export function ingestApiCommand(projectId?: string, ingestUrl: string = DEFAULT_INGEST_URL): string {
  const p = projectId && projectId.trim() ? projectId : '<project-id>'
  return (
    `curl -X POST ${ingestUrl} ` +
    `-H "X-API-Key: $TL_API_KEY" ` +
    `-F file=@results.xml -F project_id=${p} -F build_number=<build> -F format=auto`
  )
}

/**
 * Build the three first-run steps, scoping the upload command to `projectId` and
 * the ingest `curl` to the deployment's own `ingestUrl` (defaulting to
 * {@link DEFAULT_INGEST_URL} when the caller does not resolve one).
 */
export function buildSteps(projectId?: string, ingestUrl: string = DEFAULT_INGEST_URL): Step[] {
  return [
    {
      n: 1,
      title: 'Load the demo dataset',
      body: 'Spin up a fully populated instance — sample runs, failures, flaky tests, trends, and a release gate — in a few minutes.',
      command: 'make quickstart',
    },
    {
      n: 2,
      title: 'Or ingest your own test results',
      body: 'Point your CI at the ingest API (JUnit / TestNG / Allure / Cypress / Playwright / pytest), or upload a file from the CLI.',
      command: uploadCommand(projectId),
      apiCommand: ingestApiCommand(projectId, ingestUrl),
    },
    {
      n: 3,
      title: 'Then explore the intelligence',
      body: 'Once a run lands, dig into clustered failures, the flaky coach, and the release-risk gate.',
    },
  ]
}
