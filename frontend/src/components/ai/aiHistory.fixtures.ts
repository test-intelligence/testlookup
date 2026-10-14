/**
 * W03 AIH-02/03/04: unit-test answers of the AI history API, shaped to the contract (§2 digest, §3 diff, §6 routes).
 * Imported by the `*.test.ts(x)` files of components/ai, pages and hooks only.
 *
 * `HOSTILE` is a name that would run script if any path turned AI or CI text into markup: every test that renders
 * it asserts it arrives as these literal characters and creates no element.
 */
import type {
  AICompare,
  AIDigest,
  AIDigestDiff,
  AIHistoryList,
  AIPreviousRun,
  AIVersion,
  AIVersionSummary,
  RunBrief,
} from '@/types/aiHistory'

export const HOSTILE = '<img src=x onerror=alert(1)>'

export const RUN_ID = '33333333-3333-4333-8333-333333333333'
export const PREV_RUN_ID = '44444444-4444-4444-8444-100000000002'

export function versionSummary(version: number, overrides: Partial<AIVersionSummary> = {}): AIVersionSummary {
  return {
    version_id: `00000000-0000-4000-8000-00000000000${version}`,
    kind: 'analysis_digest',
    version,
    trigger: 'pipeline',
    pipeline_run_id: `pipe-${version}`,
    generated_at: '2026-09-18T10:00:00Z',
    created_at: '2026-09-18T10:00:01Z',
    analysis_mode: version === 1 ? 'rules' : 'llm',
    llm_provider: version === 1 ? null : 'ollama',
    llm_model: version === 1 ? null : 'llama3.1',
    fallback_used: false,
    deterministic: version === 1,
    verdict: version === 1 ? 'CONDITIONAL_GO' : 'NO_GO',
    risk_score: version === 1 ? 55 : 72,
    failing_tests: 8,
    categories: { PRODUCT_BUG: 2 + 3 * (version - 1), INFRASTRUCTURE: 6 - 3 * (version - 1) },
    cluster_count: 2,
    headline: null,
    content_sha256: `${'a'.repeat(63)}${version}`,
    decision_report_ref: { report_id: `report-${version}`, report_version: version },
    summary_version: version,
    review: null,
    is_latest: false,
    ...overrides,
  }
}

/** The list route's answer for `versions` (newest first), the highest marked latest. */
export function historyList(versions: AIVersionSummary[], overrides: Partial<AIHistoryList> = {}): AIHistoryList {
  const ordered = [...versions].sort((a, b) => b.version - a.version)
  const latest = ordered[0]?.version ?? null
  return {
    run_id: RUN_ID,
    kind: 'analysis_digest',
    total: ordered.length,
    limit: 100,
    truncated: false,
    latest_version: latest,
    live: { available: true },
    versions: ordered.map((v) => ({ ...v, is_latest: v.version === latest })),
    requires_human_review: true,
    review: null,
    ai_disclaimer: null,
    ai_disclaimer_version: null,
    ...overrides,
  }
}

export function digest(overrides: Partial<AIDigest> = {}): AIDigest {
  return {
    schema_version: 1,
    kind: 'analysis_digest',
    run: {
      run_id: RUN_ID, project_id: 'p1', build_number: '240', branch: 'main', primary_suite_name: 'Auth',
      total_tests: 197, failed_tests: 12, broken_tests: 3, pass_rate: 92.3,
    },
    verdict: { recommendation: 'CONDITIONAL_GO', risk_score: 55, composite_risk: 55, source: 'decision_report', decision_report_version: 1, overridden: false },
    analysed_tests: 3,
    categories: { INFRASTRUCTURE: 2, PRODUCT_BUG: 1 },
    avg_confidence: 61.7,
    flaky_count: 0,
    requires_review_count: 1,
    clusters: [
      {
        signature: 'timeout waiting for session store', cluster_id: 'cl_001', label: 'Session store timeouts', size: 2,
        member_count: 2, member_fingerprints: ['fp-a', 'fp-b'], representative_error: 'TimeoutError: session store',
      },
      {
        signature: 'hostile', cluster_id: 'cl_002', label: HOSTILE, size: 1,
        member_count: 1, member_fingerprints: ['fp-c'], representative_error: HOSTILE,
      },
    ],
    tests: [
      {
        test_fingerprint: 'fp-a', test_id: 'tc-a', test_name: 'test_login', suite_name: 'Auth', status: 'FAILED',
        category: 'INFRASTRUCTURE', confidence: 40, root_cause_sha: '1111111111111111',
        root_cause_excerpt: 'The session store timed out.', is_flaky: false, requires_human_review: true, analysis_mode: 'rules',
      },
      {
        test_fingerprint: 'fp-c', test_id: 'tc-c', test_name: HOSTILE, suite_name: 'Auth', status: 'FAILED',
        category: 'PRODUCT_BUG', confidence: 80, root_cause_sha: '2222222222222222',
        root_cause_excerpt: HOSTILE, is_flaky: false, requires_human_review: false, analysis_mode: 'rules',
      },
    ],
    actions: ['Restart the session store'],
    actions_source: 'decision_report',
    provenance: {
      pipeline_run_id: 'pipe-1', source: 'stored', captured_at: '2026-09-18T10:00:00Z', engine: 'rules',
      mode_counts: { rules: 3 }, llm_provider: null, llm_model: null, fallback_used: false, fallback_count: 0,
      deterministic: true, summary_fallback_used: false, carried_over_tests: 1, stage_quality: 'normal',
      prompt_versions: {}, partial: [],
    },
    limits: { tests_total: 3, tests_truncated: false, clusters_total: 2, clusters_truncated: false },
    content_sha256: 'b'.repeat(64),
    ...overrides,
  }
}

