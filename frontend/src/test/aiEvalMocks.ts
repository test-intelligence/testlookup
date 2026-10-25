/**
 * Stubs for `@/hooks/useAiEval` (W16), for the component and page vitests.
 * One set-up line per file:
 *
 *   vi.mock('@/hooks/useAiEval', async () => (await import('@/test/aiEvalMocks')).aiEvalHookMocks())
 *
 * Every read is a `vi.fn` answering the IDLE SWR shape (no data, no error, not
 * loading, a `mutate`), so a component renders without a request
 * (`src/test/networkGuard.ts` fails a test that makes one). Each mutation hook
 * answers a `vi.fn` (it is called, not destructured). Data per hook:
 * `aiEvalHookMocks({ useEvalRuns: { data: { items: [REPLAY_RUN_FIXTURE], total: 1 } } })`,
 * or `vi.mocked(useEvalRuns).mockReturnValue(...)` in a case.
 *
 * Deliberately imports nothing from `@/hooks/useAiEval`: this module is loaded
 * INSIDE that module's mock factory (that module exports hooks only). Test ids
 * start with a letter and mix case, so the redaction pass never reads one as
 * a phone or card number.
 */
import { vi, type Mock } from 'vitest'

import type { AIEvalRun } from '@/services/aiEvalService'
import type {
  EvalItemList,
  GateRunList,
  ShadowPair,
  ShadowPairList,
  TierComparisonResult,
} from '@/types/aiEval'

/** The reads of `@/hooks/useAiEval`, in the module's order. */
export const AI_EVAL_READ_HOOK_NAMES = [
  'useAgentStackGateRuns',
  'useEvalManifest',
  'useCandidateOptions',
  'useEvalRuns',
  'useEvalRun',
  'useEvalRunItems',
  'useTierComparisonRuns',
  'useReviewerQualityRuns',
  'useShadowPairs',
] as const
export type AiEvalReadHookName = (typeof AI_EVAL_READ_HOOK_NAMES)[number]

/** The mutations: each hook answers a function. */
export const AI_EVAL_MUTATION_HOOK_NAMES = ['useEvaluateCandidate', 'useSetBaseline', 'useLabelShadowPair'] as const
export type AiEvalMutationHookName = (typeof AI_EVAL_MUTATION_HOOK_NAMES)[number]
export type AiEvalHookName = AiEvalReadHookName | AiEvalMutationHookName

export interface AiEvalSwrStub {
  data: unknown
  error: unknown
  isLoading: boolean
  mutate: Mock
}

export function idleAiEvalSwr(overrides: Partial<AiEvalSwrStub> = {}): AiEvalSwrStub {
  return { data: undefined, error: undefined, isLoading: false, mutate: vi.fn(), ...overrides }
}

export type AiEvalHookOverrides = Partial<Record<AiEvalReadHookName, Partial<AiEvalSwrStub>>>
  & Partial<Record<AiEvalMutationHookName, Mock>>

/** Every `useAiEval` hook as a `vi.fn` (reads: the idle SWR answer merged with the override). */
export function aiEvalHookMocks(overrides: AiEvalHookOverrides = {}): Record<AiEvalHookName, Mock> {
  const mocks = {} as Record<AiEvalHookName, Mock>
  for (const name of AI_EVAL_READ_HOOK_NAMES) {
    const override = overrides[name] ?? {}
    mocks[name] = vi.fn(() => idleAiEvalSwr(override))
  }
  const evaluate = overrides.useEvaluateCandidate
    ?? vi.fn(async () => ({ run_id: REPLAY_RUN_ID, status: 'queued' }))
  const setBaseline = overrides.useSetBaseline ?? vi.fn(async () => ({ id: 'blA1b2C3d4' }))
  const label = overrides.useLabelShadowPair
    ?? vi.fn(async (pairId: string) => ({ ...SHADOW_PAIRS_FIXTURE.items[0], id: pairId, label_status: 'human_labelled' }))
  mocks.useEvaluateCandidate = vi.fn(() => evaluate)
  mocks.useSetBaseline = vi.fn(() => setBaseline)
  mocks.useLabelShadowPair = vi.fn(() => label)
  return mocks
}

// ── Fixtures ────────────────────────────────────────────────────────────────

