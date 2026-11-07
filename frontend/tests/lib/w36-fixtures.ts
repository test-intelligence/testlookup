/**
 * W36 fixtures — the binding response shapes of the W36 contract (§1 STRAT-03 cluster epics, §2 STRAT-08 likely
 * affected tests), for the hermetic specs (`cluster-epic.spec.ts`, `likely-affected-tests.spec.ts`).
 *
 * Self-contained on purpose: no import from `tests/visual/production/*`, and the matchers are path patterns, so a
 * world in those fixture files can spread `CLUSTER_EPICS_OFF` / `LIKELY_AFFECTED_LISTED` into its handlers without
 * an import cycle.
 *
 *   CLUSTER_EPICS_OFF       the product default (the opt-in is off for every project until a QA lead ticks it):
 *                           the clusters tab draws exactly what it drew before W36
 *   LIKELY_AFFECTED_LISTED  a run whose range touched the checkout module: a ranked list with history
 */
import type { ApiHandlers } from './production-pages'

// ── STRAT-03: GET /api/v1/projects/{pid}/systemic-clusters/epics (+ /{membership_key}/epic[/preview]) ──

export const CLUSTER_EPICS_PATH = /^\/api\/v1\/projects\/[^/]+\/systemic-clusters\/epics$/
export const clusterEpicsPath = (projectId: string) => `/api/v1/projects/${projectId}/systemic-clusters/epics`
export const clusterEpicPath = (projectId: string, membershipKey: string) =>
  `/api/v1/projects/${projectId}/systemic-clusters/${membershipKey}/epic`

export interface ClusterEpicLinkFixture {
  membership_key: string
  jira_key: string
  jira_url: string | null
  cluster_label: string | null
  created_at: string | null
  created_by_name: string | null
}

/** The status body; default = opted OUT (the migration's server default), Jira ready, no links. */
export function clusterEpicsStatus(over: Partial<{
  enabled: boolean
  available: boolean
  reason: string | null
  epic_type_available: boolean | null
  min_co_failure_runs: number
  links: ClusterEpicLinkFixture[]
}> = {}) {
  return {
    enabled: false,
    available: true,
    reason: null as string | null,
    epic_type_available: true as boolean | null,
    min_co_failure_runs: 3,
    links: [] as ClusterEpicLinkFixture[],
    ...over,
  }
}

export function clusterEpicLink(membershipKey: string, jiraKey: string, label: string | null = null): ClusterEpicLinkFixture {
  return {
    membership_key: membershipKey,
    jira_key: jiraKey,
    jira_url: `https://jira.example.test/browse/${jiraKey}`,
    cluster_label: label,
    created_at: '2026-11-05T09:30:00Z',
    created_by_name: 'QA Lead',
  }
}

/** The preview body for one cluster. */
export function clusterEpicPreview(membershipKey: string, label: string, memberCount: number, coFailureRuns: number) {
  return {
    membership_key: membershipKey,
    summary: `[TestLookup] Recurring failure cluster: ${label}`.slice(0, 255),
    description_text:
      `Recurring failure cluster: ${label}\nCause family: external_dependency\n` +
      `Members (${memberCount}): gateway health, gateway retry, …\nFailed together in ${coFailureRuns} runs` +
      ' (window 60 days), cohesion 82%.\nOpen in TestLookup: /failures',
    labels: ['testlookup', 'systemic-cluster-epic'],
    jira_project_key: 'PAY',
    member_count: memberCount,
    co_failure_runs: coFailureRuns,
    cluster_label: label,
  }
}

export const CLUSTER_EPICS_OFF: ApiHandlers = [[CLUSTER_EPICS_PATH, () => clusterEpicsStatus()]]

// ── STRAT-08: GET /api/v1/runs/{run_id}/likely-affected-tests?limit=20 ──

export const LIKELY_AFFECTED_PATH = /^\/api\/v1\/runs\/[^/]+\/likely-affected-tests$/
export const likelyAffectedPath = (runId: string) => `/api/v1/runs/${runId}/likely-affected-tests`

