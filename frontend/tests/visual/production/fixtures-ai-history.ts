/**
 * W03 AIH-02/03/04: the AI history answers of the run page (`/runs/<RUN_ID>?tab=analysis`) and of Run Compare,
 * shaped to `docs/deferred-scope-2026-10-13/work/W03-contract.md` §2 (digest), §3 (diff) and §6 (routes). Shared by
 * the hermetic specs (`tests/ci-e2e/run-ai-history.spec.ts`, `run-compare-ai.spec.ts`) and the visual baselines
 * (`prod-run-ai-history.visual.spec.ts`, `prod-run-compare-ai.visual.spec.ts`), so they can never draw different
 * data. Not a spec file. Like `fixtures.ts`, nothing here imports `@/...` and every instant derives from `NOW`.
 *
 * The run is `RUN_ID` (build 240 of Auth on main, `#60`), analysed twice:
 *   v1  rules (offline), CONDITIONAL_GO at risk 55, decision report v1;
 *   v2  LLM llama3.1,    NO_GO at risk 72,          decision report v2 (the latest).
 * Its previous run on the branch is build 238 (`#58`, `runs()[2]`), analysed by rules: GO at 35. Between the two
 * the test case page's test (`TEST_CASE_ID`) moved INFRASTRUCTURE -> PRODUCT_BUG (40 -> 85), one suggested root
 * cause is new, one resolved (its label is `HOSTILE_NAME`: it must arrive as text), one persists.
 *
 * Every OTHER run-page spec gets `fixtures-run.ts` `AI_HISTORY_QUIET` (inside `RUN_PAGE`): no stored version yet and
 * no previous run, so the Analysis tab's top stays one line each. (It lives there, not here: this module imports
 * `fixtures-pages.ts`, which imports `fixtures-run.ts`.)
 */