export const PROJECT_FIXTURE_ID = 'prA1b2C3d4'
export const DATASET_FIXTURE_ID = 'dsA1b2C3d4'
export const REPLAY_RUN_ID = 'rnA1b2C3d4'
export const RUNNING_RUN_ID = 'rnB2c3D4e5'
const NOW = '2026-10-25T09:00:00+00:00'

function runFixture(overrides: Partial<AIEvalRun>): AIEvalRun {
  return {
    id: REPLAY_RUN_ID,
    dataset_id: DATASET_FIXTURE_ID,
    model_name: 'rules',
    task_type: 'classification',
    precision: null,
    recall: null,
    f1_score: null,
    accuracy: null,
    agreement_rate: null,
    total_items: 12,
    correct_items: 0,
    fallback_used: false,
    evaluated_at: NOW,
    duration_ms: null,
    metric_kind: 'replay',
    metric_label: 'Replay of the candidate',
    status: 'completed',
    error_code: null,
    error_text: null,
    candidate: {
      tier: 'rules',
      model: null,
      provider: null,
      prompt_version: null,
      offline_mode: false,
      requested: { tier: 'rules' },
    },
    project_id: PROJECT_FIXTURE_ID,
    requested_by: 'usA1b2C3d4',
    started_at: NOW,
    completed_at: NOW,
    heartbeat_at: NOW,
    scored_items: 0,
    unscored_items: 0,
    error_items: 0,
    items_capped: false,
    metrics: null,
    measured: true,
    ...overrides,
  }
}

/** A completed rules replay of 12 items: 9 correct, 2 incorrect, 1 unscored. */
export const REPLAY_RUN_FIXTURE: AIEvalRun = runFixture({
  accuracy: 9 / 11,
  precision: 0.8,
  recall: 0.78,
  f1_score: 0.79,
  correct_items: 9,
  scored_items: 11,
  unscored_items: 1,
  metrics: [
    { name: 'accuracy', value: 9 / 11, n: 11, measured: true, kind: 'score' },
    { name: 'precision', value: 0.8, n: 11, measured: true, kind: 'score' },
    { name: 'recall', value: 0.78, n: 11, measured: true, kind: 'score' },
    { name: 'f1_score', value: 0.79, n: 11, measured: true, kind: 'score' },
  ],
})

/** A rules replay part-way: 4 of 12 items stored. */
export const RUNNING_RUN_FIXTURE: AIEvalRun = runFixture({
  id: RUNNING_RUN_ID,
  status: 'running',
  completed_at: null,
  correct_items: 3,
  scored_items: 4,
  measured: true,
})

/** One item of each status (the replayed run's first page). */
export const ITEMS_FIXTURE: EvalItemList = {
  items: [
    {
      item_index: 0, item_key: 'golden-cls-001', status: 'correct', error_code: null, error_text: null,
      input: { test_name: 'test_checkout_total', error_message: 'AssertionError: expected 42 to equal 40' },
      expected: { failure_category: 'PRODUCT_BUG' },
      output: { failure_category: 'PRODUCT_BUG', confidence_score: 0.9, mode_used: 'rules' },
      score: { match: true }, latency_ms: 12, tokens: null, mode_used: 'rules',
    },
    {
      item_index: 1, item_key: 'golden-cls-002', status: 'incorrect', error_code: null, error_text: null,
      input: { test_name: 'test_login_redirect', error_message: 'TimeoutError: page.goto exceeded' },
      expected: { failure_category: 'INFRASTRUCTURE' },
      output: { failure_category: 'FLAKY', confidence_score: 0.6, mode_used: 'rules' },
      score: { match: false }, latency_ms: 9, tokens: null, mode_used: 'rules',
    },
    {
      item_index: 2, item_key: 'golden-cls-003', status: 'unscored', error_code: null, error_text: null,
      input: { test_name: 'test_export_csv', error_message: 'KeyError: column' },
      expected: {},
      output: { failure_category: 'TEST_DATA', confidence_score: 0.7, mode_used: 'rules' },
      score: {}, latency_ms: 8, tokens: null, mode_used: 'rules',
    },
    {
      item_index: 3, item_key: 'golden-cls-004', status: 'error', error_code: 'engine_error',
      error_text: 'The engine raised an error (ValueError).',
      input: { test_name: 'test_profile_avatar' },
      expected: { failure_category: 'AUTOMATION_DEFECT' },
      output: null, score: {}, latency_ms: 3, tokens: null, mode_used: null,
    },
  ],
  total: 4,
  counts: { correct: 1, incorrect: 1, unscored: 1, error: 1 },
}