export function storedVersion(version: number, payload: unknown = digest()): AIVersion {
  return { ...versionSummary(version), payload: payload as AIDigest, requires_human_review: true }
}

/** v1 (rules) -> v2 (LLM): CONDITIONAL_GO -> NO_GO, one cluster new, one resolved, one resized; test_pay moved category. */
export function versionsDiff(overrides: Partial<AIDigestDiff> = {}): AIDigestDiff {
  return {
    schema_version: 1,
    mode: 'versions',
    identical: false,
    has_changes: true,
    provenance: {
      changed: true,
      engine: { from: 'rules', to: 'llm' },
      model: { from: null, to: 'llama3.1' },
      fallback_used: null,
      deterministic: { from: true, to: false },
      mode_counts: { from: { rules: 8 }, to: { llm: 8 } },
      engine_only: false,
      note: 'The analysis engine changed between these versions (rules -> llm).',
    },
    verdict: { changed: true, from: 'CONDITIONAL_GO', to: 'NO_GO', direction: 'worse', from_risk: 55, to_risk: 72, risk_delta: 17 },
    categories: {
      changed: [
        { category: 'INFRASTRUCTURE', from: 6, to: 3, delta: -3 },
        { category: 'PRODUCT_BUG', from: 2, to: 5, delta: 3 },
        { category: 'FLAKY', from: 1, to: 2, delta: 1 },
      ],
      from_total: 8,
      to_total: 8,
    },
    clusters: {
      added: [
        { signature: 'small', label: 'Small new cluster', size: 1, member_count: 1 },
        { signature: 'timeout checkout', label: 'Timeout in checkout', size: 4, member_count: 4 },
      ],
      removed: [{ signature: 'db refused', label: HOSTILE, size: 6, member_count: 6 }],
      matched: [
        { signature_from: 'same', signature_to: 'same', label_from: 'Same size', label_to: 'Same size', size_from: 2, size_to: 2, delta: 0, matched_by: 'signature', jaccard: null, resized: false },
        { signature_from: 'grew-a', signature_to: 'grew-b', label_from: 'Cache misses', label_to: 'Cache misses (renamed)', size_from: 3, size_to: 5, delta: 2, matched_by: 'overlap', jaccard: 0.67, resized: true },
      ],
      counts: { added: 2, removed: 1, matched: 2, resized: 1 },
    },
    tests: {
      added: [{ test_fingerprint: 'fp-new', test_id: 'tc-new', test_name: 'test_new', suite_name: 'Checkout', category: 'PRODUCT_BUG', confidence: 90 }],
      removed: [{ test_fingerprint: 'fp-gone', test_id: 'old-tc-gone', test_name: 'test_gone', suite_name: 'Checkout', category: 'INFRASTRUCTURE', confidence: 50 }],
      changed: [
        {
          test_fingerprint: 'fp-reworded', test_id: 'tc-reworded', from_test_id: 'tc-reworded', test_name: 'test_reworded', suite_name: 'Checkout',
          kinds: ['root_cause'], material: false, category: null, confidence: null, flaky: null, root_cause_changed: true,
          root_cause_excerpt: { from: 'old words', to: 'new words' },
        },
        {
          test_fingerprint: 'fp-pay', test_id: 'tc-pay', from_test_id: 'tc-pay', test_name: 'test_pay', suite_name: 'Checkout',
          kinds: ['category', 'confidence'], material: true,
          category: { from: 'INFRASTRUCTURE', to: 'PRODUCT_BUG' }, confidence: { from: 40, to: 85, delta: 45 }, flaky: null,
          root_cause_changed: false, root_cause_excerpt: null,
        },
        {
          test_fingerprint: 'fp-hostile', test_id: 'tc-hostile', from_test_id: 'tc-hostile', test_name: HOSTILE, suite_name: 'Checkout',
          kinds: ['confidence'], material: true, category: null, confidence: { from: 60, to: 70, delta: 10 }, flaky: null,
          root_cause_changed: false, root_cause_excerpt: null,
        },
      ],
      persisting_unchanged: 5,
      counts: { added: 1, removed: 1, changed: 3, material_changed: 2 },
      truncated: false,
    },
    actions: { added: ['Fix the gateway timeout'], removed: ['Restart the database'] },
    ...overrides,
  }
}

