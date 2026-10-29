/**
 * W23b FAIL-12 / FAIL-13 hermetic world: the narrowed range on `/failures` › Suspects and on the run page's
 * Changes tab, and the Changes tab's configuration drift
 * (`docs/deferred-scope-2026-10-13/work/W23b-contract.md` §6.2 G-A, §6.3 specs).
 *
 * `bisectDriftWorld(options)` answers FIRST, with the REAL server shapes, for ONE run (`runId`, default `RUN_ID`
 * -- the run of every top-failing row's last failure, and the run page's run):
 *  - `GET /api/v1/runs/{runId}/bisect?fingerprint=` → the `BisectResponse` scripted for that fingerprint
 *    (`answers[fp]`: an answer, a scripted refusal `{status, detail}`, or a function of how many times the
 *    fingerprint has been asked -- a `timed_out` answer then a `fetched` one for the Retry). The answer's
 *    `run_id` / `fingerprint` echo the request, as the server's do. A fingerprint with no script is the
 *    server's 404 `{"detail": {"code": "fingerprint_not_in_run", "message"}}` (another run's, another
 *    project's and an unknown fingerprint answer alike); no `fingerprint` / one over 64 characters is
 *    FastAPI's 422. EVERY fingerprint asked is recorded in `seen`, in order. Any member may read (VIEWER
 *    included): the role only picks the signed-in `user`.
 *  - `GET /api/v1/runs/{runId}/regression-diff` → `regressionDiff` (default `worldRegressionDiff()`: the
 *    baseline build 238, three new failures each WITH `test_fingerprint`, and a `recorded` `config_drift`).
 *    Pass a diff without `config_drift` (`worldRegressionDiff({ config_drift: undefined })`) for an older
 *    server.
 *
 * Then `base`, then the shared sets exactly as the existing specs use them: `FAILURES_ON` (whose `FAILURES`
 * answers every other `…/bisect` with the homelab's `offlineNarrowing`) and `RUN_PAGE`. Every other request
 * fails closed as today.
 *
 * Fresh state per call; `state` is live and mutable (a spec may change an answer or the diff between steps).
 * The clock is `NOW` (the pinned page clock). Sentinels are non-hex words; the two commits' first 7 characters
 * differ ("goodsen" / "badsent"). Not a spec file.
 */