import { respond, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import { HOSTILE_NAME, isoAgo, LAYOUT, PROJECT_ID, RUN_ID, RUN_RECOMMENDATIONS, RUNS, runs } from './fixtures'
import { TEST_CASE_FINGERPRINT, TEST_CASE_ID, TEST_CASE_NAME } from './fixtures-pages'

const RUN_PATH = `/api/v1/runs/${RUN_ID}`
export const AI_HISTORY_PATH = `${RUN_PATH}/ai-history`
export const AI_DIFF_PATH = `${RUN_PATH}/ai-history/diff`
export const PREVIOUS_PATH = `${RUN_PATH}/previous`
export const AI_COMPARE_PATH = `${RUN_PATH}/ai-compare`

const ALL_RUNS = runs()
const CURRENT = ALL_RUNS[0]
/** Build 238 of Auth on main: the run before `RUN_ID` on its suite and branch. */
export const PREVIOUS = ALL_RUNS[2]
export const PREVIOUS_RUN_ID = PREVIOUS.id

export const V1_ID = 'a1000000-0000-4000-8000-000000000001'
export const V2_ID = 'a1000000-0000-4000-8000-000000000002'
const S1_ID = 'b1000000-0000-4000-8000-000000000001'
const S2_ID = 'b1000000-0000-4000-8000-000000000002'

const ENVELOPE = { requires_human_review: true, review: null, ai_disclaimer: null, ai_disclaimer_version: null }

function summary(version: 1 | 2, kind: 'analysis_digest' | 'run_summary' = 'analysis_digest') {
  const llm = version === 2
  return {
    version_id: kind === 'run_summary' ? (llm ? S2_ID : S1_ID) : llm ? V2_ID : V1_ID,
    kind,
    version,
    trigger: 'pipeline',
    pipeline_run_id: `c1000000-0000-4000-8000-00000000000${version}`,
    generated_at: llm ? isoAgo(0, 1.3) : isoAgo(0, 3),
    created_at: llm ? isoAgo(0, 1.3) : isoAgo(0, 3),
    analysis_mode: kind === 'run_summary' ? (llm ? 'llm' : 'deterministic') : llm ? 'llm' : 'rules',
    llm_provider: llm ? 'ollama' : null,
    llm_model: llm ? 'llama3.1' : null,
    fallback_used: !llm,
    deterministic: !llm,
    verdict: kind === 'run_summary' ? null : llm ? 'NO_GO' : 'CONDITIONAL_GO',
    risk_score: kind === 'run_summary' ? null : llm ? 72 : 55,
    failing_tests: 15,
    categories: kind === 'run_summary' ? null : llm ? { INFRASTRUCTURE: 3, PRODUCT_BUG: 12 } : { INFRASTRUCTURE: 6, PRODUCT_BUG: 9 },
    cluster_count: kind === 'run_summary' ? null : 2,
    headline: kind === 'run_summary' ? (llm ? 'Session tokens expire before the MFA step completes.' : 'Fifteen Auth failures, grouped by error.') : null,
    content_sha256: `${llm ? 'b' : 'a'}${'0'.repeat(63)}`,
    decision_report_ref: kind === 'run_summary' ? null : { report_id: `d1000000-0000-4000-8000-00000000000${version}`, report_version: version },
    summary_version: kind === 'run_summary' ? null : version,
    review: { state: 'pending_review', message: 'Not reviewed yet.', review_id: null, reviewed_at: null },
    is_latest: llm,
  }
}

export const VERSIONS = [summary(2), summary(1)]

const list = (kind: 'analysis_digest' | 'run_summary', versions: unknown[]) => ({
  run_id: RUN_ID, kind, total: versions.length, limit: 100, truncated: false,
  latest_version: versions.length ? 2 : null, live: { available: true }, versions, ...ENVELOPE,
})

export const HISTORY = list('analysis_digest', VERSIONS)
const SUMMARY_HISTORY = list('run_summary', [summary(2, 'run_summary'), summary(1, 'run_summary')])

const test = (over: Record<string, unknown>) => ({
  test_fingerprint: TEST_CASE_FINGERPRINT, test_id: TEST_CASE_ID, test_name: TEST_CASE_NAME, suite_name: 'Auth',
  status: 'FAILED', category: 'INFRASTRUCTURE', confidence: 40, root_cause_sha: '1111111111111111',
  root_cause_excerpt: 'The session store timed out while the cart was read.', is_flaky: false,
  requires_human_review: true, analysis_mode: 'rules', ...over,
})

function digest(version: 1 | 2) {
  const llm = version === 2
  return {
    schema_version: 1,
    kind: 'analysis_digest',
    run: {
      run_id: RUN_ID, project_id: PROJECT_ID, build_number: String(CURRENT.build_number), branch: 'main', primary_suite_name: 'Auth',
      total_tests: CURRENT.total_tests, failed_tests: CURRENT.failed_tests, broken_tests: CURRENT.broken_tests, pass_rate: CURRENT.pass_rate,
    },
    verdict: {
      recommendation: llm ? 'NO_GO' : 'CONDITIONAL_GO', risk_score: llm ? 72 : 55, composite_risk: llm ? 72 : 55,
      source: 'decision_report', decision_report_version: version, overridden: false,
    },
    analysed_tests: 15,
    categories: llm ? { INFRASTRUCTURE: 3, PRODUCT_BUG: 12 } : { INFRASTRUCTURE: 6, PRODUCT_BUG: 9 },
    avg_confidence: llm ? 78.4 : 52.1,
    flaky_count: 0,
    requires_review_count: llm ? 2 : 6,
    clusters: [
      {
        signature: 'token expired before mfa', cluster_id: 'cl_001', label: 'Session token expiry before MFA', size: llm ? 9 : 7,
        member_count: llm ? 9 : 7, member_fingerprints: [TEST_CASE_FINGERPRINT], representative_error: 'TokenExpiredError: session token expired',
      },
      {
        signature: 'session store timeout', cluster_id: 'cl_002', label: 'Session store timeouts', size: llm ? 3 : 6,
        member_count: llm ? 3 : 6, member_fingerprints: ['fp-auth-session'], representative_error: 'TimeoutError: session store did not answer in 5s',
      },
    ],
    tests: [
      test(llm ? { category: 'PRODUCT_BUG', confidence: 85, analysis_mode: 'llm', root_cause_sha: '2222222222222222', root_cause_excerpt: 'The tax line is skipped for a discounted item.' } : {}),
      {
        test_fingerprint: 'fp-auth-token-refresh', test_id: '66666666-6666-4666-8666-000000000001', test_name: 'auth::token::test_refresh_rotates',
        suite_name: 'Auth', status: 'FAILED', category: 'PRODUCT_BUG', confidence: llm ? 90 : 70, root_cause_sha: '3333333333333333',
        root_cause_excerpt: 'The refresh endpoint returns the old token.', is_flaky: false, requires_human_review: false, analysis_mode: llm ? 'llm' : 'rules',
      },
    ],
    actions: llm ? ['Restore the previous session TTL'] : ['Restart the session store'],
    actions_source: 'decision_report',
    provenance: {
      pipeline_run_id: `c1000000-0000-4000-8000-00000000000${version}`, source: 'stored', captured_at: llm ? isoAgo(0, 1.3) : isoAgo(0, 3),
      engine: llm ? 'llm' : 'rules', mode_counts: llm ? { llm: 15 } : { rules: 15 }, llm_provider: llm ? 'ollama' : null,
      llm_model: llm ? 'llama3.1' : null, fallback_used: false, fallback_count: 0, deterministic: !llm,
      summary_fallback_used: !llm, carried_over_tests: 0, stage_quality: 'normal', prompt_versions: {}, partial: [],
    },
    limits: { tests_total: 2, tests_truncated: false, clusters_total: 2, clusters_truncated: false },
    content_sha256: `${llm ? 'b' : 'a'}${'0'.repeat(63)}`,
  }
}

const BY_ID: Record<string, unknown> = {
  [V1_ID]: { ...summary(1), payload: digest(1), ...ENVELOPE },
  [V2_ID]: { ...summary(2), payload: digest(2), ...ENVELOPE },
  [S1_ID]: {
    ...summary(1, 'run_summary'), ...ENVELOPE,
    payload: { executive_summary: 'Fifteen Auth failures, grouped by error: **session store timeouts** and token expiry.', fallback_used: true },
  },
  [S2_ID]: {
    ...summary(2, 'run_summary'), ...ENVELOPE,
    payload: { executive_summary: 'Session tokens expire before the MFA step completes; the tax line is skipped for discounted items.', fallback_used: false },
  },
}

const NO_PROVENANCE_CHANGE = { changed: false, engine: null, model: null, fallback_used: null, deterministic: null, mode_counts: null, engine_only: false, note: null }

/** `GET .../ai-history/diff?from=v1&to=v2`: what the LLM re-analysis changed. */
export const DIFF_V1_V2 = {
  schema_version: 1, mode: 'versions', identical: false, has_changes: true,
  provenance: {
    changed: true, engine: { from: 'rules', to: 'llm' }, model: { from: null, to: 'llama3.1' }, fallback_used: null,
    deterministic: { from: true, to: false }, mode_counts: { from: { rules: 15 }, to: { llm: 15 } }, engine_only: false,
    note: 'The analysis engine changed between these versions (rules -> llm).',
  },
  verdict: { changed: true, from: 'CONDITIONAL_GO', to: 'NO_GO', direction: 'worse', from_risk: 55, to_risk: 72, risk_delta: 17 },
  categories: {
    changed: [{ category: 'PRODUCT_BUG', from: 9, to: 12, delta: 3 }, { category: 'INFRASTRUCTURE', from: 6, to: 3, delta: -3 }],
    from_total: 15, to_total: 15,
  },
  clusters: {
    added: [],
    removed: [],
    matched: [
      { signature_from: 'token expired before mfa', signature_to: 'token expired before mfa', label_from: 'Session token expiry before MFA', label_to: 'Session token expiry before MFA', size_from: 7, size_to: 9, delta: 2, matched_by: 'signature', jaccard: null, resized: true },
      { signature_from: 'session store timeout', signature_to: 'session store timeout', label_from: 'Session store timeouts', label_to: 'Session store timeouts', size_from: 6, size_to: 3, delta: -3, matched_by: 'signature', jaccard: null, resized: true },
    ],
    counts: { added: 0, removed: 0, matched: 2, resized: 2 },
  },
  tests: {
    added: [],
    removed: [],
    changed: [
      {
        test_fingerprint: TEST_CASE_FINGERPRINT, test_id: TEST_CASE_ID, from_test_id: TEST_CASE_ID, test_name: TEST_CASE_NAME, suite_name: 'Auth',
        kinds: ['category', 'confidence', 'root_cause'], material: true,
        category: { from: 'INFRASTRUCTURE', to: 'PRODUCT_BUG' }, confidence: { from: 40, to: 85, delta: 45 }, flaky: null,
        root_cause_changed: true, root_cause_excerpt: { from: 'The session store timed out while the cart was read.', to: 'The tax line is skipped for a discounted item.' },
      },
      {
        test_fingerprint: 'fp-auth-token-refresh', test_id: '66666666-6666-4666-8666-000000000001', from_test_id: '66666666-6666-4666-8666-000000000001',
        test_name: 'auth::token::test_refresh_rotates', suite_name: 'Auth', kinds: ['confidence'], material: true,
        category: null, confidence: { from: 70, to: 90, delta: 20 }, flaky: null, root_cause_changed: false, root_cause_excerpt: null,
      },
    ],
    persisting_unchanged: 13,
    counts: { added: 0, removed: 0, changed: 2, material_changed: 2 },
    truncated: false,
  },
  actions: { added: ['Restore the previous session TTL'], removed: ['Restart the session store'] },
}

/** A diff with nothing to say (a version against the current data it still matches). */
const DIFF_IDENTICAL = {
  ...DIFF_V1_V2,
  identical: true, has_changes: false, provenance: NO_PROVENANCE_CHANGE,
  verdict: { changed: false, from: 'NO_GO', to: 'NO_GO', direction: 'same', from_risk: 72, to_risk: 72, risk_delta: 0 },
  categories: { changed: [], from_total: 15, to_total: 15 },
  clusters: { added: [], removed: [], matched: [], counts: { added: 0, removed: 0, matched: 0, resized: 0 } },
  tests: { added: [], removed: [], changed: [], persisting_unchanged: 15, counts: { added: 0, removed: 0, changed: 0, material_changed: 0 }, truncated: false },
  actions: { added: [], removed: [] },
}

const ref = (id: string | 'live') =>
  id === 'live'
    ? { ref: 'live', source: 'live', version: null, kind: 'analysis_digest', generated_at: null, pipeline_run_id: null, content_sha256: null, analysis_mode: 'llm', llm_model: 'llama3.1', run_id: RUN_ID, build_number: String(CURRENT.build_number) }
    : { ref: id, source: 'stored', version: id === V1_ID ? 1 : 2, kind: 'analysis_digest', generated_at: null, pipeline_run_id: null, content_sha256: null, analysis_mode: id === V1_ID ? 'rules' : 'llm', llm_model: id === V1_ID ? null : 'llama3.1', run_id: RUN_ID, build_number: String(CURRENT.build_number) }

function diffAnswer({ url }: ApiRequest) {
  const from = url.searchParams.get('from') ?? ''
  const to = url.searchParams.get('to') ?? 'latest'
  if (!(from in BY_ID) || from === S1_ID || from === S2_ID) return respond(404, { detail: 'AI version not found' })
  const toId = to === 'latest' ? V2_ID : to
  const diff = from === V1_ID && toId === V2_ID ? DIFF_V1_V2 : DIFF_IDENTICAL
  return { run_id: RUN_ID, from: ref(from), to: ref(toId === 'live' ? 'live' : toId), diff, ...ENVELOPE }
}

const brief = (run: (typeof ALL_RUNS)[number]) => ({
  run_id: run.id, build_number: String(run.build_number), run_seq: run.run_seq, branch: run.branch,
  primary_suite_name: run.primary_suite_name, status: run.status, created_at: run.created_at, finished_at: run.end_time,
})

const RULE = { scope: 'project+primary_suite+branch', suite_name: 'Auth', branch: 'main', candidate_status: 'not IN_PROGRESS (any outcome)', order: 'finished before this run, newest first' }

export const PREVIOUS_ANSWER = { run_id: RUN_ID, rule: RULE, current: brief(CURRENT), baseline: brief(PREVIOUS), reason: null, message: null }

/** The cross-run diff #58 -> #60. */
export const CROSS_RUN_DIFF = {
  schema_version: 1, mode: 'cross_run', identical: false, has_changes: true,
  provenance: {
    changed: true, engine: { from: 'rules', to: 'llm' }, model: { from: null, to: 'llama3.1' }, fallback_used: null,
    deterministic: { from: true, to: false }, mode_counts: { from: { rules: 9 }, to: { llm: 15 } }, engine_only: false,
    note: 'Baseline analysed by rules; current by llm. Category changes may reflect the engine, not the product.',
  },
  verdict: { changed: true, from: 'GO', to: 'NO_GO', direction: 'worse', from_risk: 35, to_risk: 72, risk_delta: 37 },
  categories: {
    changed: [{ category: 'PRODUCT_BUG', from: 3, to: 12, delta: 9 }, { category: 'INFRASTRUCTURE', from: 6, to: 3, delta: -3 }],
    from_total: 9, to_total: 15,
  },
  clusters: {
    added: [{ signature: 'token expired before mfa', label: 'Session token expiry before MFA', size: 9, member_count: 9 }],
    removed: [{ signature: 'db connection refused', label: HOSTILE_NAME, size: 6, member_count: 6 }],
    matched: [
      { signature_from: 'session store timeout', signature_to: 'session store timeout', label_from: 'Session store timeouts', label_to: 'Session store timeouts', size_from: 2, size_to: 3, delta: 1, matched_by: 'signature', jaccard: null, resized: true },
    ],
    counts: { added: 1, removed: 1, matched: 1, resized: 1 },
  },
  tests: {
    added: [
      { test_fingerprint: 'fp-auth-token-refresh', test_id: '66666666-6666-4666-8666-000000000001', test_name: 'auth::token::test_refresh_rotates', suite_name: 'Auth', category: 'PRODUCT_BUG', confidence: 90 },
    ],
    removed: [
      { test_fingerprint: 'fp-auth-db', test_id: '55555555-5555-4555-8555-000000000001', test_name: 'auth::db::test_pool_reconnects', suite_name: 'Auth', category: 'INFRASTRUCTURE', confidence: 60 },
    ],
    changed: [
      {
        test_fingerprint: TEST_CASE_FINGERPRINT, test_id: TEST_CASE_ID, from_test_id: '55555555-5555-4555-8555-000000000002', test_name: TEST_CASE_NAME, suite_name: 'Auth',
        kinds: ['category', 'confidence'], material: true, category: { from: 'INFRASTRUCTURE', to: 'PRODUCT_BUG' },
        confidence: { from: 40, to: 85, delta: 45 }, flaky: null, root_cause_changed: false, root_cause_excerpt: null,
      },
    ],
    persisting_unchanged: 4,
    counts: { added: 1, removed: 1, changed: 1, material_changed: 1 },
    truncated: false,
  },
  actions: { added: ['Restore the previous session TTL'], removed: [] },
}

const SIDES = {
  baseline: {
    verdict: 'GO', risk_score: 35, composite_risk: 35, analysed_tests: 9, categories: { INFRASTRUCTURE: 6, PRODUCT_BUG: 3 }, avg_confidence: 55,
    top_clusters: [{ label: HOSTILE_NAME, size: 6, signature: 'db connection refused' }, { label: 'Session store timeouts', size: 2, signature: 'session store timeout' }],
    engine: 'rules', llm_provider: null, llm_model: null, deterministic: true, fallback_used: false, decision_report_version: 1, generated_at: null,
  },
  current: {
    verdict: 'NO_GO', risk_score: 72, composite_risk: 72, analysed_tests: 15, categories: { INFRASTRUCTURE: 3, PRODUCT_BUG: 12 }, avg_confidence: 78.4,
    top_clusters: [{ label: 'Session token expiry before MFA', size: 9, signature: 'token expired before mfa' }, { label: 'Session store timeouts', size: 3, signature: 'session store timeout' }],
    engine: 'llm', llm_provider: 'ollama', llm_model: 'llama3.1', deterministic: false, fallback_used: false, decision_report_version: 2, generated_at: null,
  },
}

/** `GET .../ai-compare` (default baseline = the previous run; an explicit one = that run). */
export function compareAnswer(baselineRunId: string | null = null) {
  const baseline = baselineRunId ? ALL_RUNS.find((r) => r.id === baselineRunId) ?? PREVIOUS : PREVIOUS
  const explicit = baselineRunId != null
  return {
    run_id: RUN_ID, project_id: PROJECT_ID,
    baseline: {
      ...brief(baseline),
      selection: explicit ? 'explicit' : 'default',
      selection_reason: explicit ? 'You chose this baseline.' : 'Same suite and branch, newest completed run before this one.',
      branch_mismatch: baseline.branch !== CURRENT.branch,
    },
    reason: null, message: null,
    current_ref: ref('live'),
    baseline_ref: { ...ref('live'), run_id: baseline.id, build_number: String(baseline.build_number), analysis_mode: 'rules', llm_model: null },
    sides: SIDES,
    diff: CROSS_RUN_DIFF,
    narrative: {
      status: 'deterministic', source: 'deterministic', label: 'deterministic summary', fallback_used: true,
      executive_summary: 'The AI verdict went from GO to NO_GO: one new suggested root cause, one resolved, one test changed category.',
      ai_changes: [
        'Baseline analysed by rules; current by llm. Category changes may reflect the engine, not the product.',
        'Verdict GO -> NO_GO (risk 35 -> 72)',
        'New root cause: Session token expiry before MFA (9 tests)',
        `Resolved: ${HOSTILE_NAME} (6 tests)`,
        `${TEST_CASE_NAME}: INFRASTRUCTURE -> PRODUCT_BUG (confidence 40 -> 85)`,
      ],
      risk_level: null, prompt_version: 'run_compare_v2', model_name: null, generated_at: null, report_status: 'absent',
    },
    ...ENVELOPE,
  }
}

/** The run page's AI history, analysed twice, with a previous run: put BEFORE `RUN_PAGE` (first match wins). */
export const AI_HISTORY: ApiHandlers = [
  [AI_HISTORY_PATH, ({ url }) => (url.searchParams.get('kind') === 'run_summary' ? SUMMARY_HISTORY : HISTORY)],
  [AI_DIFF_PATH, diffAnswer],
  [/^\/api\/v1\/runs\/[^/]+\/ai-history\/[^/]+$/, ({ path }) => {
    const id = path.split('/').pop() ?? ''
    return path.startsWith(`${RUN_PATH}/`) && BY_ID[id] ? BY_ID[id] : respond(404, { detail: 'AI version not found' })
  }],
  [PREVIOUS_PATH, () => PREVIOUS_ANSWER],
  [AI_COMPARE_PATH, ({ url }) => compareAnswer(url.searchParams.get('baseline_run_id'))],
]

// ── Run Compare (/runs/compare?mode=manual&left=<238>&right=<240>) ────────

const compareSide = (run: (typeof ALL_RUNS)[number]) => ({
  id: run.id, project_id: run.project_id, build_number: String(run.build_number), branch: run.branch, commit_hash: null, status: run.status,
  total_tests: run.total_tests, passed_tests: run.passed_tests, failed_tests: run.failed_tests, broken_tests: run.broken_tests,
  skipped_tests: run.skipped_tests, unknown_tests: 0, pass_rate: run.pass_rate, duration_ms: run.duration_ms,
  start_time: run.start_time, end_time: run.end_time, primary_suite_name: run.primary_suite_name, suite_names: run.suite_names,
})

/** `GET /runs/compare?left=<238>&right=<240>&suite_name=Auth`: the results diff and its v2 AI report. */
export const RUN_COMPARE = {
  left: compareSide(PREVIOUS),
  right: compareSide(CURRENT),
  transitions: [
    { before: 'passed', after: 'passed', count: 180 },
    { before: 'passed', after: 'failed', count: 12 },
    { before: 'passed', after: 'broken', count: 3 },
  ],
  scope: 'suite',
  suite_name: 'Auth',
  selection: {
    mode: 'explicit', scope: 'suite', suite_name: 'Auth', selection_reason: 'You chose both runs.', project_id: PROJECT_ID,
    branch: 'main', branch_mismatch: false, release_name: null,
  },
  ai_report: {
    status: 'ready',
    executive_summary: 'Fifteen Auth tests started failing after the session TTL change.',
    markdown_report: '',
    risk_level: 'HIGH',
    key_differences: ['12 new failures in Auth'],
    new_risks: ['Session token expiry before MFA'],
    resolved_risks: [],
    duration_concerns: [],
    recommended_actions: ['Restore the previous session TTL'],
    confidence: 72,
    confidence_reason: 'Deterministic comparison.',
    fallback_used: true,
    ai_changes: compareAnswer().narrative.ai_changes,
  },
  delta_total: 0, delta_passed: -15, delta_failed: 12, delta_broken: 3, delta_skipped: 0, delta_pass_rate: -7.5, delta_duration_ms: 12000,
  new_failures: 12, fixed: 0, still_failing: 0, regressed: 3, improved: 0, new_tests: 0, removed_tests: 0, duration_spikes: 0, renamed: 0,
  test_deltas: [
    {
      test_fingerprint: TEST_CASE_FINGERPRINT, test_name: TEST_CASE_NAME, suite_name: 'Auth', left_status: 'PASSED', right_status: 'FAILED',
      left_duration_ms: 900, right_duration_ms: 3600, delta_duration_ms: 2700, classification: 'new_failure', paired_by: 'fingerprint',
    },
  ],
  truncated: false,
}

export const COMPARE_PAGE_PATH = `/runs/compare?mode=manual&left=${PREVIOUS_RUN_ID}&right=${RUN_ID}&suite=Auth`

/**
 * W07 FAIL-06: `GET /runs/{id}` as the run pickers read it (the deep link's
 * two labels, and the "Paste run ID" check): a fixture run, else 404 (the
 * server answers a run the reader cannot see 403 or 404; the picker shows one
 * text for both).
 */
export function runDetail({ path }: ApiRequest) {
  const run = ALL_RUNS.find((r) => r.id === path.split('/')[4])
  return run ? { ...run, ai_recommendation: RUN_RECOMMENDATIONS[ALL_RUNS.indexOf(run)] ?? null } : respond(404, { detail: 'Test run not found' })
}

export const RUN_DETAIL_PATH = /^\/api\/v1\/runs\/[0-9a-f-]{36}$/

/** Everything `/runs/compare` asks for with the two runs above. */
export const RUN_COMPARE_PAGE: ApiHandlers = [
  ...LAYOUT,
  ...RUNS,
  ['/api/v1/runs/compare', () => RUN_COMPARE],
  // W08 TRND-02: the latest-suite form (`?mode=latest&suite=Auth`, from the Trends ⋯ menu): the latest
  // completed Auth run against the previous one on its branch — the same two runs as the manual pair.
  [
    '/api/v1/runs/compare/latest',
    ({ url }) =>
      (url.searchParams.get('suite_name') ?? '').trim().toLowerCase() === 'auth'
        ? { ...RUN_COMPARE, selection: { ...RUN_COMPARE.selection, mode: 'latest', selection_reason: 'The latest completed Auth run and the one before it on main.' } }
        : respond(404, { detail: 'No two completed runs of this suite on one branch.' }),
  ],
  [AI_COMPARE_PATH, ({ url }) => compareAnswer(url.searchParams.get('baseline_run_id'))],
  [RUN_DETAIL_PATH, runDetail],
]
