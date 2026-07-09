/**
 * CI-context auto-detection for the TestLookup JS/TS SDK (US-4.3b).
 *
 * Detects the CI provider, repository, PR number, actor, and run URL from
 * the standard environment variables each CI system exports, so test runs
 * land in TestLookup already linked to their pull request and CI job.
 *
 * Detection matrix (first match wins — mirrors ``client/ci_context.py`` and
 * the Java/Go SDK ports; keep all copies in sync):
 *
 *   1. GitHub Actions  — GITHUB_ACTIONS=true
 *   2. GitLab CI       — GITLAB_CI=true
 *   3. Jenkins         — JENKINS_URL set
 *   4. Azure DevOps    — TF_BUILD=True
 *   5. CircleCI        — CIRCLECI=true
 *
 * Nothing matches → empty object (never guess). Malformed integers → the
 * field is omitted (never throw). String values are defensively truncated
 * to the backend's length caps (IngestPayload / LiveSessionCreate).
 *
 * Precedence when merging (resolveCiContext): detection <
 * TESTLOOKUP_CI_* env overrides < explicit caller values.
 */

/** Wire-format CI context — keys match the backend contract exactly. */
export interface CiContext {
  ci_provider?: string
  ci_repo?: string
  pr_number?: number
  ci_actor?: string
  ci_run_url?: string
}

type Env = Record<string, string | undefined>

// Backend length caps — see backend IngestPayload / LiveSessionCreate.
const CAPS: Record<string, number> = {
  ci_provider: 30,
  ci_repo: 300,
  ci_actor: 120,
  ci_run_url: 1000,
}

// Explicit user overrides — follow the TESTLOOKUP_* env convention.
const ENV_OVERRIDES: Record<string, keyof CiContext> = {
  TESTLOOKUP_CI_PROVIDER: 'ci_provider',
  TESTLOOKUP_CI_REPO: 'ci_repo',
  TESTLOOKUP_PR_NUMBER: 'pr_number',
  TESTLOOKUP_CI_ACTOR: 'ci_actor',
  TESTLOOKUP_CI_RUN_URL: 'ci_run_url',
}

const CI_FIELDS: Array<keyof CiContext> = [
  'ci_provider',
  'ci_repo',
  'pr_number',
  'ci_actor',
  'ci_run_url',
]

function toPrNumber(value: unknown): number | undefined {
  if (value == null) return undefined
  const n = parseInt(String(value).trim(), 10)
  if (!Number.isInteger(n) || n < 1) return undefined
  // Reject trailing garbage ("12abc" parses to 12 with parseInt).
  if (!/^\d+$/.test(String(value).trim())) return undefined
  return n
}

function clip(field: string, value: unknown): string | undefined {
  if (value == null) return undefined
  const s = String(value).trim()
  if (!s) return undefined
  const cap = CAPS[field]
  return cap != null ? s.slice(0, cap) : s
}

/** Derive "org/name" from a Jenkins GIT_URL, or undefined when unparseable. */
function repoFromGitUrl(gitUrl: string | undefined): string | undefined {
  if (!gitUrl) return undefined
  let s = gitUrl.trim()
  if (s.endsWith('.git')) s = s.slice(0, -'.git'.length)
  const segments = s.replace(/:/g, '/').split('/').filter(Boolean)
  if (segments.length < 2) return undefined
  return `${segments[segments.length - 2]}/${segments[segments.length - 1]}`
}

/**
 * Detect CI context from standard CI env vars. Returns only the fields that
 * could be determined; empty object outside CI. Never throws.
 */