/** Two identical analyses: every section empty. */
export function identicalDiff(): AIDigestDiff {
  return versionsDiff({
    identical: true,
    has_changes: false,
    provenance: { changed: false, engine: null, model: null, fallback_used: null, deterministic: null, mode_counts: null, engine_only: false, note: null },
    verdict: { changed: false, from: 'NO_GO', to: 'NO_GO', direction: 'same', from_risk: 72, to_risk: 72, risk_delta: 0 },
    categories: { changed: [], from_total: 8, to_total: 8 },
    clusters: { added: [], removed: [], matched: [], counts: { added: 0, removed: 0, matched: 0, resized: 0 } },
    tests: { added: [], removed: [], changed: [], persisting_unchanged: 8, counts: { added: 0, removed: 0, changed: 0, material_changed: 0 }, truncated: false },
    actions: { added: [], removed: [] },
  })
}

export function crossRunDiff(overrides: Partial<AIDigestDiff> = {}): AIDigestDiff {
  return versionsDiff({
    mode: 'cross_run',
    provenance: {
      changed: true, engine: { from: 'rules', to: 'llm' }, model: { from: null, to: 'llama3.1' }, fallback_used: null,
      deterministic: null, mode_counts: null, engine_only: false,
      note: 'Baseline analysed by rules; current by llm. Category changes may reflect the engine, not the product.',
    },
    verdict: { changed: true, from: 'GO', to: 'NO_GO', direction: 'worse', from_risk: 35, to_risk: 72, risk_delta: 37 },
    ...overrides,
  })
}

export function brief(overrides: Partial<RunBrief> = {}): RunBrief {
  return {
    run_id: RUN_ID, build_number: '240', run_seq: 60, branch: 'main', primary_suite_name: 'Auth', status: 'FAILED',
    created_at: '2026-09-18T10:00:00Z', finished_at: '2026-09-18T10:07:00Z', ...overrides,
  }
}

export function previousRun(overrides: Partial<AIPreviousRun> = {}): AIPreviousRun {
  return {
    run_id: RUN_ID,
    rule: { scope: 'project+primary_suite+branch', suite_name: 'Auth', branch: 'main', candidate_status: 'not IN_PROGRESS (any outcome)', order: 'finished before this run, newest first' },
    current: brief(),
    baseline: brief({ run_id: PREV_RUN_ID, build_number: '238', run_seq: 58, status: 'PASSED', created_at: '2026-09-18T07:00:00Z', finished_at: '2026-09-18T07:07:00Z' }),
    reason: null,
    message: null,
    ...overrides,
  }
}

export function aiCompare(overrides: Partial<AICompare> = {}): AICompare {
  const prev = previousRun()
  return {
    run_id: RUN_ID,
    project_id: 'p1',
    baseline: prev.baseline
      ? { ...prev.baseline, selection: 'default', selection_reason: 'Same suite and branch, newest completed run before this one.', branch_mismatch: false }
      : null,
    reason: null,
    message: null,
    current_ref: { ref: 'live', source: 'live', run_id: RUN_ID, build_number: '240' },
    baseline_ref: { ref: 'live', source: 'live', run_id: PREV_RUN_ID, build_number: '238' },
    sides: {
      baseline: {
        verdict: 'GO', risk_score: 35, composite_risk: 35, analysed_tests: 6, categories: { INFRASTRUCTURE: 6 }, avg_confidence: 50,
        top_clusters: [{ label: HOSTILE, size: 6, signature: 'db refused' }], engine: 'rules', llm_provider: null, llm_model: null,
        deterministic: true, fallback_used: false, decision_report_version: 1, generated_at: null,
      },
      current: {
        verdict: 'NO_GO', risk_score: 72, composite_risk: 72, analysed_tests: 8, categories: { PRODUCT_BUG: 5, INFRASTRUCTURE: 3 }, avg_confidence: 71.5,
        top_clusters: [{ label: 'Timeout in checkout', size: 4, signature: 'timeout checkout' }], engine: 'llm', llm_provider: 'ollama', llm_model: 'llama3.1',
        deterministic: false, fallback_used: false, decision_report_version: 2, generated_at: null,
      },
    },
    diff: crossRunDiff(),
    narrative: {
      status: 'deterministic', source: 'deterministic', label: 'deterministic summary', fallback_used: true,
      executive_summary: 'The verdict went from GO to NO_GO; one new suggested root cause.',
      ai_changes: ['Verdict GO -> NO_GO (risk 35 -> 72)', `Resolved: ${HOSTILE} (6 tests)`],
      risk_level: null, prompt_version: 'run_compare_v2', model_name: null, generated_at: null, report_status: 'absent',
    },
    requires_human_review: true,
    review: null,
    ai_disclaimer: null,
    ai_disclaimer_version: null,
    ...overrides,
  }
}