import { respond, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import { BISECT_ERRORS, type BisectCommit, type BisectCompare, type BisectNarrowing, type BisectRunRef } from '../../../src/types/bisect'
import type { ConfigDrift, DriftChange, DriftEntry, DriftSection } from '../../../src/types/configDrift'
import { FAILURES_ON, isoAgo, NOW, RUN_ID, runs, USER } from './fixtures'
import { RUN_PAGE, runTests } from './fixtures-run'
import { ROLE_USERS, type WorldRole } from './fixtures-retention'

export type { WorldRole }

// ── Fixed sentinels (non-hex words: never a uuid collision, never a phone number) ──

export const WORLD_TEST_NAME = 'checkout_sentinel_test'
export const WORLD_FINGERPRINT = 'fp-sentinel-checkout'
export const WORLD_BRANCH = 'feature/checkout-sentinel'
export const WORLD_GOOD_SHA = 'goodsentinel01'
export const WORLD_BAD_SHA = 'badsentinel002'
export const WORLD_ENV_VALUE = 'Europe/Sentinel'
/** == commit_attribution_service.MONOREPO_CAVEAT (a fetched compare's `caveat`). */
export const WORLD_MONOREPO_CAVEAT =
  'Suspects, not culprits: these commits changed files near the failing test. In a monorepo the file owner is often not the commit author — treat each as a lead to inspect, not blame.'
/** The longest fingerprint the route accepts (`Query(..., min_length=1, max_length=64)`). */
const FINGERPRINT_MAX = 64

type FixtureRun = ReturnType<typeof runs>[number]

/** One build of the walk, from a `runs()` row (build 240 = `runs()[0]`, 239, 238 = the last green, …). */
export function worldRunRef(run: FixtureRun, overrides: Partial<BisectRunRef> = {}): BisectRunRef {
  return {
    run_id: run.id,
    build_number: String(run.build_number),
    commit_hash: null,
    branch: run.branch,
    created_at: run.created_at,
    ci_run_url: `https://ci.example.com/job/checkout-ci/${run.build_number}/`,
    outcome: 'untested',
    ...overrides,
  }
}

/** The compare when nothing is fetched: `unavailable` / `offline_mode` by default (the homelab's). */
export function worldOfflineCompare(overrides: Partial<BisectCompare> = {}): BisectCompare {
  return {
    state: 'unavailable', reason: 'offline_mode', host: null, cached: false, commit_count: 0, truncated: false,
    commits: [], has_location_signal: false, caveat: null, run_level_commit_count: null, ...overrides,
  }
}

/** A ranked commit (`score_commits`' item) with its server-built link. */
export function worldCommit(overrides: Partial<BisectCommit> = {}): BisectCommit {
  return {
    sha: 'cafesentinel01',
    author: 'Dana Sentinel',
    message: 'Tighten the checkout sentinel retry',
    committed_at: isoAgo(1, 4),
    score: 82,
    rationale: { overlapping_files: ['src/checkout/retry.py'], overlap_score: 0.82, recency_rank: 3 },
    commit_url: 'https://github.example.com/acme/payments/commit/cafesentinel01',
    ...overrides,
  }
}

/** A `fetched` compare (GitHub): three ranked commits, the run-level range 17 ("… has 17"). */
export function worldFetchedCompare(overrides: Partial<BisectCompare> = {}): BisectCompare {
  const commits = [
    worldCommit(),
    worldCommit({ sha: 'fadesentinel02', author: 'Lee Sentinel', message: 'Rename the cart sentinel helper', score: 41,
      commit_url: 'https://github.example.com/acme/payments/commit/fadesentinel02' }),
    worldCommit({ sha: 'beadsentinel03', author: 'Kim Sentinel', message: 'Docs: checkout sentinel notes', score: 12,
      commit_url: 'https://github.example.com/acme/payments/commit/beadsentinel03' }),
  ]
  return {
    state: 'fetched', reason: null, host: 'github', cached: false, commit_count: commits.length, truncated: false,
    commits, has_location_signal: true, caveat: WORLD_MONOREPO_CAVEAT, run_level_commit_count: 17, ...overrides,
  }
}

/** A compare that ran out of its 8 s budget (the block offers Retry). */
export function worldTimedOutCompare(overrides: Partial<BisectCompare> = {}): BisectCompare {
  return worldOfflineCompare({ state: 'timed_out', reason: 'timed_out', ...overrides })
}

/**
 * A `narrowed` answer on one branch (`WORLD_BRANCH`): last passed in build 236 at `goodsentinel01`, first failed
 * in build 239 at `badsentinel002`, failing through build 240 (streak 2), builds 238 and 237 in between did not
 * run the test; the compare `unavailable` / `offline_mode`. `run_id` / `fingerprint` are replaced by the request's
 * when the world answers.
 */
export function worldNarrowing(overrides: Partial<BisectNarrowing> = {}): BisectNarrowing {
  const all = runs()
  const onBranch = (run: FixtureRun, extra: Partial<BisectRunRef>) => worldRunRef(run, { branch: WORLD_BRANCH, ...extra })
  return {
    run_id: RUN_ID,
    fingerprint: WORLD_FINGERPRINT,
    test_name: WORLD_TEST_NAME,
    state: 'narrowed',
    series_branch: WORLD_BRANCH,
    anchor: 'same_branch',
    fallback_branch: null,
    good: onBranch(all[4], { commit_hash: WORLD_GOOD_SHA, outcome: 'pass' }),
    bad: onBranch(all[1], { commit_hash: WORLD_BAD_SHA, outcome: 'fail' }),
    streak_length: 2,
    untested_runs: [onBranch(all[2], { ci_run_url: null }), onBranch(all[3], { ci_run_url: null })],
    untested_count: 2,
    runs_read: 9,
    bounded: false,
    narrowed_from: 'green_baseline',
    compare: worldOfflineCompare(),
    generated_at: NOW.toISOString(),
    ...overrides,
  }
}

/** A scripted refusal: the route answers `status` with `{"detail": detail}`. */
export interface BisectRefusal {
  status: number
  detail: unknown
}

/** The route's own 404 for a fingerprint that is not a test of THIS run (the same body for every such value). */
export function notInRun(): BisectRefusal {
  return { status: 404, detail: { code: 'fingerprint_not_in_run', message: BISECT_ERRORS.fingerprint_not_in_run } }
}

/** One drift entry. */
export function worldDriftEntry(
  section: DriftSection,
  key: string,
  change: DriftChange,
  old: string | null = null,
  next: string | null = null,
): DriftEntry {
  return { section, key, change, old, new: next }
}

/**
 * A `recorded` drift against build 238 (`runs()[2]`, the diff's baseline): `poetry.lock` changed, `python 3.11.8 →
 * 3.11.9` (the acceptance pair), `TZ UTC → Europe/Sentinel` and `branch main → feature/checkout-sentinel` -- in
 * `DRIFT_SECTIONS` order, keys ascending inside a section.
 */
export function worldDrift(overrides: Partial<ConfigDrift> = {}): ConfigDrift {
  const baseline = runs()[2]
  const entries = overrides.entries ?? [
    worldDriftEntry('dependencies', 'poetry.lock', 'changed', '3f2a9c1d7b4e', '9e81d04c2a77'),
    worldDriftEntry('runtime', 'python', 'changed', '3.11.8', '3.11.9'),
    worldDriftEntry('env', 'TZ', 'changed', 'UTC', WORLD_ENV_VALUE),
    worldDriftEntry('run', 'branch', 'changed', 'main', WORLD_BRANCH),
  ]
  return {
    baseline_run_id: baseline.id,
    baseline_build_number: String(baseline.build_number),
    context_state: 'recorded',
    entries,
    entries_total: entries.length,
    ...overrides,
  }
}

/** The new failures of the default diff: the run's first three failing tests, each WITH a fingerprint. */
export function worldNewFailures(): Array<{ test_name: string; suite_name: string | null; test_fingerprint: string }> {
  return runTests()
    .filter((t) => t.status === 'FAILED' || t.status === 'BROKEN')
    .slice(0, 3)
    .map((t, i) => ({ test_name: String(t.test_name), suite_name: String(t.suite_name), test_fingerprint: `fp-new-failure-${i}` }))
}

/**
 * The run page's `GET /api/v1/runs/{run_id}/regression-diff` answer (the baseline-found shape of
 * `regression_diff_service.compute_regression_diff`): baseline build 238, `worldNewFailures()`, the two commits the
 * shared `RUN_PAGE` diff lists, and `config_drift` = `worldDrift()`. Keys in `overrides` replace the defaults; a key
 * set to `undefined` is left out of the JSON (`config_drift: undefined` = an older server).
 */
export function worldRegressionDiff(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  const [run, , baseline] = runs()
  const failures = worldNewFailures()
  return {
    baseline_available: true,
    baseline_run_id: baseline.id,
    baseline_build_number: String(baseline.build_number),
    pass_rate: run.pass_rate,
    baseline_pass_rate: baseline.pass_rate,
    pass_rate_delta: Math.round((run.pass_rate - baseline.pass_rate) * 10) / 10,
    new_failing_tests: failures,
    new_failing_count: failures.length,
    resolved_count: 0,
    commit_range: [
      { sha: 'a1b2c3d', message: 'Shorten the session token TTL', author: 'dev-one', timestamp: isoAgo(0, 5) },
      { sha: 'e4f5a6b', message: 'Move MFA to the new provider', author: 'dev-two', timestamp: isoAgo(0, 7) },
    ],
    baseline_purged: null,
    config_drift: worldDrift(),
    ...overrides,
  }
}

// ── The world ──

/** One scripted answer: a narrowing, a refusal, or a function of how many times this fingerprint was asked (1, 2, …). */
export type BisectWorldAnswer = BisectNarrowing | BisectRefusal | ((asked: number) => BisectNarrowing | BisectRefusal)

export interface BisectDriftWorldOptions {
  /** The signed-in role (default QA_ENGINEER); any member may read the narrowing, so it only picks the user. */
  role?: WorldRole
  /** The ONE run the world answers for (default `RUN_ID`). */
  runId?: string
  /** The answer per fingerprint; a fingerprint with no entry is the 404 `fingerprint_not_in_run`. */
  answers?: Record<string, BisectWorldAnswer>
  /** The run's regression-diff answer (default `worldRegressionDiff()`). */
  regressionDiff?: Record<string, unknown>
  /** Where every asked fingerprint is recorded, in order (default a fresh array). */
  seen?: string[]
  /** Extra handlers, answered after the world's routes and before the shared sets. */
  base?: ApiHandlers
}

export interface BisectDriftWorldState {
  answers: Record<string, BisectWorldAnswer>
  regressionDiff: Record<string, unknown>
}

export interface BisectDriftWorld {
  handlers: ApiHandlers
  /** Every fingerprint the page asked `…/bisect` for (also a missing / over-long one, as sent), in order. */
  seen: string[]
  state: BisectDriftWorldState
  /** The `user` to sign in as (`ROLE_USERS[role]`). */
  user: typeof USER
}

const isRefusal = (answer: BisectNarrowing | BisectRefusal): answer is BisectRefusal =>
  !('state' in answer) && 'status' in answer && 'detail' in answer

/** FastAPI's own 422 for the query `fingerprint` (the shape `extractErrorMessage` reads first). */
function fingerprint422(type: 'missing' | 'string_too_long', msg: string) {
  return respond(422, { detail: [{ type, loc: ['query', 'fingerprint'], msg, input: null }] })
}

export function bisectDriftWorld(options: BisectDriftWorldOptions = {}): BisectDriftWorld {
  const role: WorldRole = options.role ?? 'QA_ENGINEER'
  const runId = options.runId ?? RUN_ID
  const seen = options.seen ?? []
  const asked = new Map<string, number>()
  const state: BisectDriftWorldState = {
    answers: { ...(options.answers ?? {}) },
    regressionDiff: options.regressionDiff ?? worldRegressionDiff(),
  }

  const bisect = ({ url }: ApiRequest) => {
    const fingerprint = url.searchParams.get('fingerprint')
    seen.push(fingerprint ?? '')
    if (!fingerprint) return fingerprint422('missing', 'Field required')
    if (fingerprint.length > FINGERPRINT_MAX) {
      return fingerprint422('string_too_long', `String should have at most ${FINGERPRINT_MAX} characters`)
    }
    const count = (asked.get(fingerprint) ?? 0) + 1
    asked.set(fingerprint, count)
    const scripted = Object.prototype.hasOwnProperty.call(state.answers, fingerprint) ? state.answers[fingerprint] : undefined
    const answer = typeof scripted === 'function' ? scripted(count) : (scripted ?? notInRun())
    if (isRefusal(answer)) return respond(answer.status, { detail: answer.detail })
    return { ...answer, run_id: runId, fingerprint }
  }

  const routes: ApiHandlers = [
    [`/api/v1/runs/${runId}/bisect`, bisect],
    [`/api/v1/runs/${runId}/regression-diff`, () => state.regressionDiff],
  ]
  return {
    handlers: [...routes, ...(options.base ?? []), ...FAILURES_ON, ...RUN_PAGE],
    seen,
    state,
    user: ROLE_USERS[role],
  }
}