export const LIKELY_AFFECTED_CAVEAT =
  'Advisory only: ranked by path overlap and past co-failures. It never selects or skips tests.'

export const PATH_ONLY_REASON = 'only 6 of 9 runs carry a usable commit range (need 30)'

type Row = {
  test_fingerprint: string
  test_name: string
  class_name: string | null
  status_in_run: string
  score: number
  reason: {
    overlapping_paths: string[]
    overlap_score: number
    co_failure_runs: number | null
    history_runs_considered: number | null
    known_flaky: boolean
  }
}

/** Three ranked rows; the second known-flaky; with history (`co_failure_runs` counted) unless `pathOnly`. */
export function likelyAffectedRows(pathOnly = false): Row[] {
  const history = (failed: number, considered: number) =>
    pathOnly ? { co_failure_runs: null, history_runs_considered: null } : { co_failure_runs: failed, history_runs_considered: considered }
  return [
    {
      test_fingerprint: 'fp-la-gateway',
      test_name: 'test_gateway_timeout_retries',
      class_name: 'tests.payments.test_gateway',
      status_in_run: 'FAILED',
      score: pathOnly ? 1 : 0.92,
      reason: { overlapping_paths: ['src/payments/gateway.py'], overlap_score: 1, known_flaky: false, ...history(4, 5) },
    },
    {
      test_fingerprint: 'fp-la-refund',
      test_name: 'test_refund_webhook',
      class_name: null,
      status_in_run: 'PASSED',
      score: pathOnly ? 0.7 : 0.5,
      reason: {
        overlapping_paths: ['src/payments/refunds/webhook.py', 'src/payments/refunds/models.py'],
        overlap_score: 0.7,
        known_flaky: true,
        ...history(1, 5),
      },
    },
    {
      test_fingerprint: 'fp-la-cart',
      test_name: 'test_cart_total',
      class_name: 'tests.checkout.test_cart',
      status_in_run: 'BROKEN',
      score: pathOnly ? 0.6 : 0.36,
      reason: { overlapping_paths: ['src/checkout/cart.py'], overlap_score: 0.6, known_flaky: false, ...history(0, 5) },
    },
  ]
}

/** The response body for `runId`; default a listed run with history. */
export function likelyAffectedTests(runId: string, over: Record<string, unknown> = {}) {
  return {
    run_id: runId,
    project_id: '11111111-1111-4111-8111-111111111111',
    available: true,
    reason: null as string | null,
    advisory: true,
    caveat: LIKELY_AFFECTED_CAVEAT,
    changed_files_count: 14,
    file_detail_coverage: 1,
    source: 'connector',
    base_source: 'last_completed_run',
    base_anchor_is_strong: false,
    history: {
      available: true,
      insufficient_data_reason: null as string | null,
      usable_runs: 64,
      window_days: 90,
      runs_considered: 5,
      scan_capped: false,
    },
    tests: likelyAffectedRows(),
    total_candidates: 212,
    ...over,
  }
}

/** History not ready: the path-only answer (`co_failure_runs` null, never 0). */
export function likelyAffectedPathOnly(runId: string) {
  return likelyAffectedTests(runId, {
    history: {
      available: false,
      insufficient_data_reason: PATH_ONLY_REASON,
      usable_runs: 6,
      window_days: 90,
      runs_considered: 0,
      scan_capped: false,
    },
    tests: likelyAffectedRows(true),
  })
}

/** No range / a range without files: `available: false` with the reason, never an empty list standing in. */
export function likelyAffectedUnavailable(runId: string, reason: 'no_commit_range' | 'no_changed_files') {
  return likelyAffectedTests(runId, {
    available: false,
    reason,
    changed_files_count: 0,
    file_detail_coverage: null,
    source: reason === 'no_commit_range' ? null : 'unavailable',
    base_source: null,
    base_anchor_is_strong: null,
    tests: [],
    total_candidates: 0,
  })
}

const runIdOf = (path: string) => path.split('/')[4]

export const LIKELY_AFFECTED_LISTED: ApiHandlers = [[LIKELY_AFFECTED_PATH, ({ path }) => likelyAffectedTests(runIdOf(path))]]
