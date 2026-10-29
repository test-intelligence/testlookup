/**
 * Fixtures for the W23b narrowing / drift vitests (FAIL-12, FAIL-13), in the REAL (parsed) wire shapes.
 *
 * - `runRef(overrides)`: one build of the walk — by default the FIRST FAILING build "build-sentinel-812"
 *   at commit "badsentinel002" on branch "feature/checkout-sentinel".
 * - `narrowing(overrides)`: one `narrowed` answer on the same branch (good "build-sentinel-805" at
 *   "goodsentinel01", bad "build-sentinel-812" at "badsentinel002", streak 2, two untested runs in
 *   between) for the test "checkout_sentinel_test" / fingerprint "fp-sentinel-checkout"; the compare is
 *   `unavailable` / `offline_mode` (the homelab's answer).
 * - `bisectCommit(overrides)` / `fetchedCompare(commits?)`: a ranked commit; a `fetched` compare
 *   (GitHub, three commits by default, the run-level range 17).
 * - `drift(overrides)`: a `recorded` drift with the acceptance pair (`poetry.lock changed`, then
 *   `python 3.11.8 → 3.11.9`); `driftEntry(section, key, change, old, new)`: one entry.
 *
 * Sentinels are non-hex words, so they never collide with a uuid or read as a phone number; the two
 * commits' first 7 characters differ ("goodsen" / "badsent"). Timestamps are built at CALL time (never a
 * module constant).
 */
import type { BisectCommit, BisectCompare, BisectNarrowing, BisectRunRef } from '@/types/bisect'
import type { ConfigDrift, DriftChange, DriftEntry, DriftSection } from '@/types/configDrift'

export const SENTINEL_TEST_NAME = 'checkout_sentinel_test'
export const SENTINEL_FINGERPRINT = 'fp-sentinel-checkout'
export const SENTINEL_BRANCH = 'feature/checkout-sentinel'
export const SENTINEL_GOOD_BUILD = 'build-sentinel-805'
export const SENTINEL_BAD_BUILD = 'build-sentinel-812'
export const SENTINEL_GOOD_SHA = 'goodsentinel01'
export const SENTINEL_BAD_SHA = 'badsentinel002'
export const SENTINEL_ENV_VALUE = 'Europe/Sentinel'
/** == commit_attribution_service.MONOREPO_CAVEAT (the `caveat` of a fetched compare). */
export const MONOREPO_CAVEAT =
  'Suspects, not culprits: these commits changed files near the failing test. In a monorepo the file owner is often not the commit author — treat each as a lead to inspect, not blame.'

let sequence = 0

/** A canonical (version 4 shaped) uuid, distinct per call. */
export function bisectUuid(): string {
  sequence += 1
  return `00000000-0000-4000-8000-${String(0xb000 + sequence).padStart(12, '0')}`
}

function hoursAgo(hours: number): string {
  return new Date(Date.now() - hours * 3_600_000).toISOString()
}

export function runRef(overrides: Partial<BisectRunRef> = {}): BisectRunRef {
  const runId = overrides.run_id ?? bisectUuid()
  return {
    run_id: runId,
    build_number: SENTINEL_BAD_BUILD,
    commit_hash: SENTINEL_BAD_SHA,
    branch: SENTINEL_BRANCH,
    created_at: hoursAgo(6),
    ci_run_url: 'https://ci.example.com/job/checkout-sentinel/812/',
    outcome: 'fail',
    ...overrides,
  }
}

/** The compare when nothing is fetched: `unavailable` / `offline_mode` by default. */
export function unavailableCompare(overrides: Partial<BisectCompare> = {}): BisectCompare {
  return {
    state: 'unavailable',
    reason: 'offline_mode',
    host: null,
    cached: false,
    commit_count: 0,
    truncated: false,
    commits: [],
    has_location_signal: false,
    caveat: null,
    run_level_commit_count: null,
    ...overrides,
  }
}

export function narrowing(overrides: Partial<BisectNarrowing> = {}): BisectNarrowing {
  const runId = overrides.run_id ?? bisectUuid()
  return {
    run_id: runId,
    fingerprint: SENTINEL_FINGERPRINT,
    test_name: SENTINEL_TEST_NAME,
    state: 'narrowed',
    series_branch: SENTINEL_BRANCH,
    anchor: 'same_branch',
    fallback_branch: null,
    good: runRef({
      build_number: SENTINEL_GOOD_BUILD,
      commit_hash: SENTINEL_GOOD_SHA,
      created_at: hoursAgo(30),
      ci_run_url: 'https://ci.example.com/job/checkout-sentinel/805/',
      outcome: 'pass',
    }),
    bad: runRef({ created_at: hoursAgo(6) }),
    streak_length: 2,
    untested_runs: [
      runRef({ build_number: 'build-sentinel-809', commit_hash: null, created_at: hoursAgo(10), ci_run_url: null, outcome: 'untested' }),
      runRef({ build_number: 'build-sentinel-807', commit_hash: null, created_at: hoursAgo(20), ci_run_url: null, outcome: 'untested' }),
    ],
    untested_count: 2,
    runs_read: 9,
    bounded: false,
    narrowed_from: 'green_baseline',
    compare: unavailableCompare(),
    generated_at: new Date().toISOString(),
    ...overrides,
  }
}

export function bisectCommit(overrides: Partial<BisectCommit> = {}): BisectCommit {
  return {
    sha: 'cafesentinel01',
    author: 'Dana Sentinel',
    message: 'Tighten the checkout sentinel retry',
    committed_at: hoursAgo(12),
    score: 80,
    rationale: { overlapping_files: ['src/checkout/retry.py'], overlap_score: 0.8, recency_rank: 3 },
    commit_url: 'https://github.example.com/acme/payments/commit/cafesentinel01',
    ...overrides,
  }
}

/** A `fetched` compare (GitHub): three ranked commits by default, the run-level range 17. */
export function fetchedCompare(commits?: BisectCommit[], overrides: Partial<BisectCompare> = {}): BisectCompare {
  const list = commits ?? [
    bisectCommit(),
    bisectCommit({ sha: 'fadesentinel02', author: 'Lee Sentinel', message: 'Rename the cart sentinel helper', score: 40 }),
    bisectCommit({ sha: 'beadsentinel03', author: 'Kim Sentinel', message: 'Docs: checkout sentinel notes', score: 10 }),
  ]
  return {
    state: 'fetched',
    reason: null,
    host: 'github',
    cached: false,
    commit_count: list.length,
    truncated: false,
    commits: list,
    has_location_signal: true,
    caveat: MONOREPO_CAVEAT,
    run_level_commit_count: 17,
    ...overrides,
  }
}

export function driftEntry(
  section: DriftSection,
  key: string,
  change: DriftChange,
  old: string | null = null,
  next: string | null = null,
): DriftEntry {
  return { section, key, change, old, new: next }
}

export function drift(overrides: Partial<ConfigDrift> = {}): ConfigDrift {
  const entries = overrides.entries ?? [
    driftEntry('dependencies', 'poetry.lock', 'changed', 'oldhashsenti', 'newhashsenti'),
    driftEntry('runtime', 'python', 'changed', '3.11.8', '3.11.9'),
  ]
  return {
    baseline_run_id: bisectUuid(),
    baseline_build_number: 'build-sentinel-805',
    context_state: 'recorded',
    entries,
    entries_total: entries.length,
    ...overrides,
  }
}
