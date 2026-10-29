/**
 * W23b FAIL-12: history bisect narrowing — the wire vocabulary, the shapes and their defensive parser.
 *
 * `GET /api/v1/runs/{run_id}/bisect?fingerprint=` narrows ONE failing test of a run to its own last pass
 * (same branch series, or `main` / `master` as a labelled fallback) and, online, lists the commits between
 * the two builds. Observe only: no job, no write.
 *
 * The constants mirror `backend/app/services/bisect_vocab.py` value for value (pinned by
 * `backend/tests/test_bisect_drift_vocab_parity.py`, which reads this file as TEXT — keep every constant a
 * plain literal on its own `export const` line, strings in one piece, one `key: 'value',` per record line).
 * Enum-like fields are plain strings on the wire.
 *
 * Absence is not health: no pass inside the 200-run bound is `no_pass_within_bound` (never "never"); a
 * timeout is not a failure (`timed_out` offers Retry). Every name, SHA, branch and sentence is rendered as
 * React text; links only to `/runs/<uuid>` (SPA-built) or through `safeCommitHref`.
 */

export const BISECT_MAX_RUNS = 200
export const UNTESTED_LIST_MAX = 20

export const BISECT_STATES = ['narrowed', 'never_passed', 'no_pass_within_bound', 'not_failing'] as const
export type BisectState = (typeof BISECT_STATES)[number]

/** Where the good run was found. */
export const ANCHOR_CLASSES = ['same_branch', 'main_fallback'] as const
export type AnchorClass = (typeof ANCHOR_CLASSES)[number]

/** This test in one run of the walk (skipped / unknown / absent = untested). */
export const TEST_OUTCOMES = ['pass', 'fail', 'untested'] as const
export type TestOutcome = (typeof TEST_OUTCOMES)[number]

export const COMPARE_STATES = ['fetched', 'unavailable', 'timed_out', 'not_attempted'] as const
export type CompareState = (typeof COMPARE_STATES)[number]

/** `compare.reason` when not fetched: the six git-host reasons (`COMMIT_RANGE_REASON_TEXT`), then bisect's own. */
export const COMPARE_REASONS = ['offline_mode', 'github_checks_off', 'no_integration', 'integration_disabled', 'repo_mismatch', 'no_token', 'not_narrowed', 'no_good_commit', 'no_bad_commit', 'same_commit', 'cache_unavailable', 'fetch_failed', 'timed_out'] as const
export type CompareReason = (typeof COMPARE_REASONS)[number]

/** Fixed copy, shown verbatim; `{…}` filled by the SPA from the answer. */
export const BISECT_SENTENCES = {
  narrowed: 'Last passed in build {good} ({good_sha}), first failed in build {bad} ({bad_sha}).',
  narrowed_main: 'Never passed on {branch} in the runs read; last passed on {fallback} in build {good} ({good_sha}). First failed on {branch} in build {bad} ({bad_sha}).',
  never_passed: 'This test never passed on {branch}: every run of it there failed ({runs} runs read).',
  no_pass_within_bound: 'No pass in the last 200 runs of {branch}; older history was not searched.',
  not_failing: 'This test did not fail in build {run}: there is nothing to narrow.',
  untested: '{n} run(s) in between did not run this test.',
  no_commit: 'no commit recorded',
  same_commit: 'The last pass and the first failure ran the same commit, so no code change explains this failure (a flaky test, data or the environment).',
  no_good_commit: 'The last passing build recorded no commit, so the commits in between cannot be listed.',
  no_bad_commit: 'The first failing build recorded no commit, so the commits in between cannot be listed.',
  cache_unavailable: 'The commit list is not fetched right now (the cache is unavailable). The two builds above are exact.',
  timed_out: 'The commit list did not arrive within 8 seconds.',
  fetch_failed: 'The git host refused or failed the compare; the two builds above are exact.',
  merge_caveat: "Compared as the git host's {good_sha}...{bad_sha} (commits reachable from the failure and not from the pass): with merges or a rebase this is not a straight line.",
} as const
export type BisectSentenceKey = keyof typeof BISECT_SENTENCES