export function detectCiContext(env: Env = process.env): CiContext {
  const get = (key: string): string | undefined => {
    const v = env[key]
    if (v == null) return undefined
    const s = String(v).trim()
    return s || undefined
  }
  const truthy = (key: string): boolean => (get(key) ?? '').toLowerCase() === 'true'

  let provider: string | undefined
  let repo: string | undefined
  let pr: number | undefined
  let actor: string | undefined
  let runUrl: string | undefined

  if (truthy('GITHUB_ACTIONS')) {
    provider = 'github_actions'
    repo = get('GITHUB_REPOSITORY')
    const eventName = get('GITHUB_EVENT_NAME')
    if (eventName === 'pull_request' || eventName === 'pull_request_target') {
      const m = /^refs\/pull\/(\d+)\//.exec(get('GITHUB_REF') ?? '')
      if (m) pr = toPrNumber(m[1])
    }
    actor = get('GITHUB_ACTOR')
    const server = get('GITHUB_SERVER_URL')
    const runId = get('GITHUB_RUN_ID')
    if (server && repo && runId) {
      runUrl = `${server.replace(/\/+$/, '')}/${repo}/actions/runs/${runId}`
    }
  } else if (truthy('GITLAB_CI')) {
    provider = 'gitlab_ci'
    repo = get('CI_PROJECT_PATH')
    pr = toPrNumber(get('CI_MERGE_REQUEST_IID'))
    actor = get('GITLAB_USER_LOGIN') ?? get('GITLAB_USER_NAME')
    runUrl = get('CI_JOB_URL') ?? get('CI_PIPELINE_URL')
  } else if (get('JENKINS_URL')) {
    provider = 'jenkins'
    repo = repoFromGitUrl(get('GIT_URL'))
    pr = toPrNumber(get('CHANGE_ID')) // multibranch PR builds
    actor = get('CHANGE_AUTHOR') ?? get('BUILD_USER_ID')
    runUrl = get('BUILD_URL')
  } else if (truthy('TF_BUILD')) {
    provider = 'azure_devops'
    repo = get('BUILD_REPOSITORY_NAME')
    pr =
      toPrNumber(get('SYSTEM_PULLREQUEST_PULLREQUESTNUMBER')) ??
      toPrNumber(get('SYSTEM_PULLREQUEST_PULLREQUESTID'))
    actor = get('BUILD_REQUESTEDFOR')
    const coll = get('SYSTEM_TEAMFOUNDATIONCOLLECTIONURI')
    const proj = get('SYSTEM_TEAMPROJECT')
    const buildId = get('BUILD_BUILDID')
    if (coll && proj && buildId) {
      const base = coll.endsWith('/') ? coll : `${coll}/`
      runUrl = `${base}${proj}/_build/results?buildId=${buildId}`
    }
  } else if (truthy('CIRCLECI')) {
    provider = 'circleci'
    const user = get('CIRCLE_PROJECT_USERNAME')
    const name = get('CIRCLE_PROJECT_REPONAME')
    if (user && name) repo = `${user}/${name}`
    const prUrl = get('CIRCLE_PULL_REQUEST')
    if (prUrl) {
      const m = /\/(\d+)\/?$/.exec(prUrl)
      if (m) pr = toPrNumber(m[1])
    }
    actor = get('CIRCLE_USERNAME')
    runUrl = get('CIRCLE_BUILD_URL')
  } else {
    return {}
  }

  const out: CiContext = {}
  const providerClipped = clip('ci_provider', provider)
  if (providerClipped != null) out.ci_provider = providerClipped
  const repoClipped = clip('ci_repo', repo)
  if (repoClipped != null) out.ci_repo = repoClipped
  const actorClipped = clip('ci_actor', actor)
  if (actorClipped != null) out.ci_actor = actorClipped
  const runUrlClipped = clip('ci_run_url', runUrl)
  if (runUrlClipped != null) out.ci_run_url = runUrlClipped
  if (pr != null) out.pr_number = pr
  return out
}

/**
 * Merge CI context with the standard precedence chain:
 * detection < TESTLOOKUP_CI_* env overrides < explicit caller values.
 * Returns only defined keys, ready to splat into a session-create payload.
 */
export function resolveCiContext(explicit?: Partial<CiContext>, env: Env = process.env): CiContext {
  const ctx = detectCiContext(env)

  const apply = (field: keyof CiContext, value: unknown): void => {
    if (value == null) return
    if (field === 'pr_number') {
      const n = typeof value === 'number' && Number.isInteger(value) && value >= 1
        ? value
        : toPrNumber(value)
      if (n != null) ctx.pr_number = n
    } else {
      const s = clip(field, value)
      if (s != null) ctx[field] = s
    }
  }

  for (const [envName, field] of Object.entries(ENV_OVERRIDES)) {
    apply(field, env[envName])
  }
  if (explicit) {
    for (const field of CI_FIELDS) {
      apply(field, explicit[field])
    }
  }
  return ctx
}
