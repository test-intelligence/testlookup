import { ReactNode } from 'react'
import { SUPPORTED_FORMATS, type ReportFormat } from '@/services/reportUploadService'

/**
 * Base localStorage key for the first-run guide's dismissed state. Dismissal is
 * remembered PER dashboard scope (a project id, or the All-Projects sentinel) —
 * the real key is {@link firstRunDismissKey} — not once for the whole browser.
 *
 * The guide is a per-scope empty-state helper: `OverviewPage` shows it only when
 * THIS scope has no runs at all. A single global flag coupled that per-scope
 * show to a browser-wide hide, so dismissing the guide on the first empty
 * project silently suppressed the same first-run help on every genuinely new,
 * still-empty project created later — exactly the self-hoster who most needs it.
 * Scoping the dismissal to the project keeps the two consistent. (These helpers
 * live in this pure module, not in `FirstRunGuide.tsx`, so the component file
 * exports only its component — keeping React Fast Refresh happy.)
 */
export const FIRST_RUN_DISMISS_KEY = 'tl_first_run_guide_dismissed'

/**
 * The per-scope localStorage key for a dashboard scope — a project id, or the
 * All-Projects sentinel. Namespacing under {@link FIRST_RUN_DISMISS_KEY} keeps
 * every scope's dismissal independent.
 */
export function firstRunDismissKey(scopeId: string): string {
  return `${FIRST_RUN_DISMISS_KEY}:${scopeId}`
}

/**
 * Whether the first-run guide has been dismissed for this dashboard scope.
 * Falsy scope (no active project resolved yet) is treated as not-dismissed —
 * the guide only renders once a real scope is in hand, so this never suppresses
 * it. localStorage access is guarded: a privacy-mode / blocked-storage browser
 * reports not-dismissed rather than throwing.
 */
export function isFirstRunGuideDismissed(scopeId: string | null | undefined): boolean {
  if (!scopeId) return false
  try {
    return localStorage.getItem(firstRunDismissKey(scopeId)) === '1'
  } catch {
    return false
  }
}

/** Persist dismissal of the first-run guide for this dashboard scope. */
export function dismissFirstRunGuide(scopeId: string | null | undefined): void {
  if (!scopeId) return
  try {
    localStorage.setItem(firstRunDismissKey(scopeId), '1')
  } catch {
    /* ignore — a blocked-storage browser just re-shows the guide next load */
  }
}

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
  /**
   * An optional copy-paste command that installs the `testlookup` CLI, shown
   * beside a step whose {@link command} invokes it. The CLI is not published to
   * PyPI — it ships in the repo — so a fresh self-hoster who copies the upload
   * command hits `command not found: testlookup` with no next step unless the
   * guide points at the install.
   */
  cliInstall?: string
}

/**
 * Install command for the `testlookup` CLI. The CLI ships in the repo's `cli/`
 * package (name `testlookup-cli`, not on PyPI — see `cli/pyproject.toml`), so it
 * is installed editable from source rather than `pip install testlookup-cli`.
 * This mirrors the documented step in `GETTING_STARTED.md`
 * (`pip install -e /app/cli/` inside the backend container); from a plain repo
 * checkout the path is the repo-root-relative `cli/`.
 */
export const CLI_INSTALL_COMMAND = 'pip install -e cli/'

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
 * Short, prose-friendly display names for every ingest format the backend
 * `/ingest/file` endpoint accepts, keyed by the canonical {@link ReportFormat}
 * value. `'auto'` is the content-detection MODE, not a report format, so it is
 * excluded here (it is the endpoint default, covered by the step-2 body copy).
 *
 * Typed as an exhaustive `Record` over `Exclude<ReportFormat, 'auto'>` on
 * purpose: adding a parser to the canonical `SUPPORTED_FORMATS` registry (and
 * its `ReportFormat` union, in `reportUploadService`) makes this map a compile
 * error until the new format gets a label — so the first-run guide can never
 * again silently under-advertise what the backend actually ingests. The guide
 * historically listed only six of the eleven, telling a NUnit / xUnit / TRX /
 * Robot / Cucumber self-hoster their CI was unsupported when it was not.
 */
export const INGEST_FORMAT_LABELS: Record<Exclude<ReportFormat, 'auto'>, string> = {
  junit: 'JUnit',
  testng: 'TestNG',
  allure: 'Allure',
  cypress: 'Cypress',
  playwright: 'Playwright',
  pytest: 'pytest',
  robot: 'Robot Framework',
  cucumber: 'Cucumber',
  nunit: 'NUnit',
  trx: 'TRX',
  xunit: 'xUnit',
}

/**
 * The advertised-format summary rendered in step 2, derived from the canonical
 * {@link SUPPORTED_FORMATS} registry (minus `'auto'`) so it tracks the backend
 * rather than a hand-maintained copy. Order follows the registry.
 */
export const SUPPORTED_FORMAT_SUMMARY = SUPPORTED_FORMATS
  .filter((f) => f.value !== 'auto')
  .map((f) => INGEST_FORMAT_LABELS[f.value as Exclude<ReportFormat, 'auto'>])
  .join(' / ')

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
      body: `Point your CI at the ingest API (${SUPPORTED_FORMAT_SUMMARY}), or upload a file from the CLI.`,
      command: uploadCommand(projectId),
      cliInstall: CLI_INSTALL_COMMAND,
      apiCommand: ingestApiCommand(projectId, ingestUrl),
    },
    {
      n: 3,
      title: 'Then explore the intelligence',
      body: 'Once a run lands, dig into clustered failures, the flaky coach, and the release-risk gate.',
    },
  ]
}