/** The route's own 404: detail = {code, message}. */
export const BISECT_ERRORS = {
  fingerprint_not_in_run: 'That test is not part of this run.',
} as const

/** One run of the walk (the good / bad build, or one untested run in between). */
export interface BisectRunRef {
  run_id: string
  build_number: string
  /** The stored value, full length; the SPA shows the first 7 (or "no commit recorded"). */
  commit_hash: string | null
  branch: string | null
  created_at: string
  /** Only an `^https?://` URL reaches the SPA; still re-checked with `safeCommitHref` before linking. */
  ci_run_url: string | null
  outcome: TestOutcome
}

/** One ranked commit (`score_commits`' item) and its server-built link. */
export interface BisectCommit {
  sha: string | null
  author: string | null
  message: string
  committed_at: string | null
  score: number
  rationale: Record<string, unknown>
  commit_url: string | null
}

export interface BisectCompare {
  state: CompareState
  /** A `COMPARE_REASONS` value (a plain string: an unknown one reads `UNKNOWN_REASON_LINE`); null when fetched. */
  reason: string | null
  /** "github" | "gitlab": which connector answered. */
  host: string | null
  cached: boolean
  /** Commits the compare returned (≤ 100); `commits` holds at most 50 of them, ranked. */
  commit_count: number
  truncated: boolean
  commits: BisectCommit[]
  has_location_signal: boolean
  caveat: string | null
  /** `run_commit_ranges.commits` of the run (the run-level range); null = no stored range. */
  run_level_commit_count: number | null
}

/** The parsed `BisectResponse`. */
export interface BisectNarrowing {
  run_id: string
  fingerprint: string
  test_name: string
  state: BisectState
  /** The run's branch as stored; null = "runs without a branch". */
  series_branch: string | null
  anchor: AnchorClass | null
  /** "main" / "master" when `anchor` is `main_fallback`. */
  fallback_branch: string | null
  good: BisectRunRef | null
  bad: BisectRunRef | null
  /** Failing runs from bad to this run, inclusive. */
  streak_length: number
  /** Strictly between good and bad, newest first, ≤ `UNTESTED_LIST_MAX`. */
  untested_runs: BisectRunRef[]
  /** All of them. */
  untested_count: number
  runs_read: number
  /** `runs_read == BISECT_MAX_RUNS`: older history was not read. */
  bounded: boolean
  /** The run's commit-range `base_source` (green_baseline | last_completed_run | supplied), else "unavailable". */
  narrowed_from: string
  compare: BisectCompare
  generated_at: string
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function isNum(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v)
}

function strOrNull(v: unknown): string | null {
  return typeof v === 'string' ? v : null
}

function oneOf<T extends string>(values: readonly T[], v: unknown): v is T {
  return typeof v === 'string' && (values as readonly string[]).includes(v)
}

/** A run reference, `null` when absent, `undefined` when present but malformed. */
function parseRunRef(raw: unknown): BisectRunRef | null | undefined {
  if (raw === undefined || raw === null) return null
  if (!isRecord(raw)) return undefined
  if (typeof raw.run_id !== 'string' || typeof raw.build_number !== 'string' || typeof raw.created_at !== 'string') {
    return undefined
  }
  return {
    run_id: raw.run_id,
    build_number: raw.build_number,
    commit_hash: strOrNull(raw.commit_hash),
    branch: strOrNull(raw.branch),
    created_at: raw.created_at,
    ci_run_url: strOrNull(raw.ci_run_url),
    outcome: oneOf(TEST_OUTCOMES, raw.outcome) ? raw.outcome : 'untested',
  }
}

function parseCommit(raw: unknown): BisectCommit | null {
  if (!isRecord(raw)) return null
  return {
    sha: strOrNull(raw.sha),
    author: strOrNull(raw.author),
    message: typeof raw.message === 'string' ? raw.message : '',
    committed_at: strOrNull(raw.committed_at),
    score: isNum(raw.score) ? raw.score : 0,
    rationale: isRecord(raw.rationale) ? raw.rationale : {},
    commit_url: strOrNull(raw.commit_url),
  }
}