/** Agent-stack history: one FAIL row (`gate_type` null — agent-stack rows never set it). */
export const GATE_RUNS_FIXTURE: GateRunList = {
  items: [
    {
      id: 'grA1b2C3d4',
      change_id: 'manual-2026-10-25',
      status: 'fail',
      manifest_checksum_sha256: 'cafe'.repeat(16),
      manifest: { schema_version: 1, change_id: 'manual-2026-10-25', required_gates: [] },
      gate_results: [
        { task_type: 'classification', agent_name: 'AnalysisAgent', status: 'pass' },
        { task_type: 'duplicate_detection', agent_name: 'DefectPromotionAgent', status: 'fail' },
      ],
      blocking_gates: [{ task_type: 'duplicate_detection', agent_name: 'DefectPromotionAgent', status: 'fail' }],
      version_changes: [],
      gate_type: null,
      project_id: null,
      agent_id: null,
      baseline_tier: null,
      candidate_tier: null,
      sample_count: null,
      evaluated_by: 'usA1b2C3d4',
      evaluated_at: NOW,
    },
  ],
  total: 1,
}

function pairFixture(overrides: Partial<ShadowPair>): ShadowPair {
  return {
    id: 'spA1b2C3d4',
    project_id: PROJECT_FIXTURE_ID,
    agent_id: 'agent.root_cause_analysis.v1',
    agent_label: 'Root-cause analysis',
    sample_key: 'root_cause:tcA1b2C3d4',
    incumbent_tier: 'llm',
    candidate_tier: 'slm',
    incumbent_output: { failure_category: 'PRODUCT_BUG', root_cause_summary: 'The total ignores the discount.' },
    candidate_output: { failure_category: 'TEST_DATA', root_cause_summary: 'Fixture prices are stale.' },
    incumbent_tokens: 410,
    candidate_tokens: 95,
    label_status: 'pending',
    label: null,
    labelled_by_name: null,
    labelled_at: null,
    created_at: NOW,
    source: {
      kind: 'test_case',
      id: 'tcA1b2C3d4',
      label: 'test_checkout_total',
      run_id: 'trA1b2C3d4',
      build_number: 'b-118',
      href: '/runs/trA1b2C3d4?tab=tests',
    },
    expires_at: '2026-11-24T09:00:00+00:00',
    ...overrides,
  }
}

/** Two pending root-cause pairs; the second's source is gone. */
export const SHADOW_PAIRS_FIXTURE: ShadowPairList = {
  items: [
    pairFixture({}),
    pairFixture({ id: 'spB2c3D4e5', sample_key: 'root_cause:tcB2c3D4e5', source: null }),
  ],
  total: 2,
  pending_total: 2,
  live_tier_pairs_seen: true,
}

/** A G2 answer below the floor: 3 live labelled pairs, `insufficient_samples`. */
export const TIER_RESULT_FIXTURE: TierComparisonResult = {
  verdict: 'insufficient_samples',
  status: 'insufficient_samples',
  reason: 'need at least 100 paired samples; got 3',
  sample_count: 3,
  smoke_mode: true,
  live_labelled_pairs: 3,
  manifest_checksum: 'beef'.repeat(16),
  per_gate: {
    G2: [
      { name: 'incumbent_accuracy', value: 2 / 3, n: 3, ci_low: 0.21, ci_high: 0.94, kind: 'ground_truth' },
      { name: 'candidate_accuracy', value: 1 / 3, n: 3, ci_low: 0.06, ci_high: 0.79, kind: 'ground_truth' },
      { name: 'paired_accuracy_delta', value: -1 / 3, n: 3, ci_low: -0.99, ci_high: 0.32, kind: 'ground_truth' },
    ],
  },
  regressions: [],
  scored_pairs: [
    { sample_id: 'live:spA1b2C3d4', incumbent_correct: true, candidate_correct: false, incumbent_valid: true, candidate_valid: true, provenance: 'human_labelled' },
    { sample_id: 'live:spB2c3D4e5', incumbent_correct: true, candidate_correct: true, incumbent_valid: true, candidate_valid: true, provenance: 'human_labelled' },
    { sample_id: 'live:spC3d4E5f6', incumbent_correct: false, candidate_correct: false, incumbent_valid: true, candidate_valid: true, provenance: 'golden_match' },
  ],
  gate_run_id: 'grB2c3D4e5',
}
