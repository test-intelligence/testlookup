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
 * The raw ingest-API equivalent of {@link uploadCommand}, for CI runners that
 * `curl` the endpoint instead of installing the CLI. Mirrors the documented
 * `POST /api/v1/ingest/file` multipart contract (see `GETTING_STARTED.md` and
 * `backend/app/routers/ingest.py`): `file`, the required `project_id` and
 * `build_number`, and `format`. `$TL_TOKEN` and `<build>` stay placeholders —
 * the bearer token and per-run build label are only known to the caller. The
 * project id is spliced in when the guide is scoped to a concrete project,
 * matching {@link uploadCommand}.
 */
export function ingestApiCommand(projectId?: string): string {
  const p = projectId && projectId.trim() ? projectId : '<project-id>'
  return (
    `curl -X POST http://localhost:8000/api/v1/ingest/file ` +
    `-H "Authorization: Bearer $TL_TOKEN" ` +
    `-F file=@results.xml -F project_id=${p} -F build_number=<build> -F format=auto`
  )
}

/** Build the three first-run steps, scoping the upload command to `projectId`. */
export function buildSteps(projectId?: string): Step[] {
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
      apiCommand: ingestApiCommand(projectId),
    },
    {
      n: 3,
      title: 'Then explore the intelligence',
      body: 'Once a run lands, dig into clustered failures, the flaky coach, and the release-risk gate.',
    },
  ]
}