/** An unknown or missing compare reads as `unavailable` with no reason (the block shows `UNKNOWN_REASON_LINE`). */
function parseCompare(raw: unknown): BisectCompare {
  const unknown: BisectCompare = {
    state: 'unavailable',
    reason: null,
    host: null,
    cached: false,
    commit_count: 0,
    truncated: false,
    commits: [],
    has_location_signal: false,
    caveat: null,
    run_level_commit_count: null,
  }
  if (!isRecord(raw)) return unknown
  const runLevel = isNum(raw.run_level_commit_count) ? raw.run_level_commit_count : null
  if (!oneOf(COMPARE_STATES, raw.state)) return { ...unknown, run_level_commit_count: runLevel }
  const commits = Array.isArray(raw.commits)
    ? raw.commits.map(parseCommit).filter((c): c is BisectCommit => c !== null)
    : []
  return {
    state: raw.state,
    reason: strOrNull(raw.reason),
    host: strOrNull(raw.host),
    cached: raw.cached === true,
    commit_count: isNum(raw.commit_count) ? raw.commit_count : commits.length,
    truncated: raw.truncated === true,
    commits: raw.state === 'fetched' ? commits : [],
    has_location_signal: raw.has_location_signal === true,
    caveat: strOrNull(raw.caveat),
    run_level_commit_count: runLevel,
  }
}

/**
 * The defensive guard over `GET /api/v1/runs/{run_id}/bisect` (the `parseTrendSummary` precedent). Returns
 * `null` for a malformed answer — not an object, no run id / fingerprint, an unknown `state`, a present but
 * malformed good / bad build, or a `narrowed` answer missing either build — so the block says "Could not
 * load the narrowed range." instead of crashing the section. An unknown `compare.state` is treated as
 * `unavailable` with no reason; malformed untested runs and commits are dropped; missing optional fields are
 * defaulted. Unknown extra keys are ignored.
 */
export function parseBisect(raw: unknown): BisectNarrowing | null {
  if (!isRecord(raw)) return null
  if (typeof raw.run_id !== 'string' || typeof raw.fingerprint !== 'string') return null
  if (!oneOf(BISECT_STATES, raw.state)) return null
  const good = parseRunRef(raw.good)
  const bad = parseRunRef(raw.bad)
  if (good === undefined || bad === undefined) return null
  if (raw.state === 'narrowed' && (good === null || bad === null)) return null
  const untested: BisectRunRef[] = []
  for (const item of Array.isArray(raw.untested_runs) ? raw.untested_runs : []) {
    const ref = parseRunRef(item)
    if (ref) untested.push(ref)
  }
  const runsRead = isNum(raw.runs_read) ? raw.runs_read : 0
  return {
    run_id: raw.run_id,
    fingerprint: raw.fingerprint,
    test_name: typeof raw.test_name === 'string' ? raw.test_name : '',
    state: raw.state,
    series_branch: strOrNull(raw.series_branch),
    anchor: oneOf(ANCHOR_CLASSES, raw.anchor) ? raw.anchor : null,
    fallback_branch: strOrNull(raw.fallback_branch),
    good,
    bad,
    streak_length: isNum(raw.streak_length) ? raw.streak_length : 0,
    untested_runs: untested,
    untested_count: isNum(raw.untested_count) ? raw.untested_count : untested.length,
    runs_read: runsRead,
    bounded: typeof raw.bounded === 'boolean' ? raw.bounded : runsRead >= BISECT_MAX_RUNS,
    narrowed_from: typeof raw.narrowed_from === 'string' ? raw.narrowed_from : 'unavailable',
    compare: parseCompare(raw.compare),
    generated_at: typeof raw.generated_at === 'string' ? raw.generated_at : '',
  }
}
