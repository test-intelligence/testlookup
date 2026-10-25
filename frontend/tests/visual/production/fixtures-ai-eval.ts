/**
 * W16 G-A shared hermetic world for `/settings/ai-eval` (`docs/deferred-scope-2026-10-13/work/W16-contract.md`
 * §1.1, §2.1, §2.2, §3, §4.2, §6.2): `aiEvalWorld({ role, projectId, datasets, runs, items, gateRuns, manifests,
 * tierRuns, reviewerRuns, pairs, options, writes })`, used by `ai-eval-gates.spec.ts` (G-E),
 * `ai-eval-shadow-labelling.spec.ts` (G-D) and the two visual specs.
 *
 * Every §4.2 path is answered with the REAL server shape: the four older page reads as the server sends them
 * (`/dashboard` an `AIQualityDashboardResponse`; `/datasets`, `/report-cycles`, `/baselines` bare lists — those
 * routes answer lists), and every W16 list as `{ items, total }` (`/runs/{id}/items` adds `counts`,
 * `/shadow-pairs` adds `pending_total` / `live_tier_pairs_seen`) — never a bare `[]` where the server sends an
 * object (W15's CI broke on one). A queued run advances ONE status per poll after it was first seen (queued →
 * running → completed), its counters and metrics from its own stored items. Every write is recorded in `writes`
 * (`{ line, body }`); a refusal is the server's own (`detail: { code, message }` for W16's, FastAPI's role /
 * project strings otherwise); a write the world does not model is recorded AND answered with that 4xx shape, so
 * a spec that expects no write fails on it.
 *
 * Roles: `admin` (every control) or `qa_lead` (reads; the ADMIN routes answer 403 `Requires at least ADMIN
 * role`). The world's project is the member project; any other `project_id` answers 403 `You do not have access
 * to this project`, and another project's pair 404 `shadow_pair_not_found`. Fresh state per call. Model output
 * is run data rendered as TEXT; no id looks like an e-mail, a phone or a card number. Not a spec file.
 */
import { respond, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import { isoAgo, LAYOUT, NOW, PROJECT_ID, USER } from './fixtures'
import { ADMIN } from './fixtures-ai-config'
import { bodyOf } from './fixtures-in-page'

export type AiEvalRole = 'admin' | 'qa_lead'
type Row = Record<string, unknown>
type RunRow = Row & { id: string; dataset_id: string; task_type: string; status: string; metric_kind: string }
type ItemRow = Row & { item_index: number; status: string }
type PairRow = Row & { id: string; project_id: string; agent_id: string; label_status: string }

/** Every write the world answered (or refused), with its body. */
export interface AiEvalWrite {
  line: string
  body: unknown
}

const API = '/api/v1/ai-eval'
export const AI_EVAL_PATHS = {
  dashboard: `${API}/dashboard`,
  datasets: `${API}/datasets`,
  reportCycles: `${API}/report-cycles`,
  readiness: `${API}/report-cycles/readiness`,
  baselines: `${API}/baselines`,
  runs: `${API}/runs`,
  candidateOptions: `${API}/candidate-options`,
  preReleaseGate: `${API}/pre-release-gate`,
  agentStackGate: `${API}/agent-stack-release-gate`,
  agentStackRuns: `${API}/agent-stack-release-gate/runs`,
  tierComparison: `${API}/tier-comparison`,
  tierRuns: `${API}/tier-comparison/runs`,
  reviewerRuns: `${API}/reviewer-quality/runs`,
  shadowPairs: `${API}/shadow-pairs`,
} as const
const RUN_PATH = /^\/api\/v1\/ai-eval\/runs\/([^/]+)$/
const RUN_ITEMS_PATH = /^\/api\/v1\/ai-eval\/runs\/([^/]+)\/items$/
const EVALUATE_PATH = /^\/api\/v1\/ai-eval\/runs\/evaluate\/([^/]+)$/
const MANIFEST_PATH = /^\/api\/v1\/ai-eval\/gates\/([^/]+)$/
const LABEL_PATH = /^\/api\/v1\/ai-eval\/shadow-pairs\/([^/]+)\/label$/
const ANY_AI_EVAL = /^\/api\/v1\/ai-eval(\/.*)?$/

const P = PROJECT_ID
/** A project the world's user is NOT a member of (an IDOR probe's project). */
export const OTHER_PROJECT_ID = '22222222-2222-4222-8222-222222222222'

// ── Ids (UUIDs: the routes type them so; letters first, never a phone / card look) ─────────────────────────

export const DATASET_IDS = {
  classification: 'dadc1a55-0001-4abc-8def-c1a55a1f1ca1',
  root_cause: 'dadc1a55-0002-4abc-8def-a00c0a05e0a2',
  duplicate_detection: 'dadc1a55-0003-4abc-8def-d0b1eca7e0a3',
  release_decision: 'dadc1a55-0004-4abc-8def-ae1ea5edec04',
} as const
export const LEGACY_RUN_IDS = {
  classification: 'aeae1e9a-0001-4abc-8def-1e9ac0c1a551',
  root_cause: 'aeae1e9a-0002-4abc-8def-1e9aca05ea02',
} as const
export const LEGACY_BASELINE_ID = 'bae11e9a-0001-4abc-8def-ba5e1e9ac1a5'
/** The id of the n-th run the world creates (POST `/runs/evaluate`), 1-based. */
export const createdRunId = (n: number) => `ceeda1a0-${String(n).padStart(4, '0')}-4abc-8def-cafebeefface`

/** The capability each shadow agent compares in G2 (`agent.{stage}.v1`). */
export const SHADOW_CAPABILITIES: Record<string, string> = {
  'agent.root_cause_analysis.v1': 'root_cause_analysis',
  'agent.summary.v1': 'summary',
}

// ── Vocabulary (mirrors services/ai_eval_vocab.py; the TS constants are pinned by the parity test) ────────────

const CANDIDATE_TIERS = ['rules', 'ml', 'llm']
const TASK_TIERS: Record<string, string[]> = {
  classification: ['rules', 'ml', 'llm'], root_cause: ['rules', 'ml', 'llm'],
  duplicate_detection: ['rules'], release_decision: ['rules'],
}
const ITEM_STATUSES = ['correct', 'incorrect', 'unscored', 'error']
const RUN_STATUSES = ['queued', 'running', 'completed', 'partial', 'failed']
const SHADOW_VERDICTS = ['incumbent', 'candidate', 'both', 'neither']
const SHADOW_LABEL_STATUSES = ['pending', 'human_labelled', 'golden_match']
const SHADOW_AGENT_LABELS: Record<string, string> = {
  'agent.root_cause_analysis.v1': 'Root-cause analysis', 'agent.summary.v1': 'Run summary',
}
const FAILURE_CATEGORIES = ['PRODUCT_BUG', 'INFRASTRUCTURE', 'TEST_DATA', 'AUTOMATION_DEFECT', 'FLAKY', 'UNKNOWN']
const METRIC_LABELS: Record<string, string> = {
  label_agreement: 'Legacy label-agreement metric', replay: 'Replay of the candidate',
}
const TIER_UNAVAILABLE_ML = 'No ML model is trained yet; the ML tier would answer with the rules engine.'
export const PROMPT_TAG = 'fast_classifier_system@v1:9d46ffbbed7b+react_triage@v3:e71c26f0ad74'

const refuse = (status: number, code: string, message: string) => respond(status, { detail: { code, message } })
const needsAdmin = () => respond(403, { detail: 'Requires at least ADMIN role' })
const noProject = () => respond(403, { detail: 'You do not have access to this project' })
const DATASET_NAMES: Record<string, string> = {
  classification: 'Golden: Failure Classification', root_cause: 'Golden: Root Cause Extraction',
  duplicate_detection: 'Golden: Duplicate Detection', release_decision: 'Golden: Release Decision',
}
const DATASET_SIZES: Record<string, number> = { classification: 12, root_cause: 10, duplicate_detection: 8, release_decision: 6 }

// ── Rows (the server's shapes) ──────────────────────────────────────────────────────────────────────────────

export type TaskType = keyof typeof DATASET_IDS

/** An `AIEvalDatasetResponse` (the golden set of `taskType`). */
export function datasetRow(taskType: TaskType, over: Row = {}): Row {
  return {
    id: DATASET_IDS[taskType], name: DATASET_NAMES[taskType], description: 'Version-controlled golden set.',
    task_type: taskType, item_count: DATASET_SIZES[taskType], is_active: true, created_by: null,
    created_at: isoAgo(20), updated_at: isoAgo(20), ...over,
  }
}

/** The four seeded golden datasets. */
export function goldenDatasets(): Row[] {
  return (Object.keys(DATASET_IDS) as TaskType[]).map((task) => datasetRow(task))
}

/** What `AIEvalRunResponse`'s validator derives: the label, and `measured: false` on a replay that scored nothing. */
function derive(run: RunRow): RunRow {
  run.metric_label = METRIC_LABELS[run.metric_kind] ?? run.metric_kind
  run.measured = !(run.metric_kind === 'replay' && !Number(run.scored_items))
  return run
}

/** An `AIEvalRunResponse` (§1.1): every W16 field present; a legacy label-agreement row unless overridden. */
export function evalRun(over: Partial<RunRow> & { id: string }): RunRow {
  return derive({
    dataset_id: DATASET_IDS.classification, model_name: 'AnalysisAgent', model_version_id: null,
    task_type: 'classification', precision: null, recall: null, f1_score: null, accuracy: null, agreement_rate: null,
    total_items: 12, correct_items: 0, fallback_used: false, evaluated_at: isoAgo(1), duration_ms: 40,
    metric_kind: 'label_agreement', metric_label: '', status: 'completed', error_code: null, error_text: null,
    candidate: null, project_id: null, requested_by: null, started_at: null, completed_at: null, heartbeat_at: null,
    scored_items: 0, unscored_items: 0, error_items: 0, items_capped: false, metrics: null, measured: true,
    ...over,
  } as RunRow)
}

/** The nightly legacy rows the Overview's Recent runs list (never recomputed). */
export function legacyRuns(): RunRow[] {
  return [
    evalRun({
      id: LEGACY_RUN_IDS.classification, accuracy: 0.82, precision: 0.81, recall: 0.79, f1_score: 0.8,
      correct_items: 10, total_items: 12, evaluated_at: isoAgo(1, 8),
    }),
    evalRun({
      id: LEGACY_RUN_IDS.root_cause, dataset_id: DATASET_IDS.root_cause, task_type: 'root_cause',
      accuracy: 0.7, precision: 0.7, recall: 0.7, f1_score: 0.7, correct_items: 7, total_items: 10,
      evaluated_at: isoAgo(1, 8),
    }),
  ]
}

/** The planned answer of a candidate run: `good` gates PASS against itself, `bad` FAILs against a good baseline. */
export type CandidatePlan = 'good' | 'bad'

const CATEGORIES = ['PRODUCT_BUG', 'INFRASTRUCTURE', 'TEST_DATA', 'AUTOMATION_DEFECT', 'FLAKY']
const TEST_NAMES = [
  'test_checkout_total', 'test_login_redirect', 'test_export_csv', 'test_profile_avatar', 'test_cart_badge',
  'test_refund_flow', 'test_search_suggest', 'test_invoice_pdf', 'test_password_reset', 'test_order_history',
  'test_coupon_apply', 'test_shipping_quote',
]
const ERRORS = [
  'AssertionError: expected total 42.10 to equal 40.00', 'TimeoutError: page.goto exceeded 30000 ms',
  'KeyError: column "amount" missing in fixture', 'ElementNotFound: avatar upload button',
  'Flaky: passed on retry 2 of 3',
]

/** Which items are unscored / incorrect under a plan (index sets over `total` items). */
function planned(plan: CandidatePlan, total: number): (index: number) => 'correct' | 'incorrect' | 'unscored' {
  const incorrect = plan === 'good' ? new Set([1]) : new Set([1, 4, 5, 7, 9])
  return (i) => (i === 2 ? 'unscored' : incorrect.has(i % total) ? 'incorrect' : 'correct')
}

/** One stored `AIEvalItemOutputRead` of a replay of `task` by `tier`. */
function itemRow(task: string, tier: string, index: number, status: 'correct' | 'incorrect' | 'unscored'): ItemRow {
  const truth = CATEGORIES[index % CATEGORIES.length]
  const answered = status === 'incorrect' ? CATEGORIES[(index + 2) % CATEGORIES.length] : truth
  const base = { item_index: index, item_key: `golden-${task}-${String(index + 1).padStart(3, '0')}`, status,
    error_code: null, error_text: null, latency_ms: 8 + (index % 5) * 3, tokens: null, mode_used: tier }
  if (task === 'duplicate_detection') {
    const duplicate = index % 2 === 0
    const predicted = status === 'incorrect' ? !duplicate : duplicate
    return { ...base, input: { title_a: `Checkout total wrong on ${TEST_NAMES[index]}`, title_b: `Total mismatch in ${TEST_NAMES[index]}` },
      expected: status === 'unscored' ? {} : { is_duplicate: duplicate },
      output: { ratio: predicted ? 0.8125 : 0.3333, predicted_duplicate: predicted, detector_path: 'title_ratio' },
      score: status === 'unscored' ? {} : { match: status === 'correct' } }
  }
  if (task === 'release_decision') {
    const truthRec = ['GO', 'CONDITIONAL_GO', 'NO_GO'][index % 3]
    const rec = status === 'incorrect' ? ['GO', 'CONDITIONAL_GO', 'NO_GO'][(index + 1) % 3] : truthRec
    return { ...base, input: { risk_score: 20 + index * 7, pass_rate: 0.98 - index * 0.04 },
      expected: status === 'unscored' ? {} : { recommendation: truthRec },
      output: { recommendation: rec, overall_result: rec, effective_composite: 20 + index * 7, policy_level: 'simulated',
        policy_version: null, rule_results: [] },
      score: status === 'unscored' ? {} : { match: status === 'correct' } }
  }
  const input = { test_name: TEST_NAMES[index % TEST_NAMES.length], error_message: ERRORS[index % ERRORS.length] }
  if (task === 'root_cause') {
    return { ...base, input,
      expected: status === 'unscored' ? { has_root_cause: true } : { category: truth, has_root_cause: true },
      output: { root_cause_summary: `${ERRORS[index % ERRORS.length].split(':')[0]} in ${input.test_name}`,
        failure_category: answered, recommended_actions: ['Re-run the failing step locally'], mode_used: tier },
      score: status === 'unscored' ? {} : { match: status === 'correct', mentions_file: false, actionable: true } }
  }
  return { ...base, input, expected: status === 'unscored' ? {} : { failure_category: truth },
    output: { failure_category: answered, confidence_score: 0.9 - (index % 4) * 0.1,
      root_cause_summary: ERRORS[index % ERRORS.length], mode_used: tier, fallback_reason: null },
    score: status === 'unscored' ? {} : { match: status === 'correct' } }
}

/** The stored items of a finished replay (`total` items, statuses per `plan`). */
export function replayItems(task: string, tier: string, plan: CandidatePlan, total: number): ItemRow[] {
  const statusOf = planned(plan, total)
  return Array.from({ length: total }, (_, i) => itemRow(task, tier, i, statusOf(i)))
}

const round4 = (n: number) => Math.round(n * 10_000) / 10_000

/** `finish_run` over STORED items: counters, the four scores (over scored items only; none → null, not 0 %). */
export function aggregateRun(run: RunRow, items: readonly ItemRow[]): RunRow {
  const count = (s: string) => items.filter((i) => i.status === s).length
  const correct = count('correct')
  const scored = correct + count('incorrect')
  const accuracy = scored ? round4(correct / scored) : null
  const recall = accuracy === null ? null : round4(Math.max(0, accuracy - 0.02))
  const f1 = accuracy === null || recall === null || accuracy + recall === 0
    ? null : round4((2 * accuracy * recall) / (accuracy + recall))
  const metric = (name: string, value: number | null) => ({ name, value, n: scored, measured: value !== null, kind: 'score' })
  Object.assign(run, {
    correct_items: correct, scored_items: scored, unscored_items: count('unscored'), error_items: count('error'),
    accuracy, precision: accuracy, recall, f1_score: f1,
    metrics: [metric('accuracy', accuracy), metric('precision', accuracy), metric('recall', recall), metric('f1_score', f1)],
  })
  return derive(run)
}

/** A completed replay of `task` by `tier` with its items (for a world that starts with one). */
export function completedReplay(
  id: string, task: TaskType, tier = 'rules', plan: CandidatePlan = 'good',
): { run: RunRow; items: ItemRow[] } {
  const total = DATASET_SIZES[task]
  const items = replayItems(task, tier, plan, total)
  const run = evalRun({
    id, dataset_id: DATASET_IDS[task], task_type: task, model_name: tier, metric_kind: 'replay', status: 'completed',
    total_items: total, candidate: resolvedCandidate(tier), project_id: P, requested_by: ADMIN.id,
    started_at: isoAgo(0, 3), completed_at: isoAgo(0, 3), heartbeat_at: isoAgo(0, 3), evaluated_at: isoAgo(0, 3),
  })
  return { run: aggregateRun(run, items), items }
}

/** `EvalCandidateResolved` (`ai_eval_runs.candidate`). */
function resolvedCandidate(tier: string, requested: Row = { tier }): Row {
  const llm = tier === 'llm'
  return {
    tier, model: llm ? 'llama3.1:8b-instruct' : null, provider: llm ? 'ollama' : null,
    prompt_version: llm ? PROMPT_TAG : null, offline_mode: true, requested: { model: null, prompt_version: null, ...requested },
  }
}

/** `GET /baselines` row (the route's dict; `is_active` is the world's own bookkeeping, stripped on read). */
export function baselineRow(over: Row = {}): Row {
  return {
    id: LEGACY_BASELINE_ID, task_type: 'classification', agent_name: 'AnalysisAgent', prompt_version: 'v1',
    model_name: null, baseline_accuracy: 0.82, baseline_precision: 0.81, baseline_recall: 0.79, baseline_f1: 0.8,
    min_accuracy: 0.8, min_f1: 0.75, max_regression_pct: 5, created_at: isoAgo(10), metric_kind: 'label_agreement',
    eval_run_id: LEGACY_RUN_IDS.classification, dataset_id: DATASET_IDS.classification, is_active: true, ...over,
  }
}

/** `CandidateOptionsResponse`: rules and the local model available, ML not trained, offline mode on. */
export function candidateOptions(over: Row = {}): Row {
  return {
    tiers: [
      { tier: 'rules', available: true, reason: null, model: null, provider: null },
      { tier: 'ml', available: false, reason: TIER_UNAVAILABLE_ML, model: null, provider: null },
      { tier: 'llm', available: true, reason: null, model: 'llama3.1:8b-instruct', provider: 'ollama' },
    ],
    prompt_version: PROMPT_TAG, offline_mode: true, max_items: 200, supported_tiers_by_task: TASK_TIERS, ...over,
  }
}

/** A deterministic 64-hex checksum of `seed` (FNV-1a, repeated): stands for the manifest's SHA-256. */
export function checksumOf(seed: string): string {
  let out = ''
  for (let round = 0; out.length < 64; round += 1) {
    let h = 0x811c9dc5 ^ round
    for (const ch of `${round}:${seed}`) h = Math.imul(h ^ ch.charCodeAt(0), 0x01000193) >>> 0
    out += h.toString(16).padStart(8, 'a')
  }
  return out.slice(0, 64)
}

/** The agent stack's per-gate verdicts the world answers (`DEFAULT_AGENT_STACK_GATES` order). */
export const DEFAULT_STACK_STATUSES: Record<TaskType, string> = {
  classification: 'pass', root_cause: 'pass', duplicate_detection: 'fail', release_decision: 'insufficient_samples',
}
const STACK_AGENTS: Record<TaskType, string> = {
  classification: 'AnalysisAgent', root_cause: 'AnalysisAgent',
  duplicate_detection: 'DefectPromotionAgent', release_decision: 'ReleaseRiskAgent',
}

/** One pre-release gate result as the agent stack embeds it (legacy label-agreement metric). */
function stackGateEntry(task: TaskType, status: string, evaluatedAt: string): Row {
  const accuracy = status === 'pass' ? 0.86 : status === 'fail' ? 0.58 : null
  return {
    status, task_type: task, agent_name: STACK_AGENTS[task],
    current_metrics: accuracy === null ? null : { accuracy, precision: accuracy, recall: accuracy, f1_score: accuracy, total: 12, correct: Math.round(accuracy * 12) },
    baseline_metrics: null,
    rule_results: accuracy === null
      ? [{ rule: 'dataset_exists', passed: false, detail: 'No evaluation dataset found' }]
      : [{ rule: 'default_min_accuracy', passed: accuracy >= 0.7, detail: `Accuracy ${accuracy.toFixed(4)} (no baseline — using 0.70 default)` },
        { rule: 'sufficient_data', passed: true, detail: 'Dataset has 12 items' }],
    evaluated_at: evaluatedAt, metric_kind: 'label_agreement', candidate_run_id: null, baseline_run_id: null, dataset_id: null,
  }
}

/** An agent-stack run: the POST answer and the history row it persists (`gate_type` null), from per-gate statuses. */
export function agentStackRun(changeId: string, statuses: Record<TaskType, string>, evaluatedAt: string, id: string) {
  const gates = (Object.keys(STACK_AGENTS) as TaskType[]).map((t) => ({ task_type: t, agent_name: STACK_AGENTS[t] }))
  const manifestBody = { schema_version: 1, change_id: changeId, prompt_versions: {}, model_versions: {}, routing_versions: {}, required_gates: gates }
  const checksum = checksumOf(JSON.stringify(manifestBody))
  const manifest = { ...manifestBody, manifest_checksum_sha256: checksum }
  const gateResults = gates.map((g) => stackGateEntry(g.task_type, statuses[g.task_type], evaluatedAt))
  const blocking = gateResults
    .filter((r) => r.status === 'fail' || r.status === 'insufficient_samples')
    .map((r) => ({ task_type: r.task_type, agent_name: r.agent_name, status: r.status }))
  const status = blocking.length ? 'fail' : 'pass'
  const versionChanges = gates.map((g) => ({
    task_type: g.task_type, agent_name: g.agent_name,
    prompt_version: { candidate: null, baseline: null, changed: false },
    model_name: { candidate: null, baseline: null, changed: false },
    routing_version: { candidate: null, baseline: null, changed: false },
  }))
  const answer = { status, manifest, gate_results: gateResults, blocking_gates: blocking, version_changes: versionChanges, evaluated_at: evaluatedAt, gate_run_id: id }
  const row = {
    id, change_id: changeId, status, manifest_checksum_sha256: checksum, manifest, gate_results: gateResults,
    blocking_gates: blocking, version_changes: versionChanges, gate_type: null, project_id: null, agent_id: null,
    baseline_tier: null, candidate_tier: null, sample_count: null, evaluated_by: ADMIN.id, evaluated_at: evaluatedAt,
  }
  return { answer, row }
}

/** The agent-stack history the world starts with: one nightly FAIL (duplicate detection blocking). */
export function defaultGateRuns(): Row[] {
  return [agentStackRun('nightly-2026-09-17', DEFAULT_STACK_STATUSES, isoAgo(1, 8), 'aa9e0001-0001-4abc-8def-9a7e5a1ac0a1').row]
}

/** A recorded G2 comparison (`AIEvalGateRunResponse` with `gate_type = 'tier_comparison'`) of the world's project. */
export function tierRunRow(over: Row = {}): Row {
  // A comparison recorded before any pair was labelled: no evidence yet (the honest insufficient_samples).
  const manifest = { schema_version: 1, gate: 'G2', capability: 'root_cause_analysis', incumbent_tier: 'llm',
    candidate_tier: 'slm', sample_ids: [], delta: 0.05, live_label_count: 0 }
  return {
    id: 'aa9e0002-0002-4abc-8def-71e9c0a9a1e2', change_id: 'tier-comparison', status: 'insufficient_samples',
    manifest_checksum_sha256: checksumOf(JSON.stringify(manifest)), manifest, gate_results: [], blocking_gates: [],
    version_changes: [], gate_type: 'tier_comparison', project_id: P, agent_id: 'agent.root_cause_analysis.v1',
    baseline_tier: 'llm', candidate_tier: 'slm', sample_count: 0, evaluated_by: USER.id, evaluated_at: isoAgo(2, 4),
    ...over,
  }
}

/** A G3 row of `/reviewer-quality/runs` (no observation arrays). */
export function reviewerRunRow(over: Row = {}): Row {
  return {
    id: 'aa9e0003-0003-4abc-8def-a1e0e1ea0a03', project_id: P, agent_id: 'agent.reviewer.v1', source: 'scheduled',
    status: 'pass', window_started_at: isoAgo(8), window_ended_at: isoAgo(1), evaluated_at: isoAgo(1, 3),
    // The names `reviewer_quality_service` stores (`_metric(...)`): the cards look their 95 % bounds up by name.
    metrics: [
      { name: 'deterministic_semantic_recall', value: 0.84, n: 120, ci_low: 0.77, ci_high: 0.89, kind: 'ground_truth' },
      { name: 'second_model_semantic_recall', value: 0.91, n: 120, ci_low: 0.85, ci_high: 0.95, kind: 'ground_truth' },
      { name: 'clean_false_flag_rate', value: 0.04, n: 200, ci_low: 0.02, ci_high: 0.08, kind: 'ground_truth' },
      { name: 'human_false_omission_rate', value: 0.03, n: 14, ci_low: 0.01, ci_high: 0.31, kind: 'ground_truth' },
    ],
    regressions: [], semantic_sample_counts: { mutation: 120, clean: 200 }, clean_sample_count: 200,
    human_outcome_count: 14, deterministic_recall: 0.84, second_model_recall: 0.91, recall_delta: 0.07,
    false_flag_rate: 0.04, false_omission_rate: 0.03, auto_disable_eligible: false, auto_disable_applied: false,
    ...over,
  }
}

/** The G3 history the world starts with: last night's scheduled run and an older observation batch. */
export function defaultReviewerRuns(): Row[] {
  return [
    reviewerRunRow(),
    reviewerRunRow({
      id: 'aa9e0004-0004-4abc-8def-a1e0e1ea0a04', source: 'observation_batch', status: 'insufficient_samples',
      window_started_at: isoAgo(15), window_ended_at: isoAgo(8), evaluated_at: isoAgo(8, 2), human_outcome_count: 3,
      deterministic_recall: 0.8, second_model_recall: null, recall_delta: null, false_flag_rate: null,
      false_omission_rate: null,
    }),
  ]
}

const PAIR_IDS = [
  'a5a10001-0001-4abc-8def-5ad0ba1ea001', 'a5a10002-0002-4abc-8def-5ad0ba1ea002', 'a5a10003-0003-4abc-8def-5ad0ba1ea003',
]
export { PAIR_IDS as SHADOW_PAIR_IDS }
/** A pair of ANOTHER project (the label route must answer it like a missing id). */
export const FOREIGN_PAIR_ID = 'a5a1f0e1-0009-4abc-8def-f0ae1ea0a009'

/** A stored `ShadowPairRead` (pending, root-cause, resolved to a run of the world's project unless overridden). */
export function shadowPairRow(over: Partial<PairRow> & { id: string }): PairRow {
  const created = String(over.created_at ?? isoAgo(2))
  return {
    project_id: P, agent_id: 'agent.root_cause_analysis.v1', agent_label: 'Root-cause analysis',
    sample_key: 'root_cause:ca5ec0de-0001-4abc-8def-7e57ca5e0001', incumbent_tier: 'llm', candidate_tier: 'slm',
    incumbent_output: { failure_category: 'PRODUCT_BUG', root_cause_summary: 'The order total ignores the coupon discount.' },
    candidate_output: { failure_category: 'TEST_DATA', root_cause_summary: 'The fixture prices are stale.' },
    incumbent_tokens: 410, candidate_tokens: 95, label_status: 'pending', label: null, labelled_by_name: null,
    labelled_at: null, created_at: created,
    source: { kind: 'test_case', id: 'ca5ec0de-0001-4abc-8def-7e57ca5e0001', label: 'test_checkout_total',
      run_id: 'aa1d0001-0001-4abc-8def-a1dbea7e0001', build_number: '128',
      href: '/runs/aa1d0001-0001-4abc-8def-a1dbea7e0001?tab=tests' },
    expires_at: new Date(Date.parse(created) + 30 * 86_400_000).toISOString(),
    ...over,
  } as PairRow
}

/** The queue the world starts with: three pending root-cause pairs, the third with no source any more. */
export function defaultShadowPairs(): PairRow[] {
  return [
    shadowPairRow({ id: PAIR_IDS[0], created_at: isoAgo(1, 2) }),
    shadowPairRow({
      id: PAIR_IDS[1], created_at: isoAgo(3), sample_key: 'root_cause:ca5ec0de-0002-4abc-8def-7e57ca5e0002',
      incumbent_output: { failure_category: 'INFRASTRUCTURE', root_cause_summary: 'The payments sandbox timed out.' },
      candidate_output: { failure_category: 'INFRASTRUCTURE', root_cause_summary: 'Gateway timeout from the sandbox.' },
      source: { kind: 'test_case', id: 'ca5ec0de-0002-4abc-8def-7e57ca5e0002', label: 'test_refund_flow',
        run_id: 'aa1d0002-0002-4abc-8def-a1dbea7e0002', build_number: '127',
        href: '/runs/aa1d0002-0002-4abc-8def-a1dbea7e0002?tab=tests' },
    }),
    shadowPairRow({
      id: PAIR_IDS[2], created_at: isoAgo(12), sample_key: 'root_cause:ca5ec0de-0003-4abc-8def-7e57ca5e0003',
      source: null,
    }),
    // Another project's pending pair: never listed, and its id answers 404 like a random one.
    shadowPairRow({ id: FOREIGN_PAIR_ID, project_id: OTHER_PROJECT_ID }),
  ]
}

/** `AIQualityDashboardResponse` around the world's runs (agreement, label health; no drift, no provenance gap). */
function dashboardBody(recent: RunRow[]): Row {
  return {
    agreement: { total_feedback: 48, correct: 39, partially_correct: 5, incorrect: 4, agreement_rate: 0.8125, period_days: 30 },
    drift: null,
    recent_eval_runs: recent,
    model_versions: [],
    feedback_summary: null,
    label_health: {
      human_direct: 31, human_indirect: 9, human_label_total: 40, llm_pseudo_candidates: 12, human_share_of_pool: 0.77,
      human_label_floor: 50, meets_human_label_floor: false, ml_maturity: 'not_trained', last_trained_composition: null,
      pseudo_label_cap: 0.3, pseudo_label_weight: 0.5,
    },
    eval_provenance: null,
  }
}

// ── The world ───────────────────────────────────────────────────────────────────────────────────────────────

export interface AiEvalWorldOptions {
  /** `admin` (default: every control) or `qa_lead` (reads; ADMIN routes answer 403). */
  role?: AiEvalRole
  /** The member project (default the fixture project); any other `project_id` is refused. */
  projectId?: string
  datasets?: Row[]
  /** Stored runs (default the two legacy nightly rows). */
  runs?: RunRow[]
  /** Stored items by run id. */
  items?: Record<string, ItemRow[]>
  baselines?: Row[]
  gateRuns?: Row[]
  /** Extra manifests by checksum (every gate run's own manifest is answered already). */
  manifests?: Record<string, Row>
  tierRuns?: Row[]
  reviewerRuns?: Row[]
  pairs?: PairRow[]
  /** `CandidateOptionsResponse` (default rules + local model available, ML not trained). */
  options?: Row
  reportCycles?: Row[]
  /** The agent stack's per-gate verdicts on Run gate. */
  stackStatuses?: Record<TaskType, string>
  /** The plan of each candidate run the world creates, in order (default good, then bad, then good …). */
  candidatePlans?: CandidatePlan[]
  /** Record into this array instead of a new one. */
  writes?: AiEvalWrite[]
}

export function aiEvalWorld(opts: AiEvalWorldOptions = {}) {
  const role: AiEvalRole = opts.role ?? 'admin'
  const project = opts.projectId ?? P
  const user = role === 'admin' ? ADMIN : USER
  const writes = opts.writes ?? []
  const datasets = (opts.datasets ?? goldenDatasets()).map((d) => ({ ...d }))
  const runs = new Map<string, RunRow>((opts.runs ?? legacyRuns()).map((r) => [r.id, { ...r }]))
  const items = new Map<string, ItemRow[]>(Object.entries(opts.items ?? {}))
  const baselines = (opts.baselines ?? [baselineRow()]).map((b) => ({ ...b }))
  const gateRuns = (opts.gateRuns ?? defaultGateRuns()).map((g) => ({ ...g }))
  const manifests = new Map<string, Row>(Object.entries(opts.manifests ?? {}))
  const tierRuns = (opts.tierRuns ?? [tierRunRow()]).map((t) => ({ ...t }))
  const reviewerRuns = (opts.reviewerRuns ?? defaultReviewerRuns()).map((r) => ({ ...r }))
  const pairs = new Map<string, PairRow>((opts.pairs ?? defaultShadowPairs()).map((p) => [p.id, { ...p }]))
  const options = opts.options ?? candidateOptions()
  const plans = new Map<string, { plan: CandidatePlan; tier: string }>()
  const seenActive = new Set<string>()
  const plansInOrder = opts.candidatePlans ?? ['good', 'bad']
  let created = 0
  let stackRuns = 0

  const isAdmin = role === 'admin'
  const record = (request: ApiRequest, body: unknown) => writes.push({ line: `${request.method} ${request.path}`, body })
  const page = <T>(list: T[], request: ApiRequest, fallback: number): T[] => {
    const limit = Number(request.url.searchParams.get('limit') ?? fallback)
    const offset = Number(request.url.searchParams.get('offset') ?? 0)
    return list.slice(offset, offset + limit)
  }
  const newestFirst = (a: Row, b: Row) => String(b.evaluated_at).localeCompare(String(a.evaluated_at))
  const datasetOf = (id: string) => datasets.find((d) => d.id === id)

  /** The items a run has stored so far: none while queued, a prefix while running, every item once done. */
  function storedItems(run: RunRow): ItemRow[] {
    const planOf = plans.get(run.id)
    if (!planOf) return items.get(run.id) ?? []
    if (run.status === 'queued') return []
    const all = items.get(run.id) ?? replayItems(run.task_type, planOf.tier, planOf.plan, Number(run.total_items))
    items.set(run.id, all)
    if (run.status === 'running') return all.slice(0, 4)
    return all
  }

  /** One poll of an active run: first seen as it is, then one status further per poll. */
  function advance(run: RunRow) {
    if (run.status !== 'queued' && run.status !== 'running') return
    if (!seenActive.has(run.id)) {
      seenActive.add(run.id)
      return
    }
    if (run.status === 'queued') {
      Object.assign(run, { status: 'running', started_at: NOW.toISOString(), heartbeat_at: NOW.toISOString() })
      aggregateRun(run, storedItems(run))
      Object.assign(run, { accuracy: null, precision: null, recall: null, f1_score: null, metrics: null })
    } else {
      Object.assign(run, { status: 'completed', completed_at: NOW.toISOString(), heartbeat_at: NOW.toISOString() })
      aggregateRun(run, storedItems(run))
    }
  }

  /** The active baseline of `(task, agent)` (the newest active row). */
  const activeBaseline = (task: string, agent: string) =>
    baselines.find((b) => b.is_active && b.task_type === task && b.agent_name === agent)
  const baselineRead = ({ is_active: _active, ...row }: Row) => row

  const reportCycles = opts.reportCycles ?? []

  /** GET `/runs`: the filters, every matching active run advanced one poll, `{ items, total }`. */
  function listRuns(request: ApiRequest) {
    const q = request.url.searchParams
    const metricKind = q.get('metric_kind')
    const status = q.get('status')
    if (metricKind !== null && !(metricKind in METRIC_LABELS)) {
      return refuse(422, 'metric_kind_invalid', 'metric_kind must be label_agreement or replay.')
    }
    if (status !== null && !RUN_STATUSES.includes(status)) {
      return refuse(422, 'status_invalid', 'status must be queued, running, completed, partial or failed.')
    }
    const matching = [...runs.values()]
      .filter((r) => !q.get('dataset_id') || r.dataset_id === q.get('dataset_id'))
      .filter((r) => !q.get('task_type') || r.task_type === q.get('task_type'))
      .filter((r) => !metricKind || r.metric_kind === metricKind)
      .filter((r) => !status || r.status === status)
      .sort(newestFirst)
    matching.forEach(advance)
    return { items: page(matching, request, 20), total: matching.length }
  }

  /** GET `/runs/{id}/items` (ADMIN): the stored items, filtered and paged, with the run's counts. */
  function listItems(request: ApiRequest) {
    if (!isAdmin) return needsAdmin()
    const status = request.url.searchParams.get('status')
    if (status !== null && !ITEM_STATUSES.includes(status)) {
      return refuse(422, 'status_invalid', 'status must be correct, incorrect, unscored or error.')
    }
    const run = runs.get(decodeURIComponent(RUN_ITEMS_PATH.exec(request.path)?.[1] ?? ''))
    if (!run) return refuse(404, 'eval_run_not_found', 'Evaluation run not found.')
    if (run.metric_kind !== 'replay') return { items: [], total: 0, counts: {} }
    const stored = storedItems(run)
    const counts: Record<string, number> = {}
    for (const item of stored) counts[item.status] = (counts[item.status] ?? 0) + 1
    const filtered = status ? stored.filter((i) => i.status === status) : stored
    return { items: page(filtered, request, 25), total: filtered.length, counts }
  }

  /** A per-project QUERY read: the member project only (the helper's 403 otherwise). */
  const memberOnly = (request: ApiRequest): boolean => request.url.searchParams.get('project_id') === project

  const reads: ApiHandlers = [
    [AI_EVAL_PATHS.dashboard, () => dashboardBody([...runs.values()].sort(newestFirst).slice(0, 10))],
    [AI_EVAL_PATHS.datasets, ({ url }) => {
      const task = url.searchParams.get('task_type')
      return datasets.filter((d) => d.is_active !== false && (!task || d.task_type === task))
    }],
    [AI_EVAL_PATHS.reportCycles, () => reportCycles],
    [AI_EVAL_PATHS.readiness, ({ url }) => ({
      status: 'not_ready', latest_status: 'missing', consecutive_passes: 0, required_consecutive_passes: 2,
      utility_rate: null, minimum_utility_rate: 0.8, corpus_version: url.searchParams.get('corpus_version'),
      corpus_sha256: null, reasons: ['latest_cycle_missing'],
    })],
    [AI_EVAL_PATHS.baselines, ({ url }) => {
      const task = url.searchParams.get('task_type')
      return baselines
        .filter((b) => b.is_active && (!task || b.task_type === task))
        .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
        .map(baselineRead)
    }],
    [AI_EVAL_PATHS.candidateOptions, () => (isAdmin ? options : needsAdmin())],
    [AI_EVAL_PATHS.runs, listRuns],
    [RUN_ITEMS_PATH, listItems],
    [RUN_PATH, ({ path }) => {
      const run = runs.get(decodeURIComponent(RUN_PATH.exec(path)?.[1] ?? ''))
      if (!run) return refuse(404, 'eval_run_not_found', 'Evaluation run not found.')
      advance(run)
      return run
    }],
    [AI_EVAL_PATHS.agentStackRuns, (request) => {
      const status = request.url.searchParams.get('status')
      const rows = gateRuns.filter((g) => g.gate_type == null && (!status || g.status === status)).sort(newestFirst)
      return { items: page(rows, request, 20), total: rows.length }
    }],
    [MANIFEST_PATH, ({ path }) => {
      const checksum = decodeURIComponent(MANIFEST_PATH.exec(path)?.[1] ?? '').toLowerCase()
      const extra = manifests.get(checksum)
      if (extra) return extra
      const row = gateRuns.find((g) => g.manifest_checksum_sha256 === checksum)
      if (!row) return respond(404, { detail: 'Evaluation manifest not found' })
      return {
        eval_manifest_checksum: checksum, source: 'database_gate', status: row.status, manifest: row.manifest,
        gate_run_id: row.id, evaluated_at: row.evaluated_at,
      }
    }],
    [AI_EVAL_PATHS.tierRuns, (request) => {
      if (!memberOnly(request)) return noProject()
      const agent = request.url.searchParams.get('agent_id')
      const rows = tierRuns
        .filter((t) => t.gate_type === 'tier_comparison' && t.project_id === project && (!agent || t.agent_id === agent))
        .sort(newestFirst)
      return { items: page(rows, request, 20), total: rows.length }
    }],
    [AI_EVAL_PATHS.reviewerRuns, (request) => {
      if (!memberOnly(request)) return noProject()
      const rows = reviewerRuns.filter((r) => r.project_id === project).sort(newestFirst)
      return { items: page(rows, request, 20), total: rows.length }
    }],
    [AI_EVAL_PATHS.shadowPairs, listPairs],
  ]

  /** GET `/shadow-pairs`: the member project only; agent / status filters INSIDE it; newest first. */
  function listPairs(request: ApiRequest) {
    if (!memberOnly(request)) return noProject()
    const q = request.url.searchParams
    const agent = q.get('agent_id')
    const labelStatus = q.get('label_status') ?? 'pending'
    if (agent !== null && !(agent in SHADOW_AGENT_LABELS)) {
      return refuse(422, 'agent_id_invalid', 'agent_id must be agent.root_cause_analysis.v1 or agent.summary.v1.')
    }
    if (labelStatus !== 'all' && !SHADOW_LABEL_STATUSES.includes(labelStatus)) {
      return refuse(422, 'label_status_invalid', 'label_status must be pending, human_labelled, golden_match or all.')
    }
    const own = [...pairs.values()].filter((p) => p.project_id === project)
    const rows = own
      .filter((p) => (!agent || p.agent_id === agent) && (labelStatus === 'all' || p.label_status === labelStatus))
      .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
    return {
      items: page(rows, request, 25),
      total: rows.length,
      pending_total: own.filter((p) => p.label_status === 'pending').length,
      live_tier_pairs_seen: own.length > 0,
    }
  }

  /** POST `/shadow-pairs/{id}/label`: another project's pair is the SAME 404 as a missing id. */
  function labelPair(request: ApiRequest) {
    const body = (bodyOf(request) ?? {}) as { verdict?: string; failure_category?: string | null; notes?: string | null }
    record(request, body)
    const pair = pairs.get(decodeURIComponent(LABEL_PATH.exec(request.path)?.[1] ?? ''))
    if (!pair || pair.project_id !== project) return refuse(404, 'shadow_pair_not_found', 'Shadow pair not found.')
    if (pair.label_status !== 'pending') {
      return refuse(409, 'shadow_pair_already_labelled', `This pair is already labelled (${pair.label_status}).`)
    }
    if (!SHADOW_VERDICTS.includes(String(body.verdict))) {
      return refuse(422, 'verdict_invalid', 'verdict must be incumbent, candidate, both or neither.')
    }
    if (body.failure_category != null) {
      if (pair.agent_id !== 'agent.root_cause_analysis.v1') {
        return refuse(422, 'failure_category_not_applicable', 'failure_category applies to root-cause pairs only.')
      }
      if (!FAILURE_CATEGORIES.includes(body.failure_category)) {
        return refuse(422, 'failure_category_invalid',
          `failure_category must be one of ${FAILURE_CATEGORIES.join(', ')}.`)
      }
    }
    Object.assign(pair, {
      label_status: 'human_labelled',
      label: {
        verdict: body.verdict,
        ...(body.failure_category ? { failure_category: body.failure_category } : {}),
        ...(body.notes ? { notes: body.notes } : {}),
        source: 'human',
      },
      labelled_by_name: user.full_name, labelled_at: NOW.toISOString(), expires_at: null,
    })
    return { ...pair, source: null }
  }

  /** POST `/tier-comparison` (QA lead+): `pairs: []` + the project's live labels → G2 over them. */
  function compareTiers(request: ApiRequest) {
    const body = (bodyOf(request) ?? {}) as Row
    record(request, body)
    if (body.project_id !== project) return noProject()
    const agent = String(body.agent_id ?? '')
    const capability = String(body.capability ?? '')
    if (SHADOW_CAPABILITIES[agent] !== capability) {
      return respond(422, { detail: 'agent_id does not match the capability registry entry' })
    }
    const incumbent = String(body.incumbent_tier)
    const candidate = String(body.candidate_tier)
    const live = body.include_live_labels
      ? [...pairs.values()].filter((p) => p.project_id === project && p.agent_id === agent
        && p.incumbent_tier === incumbent && p.candidate_tier === candidate && p.label_status !== 'pending')
      : []
    const scored = live.map((p) => {
      const verdict = String((p.label as Row | null)?.verdict ?? 'neither')
      return {
        sample_id: `live:${p.id}`, incumbent_correct: verdict === 'incumbent' || verdict === 'both',
        candidate_correct: verdict === 'candidate' || verdict === 'both', incumbent_valid: true, candidate_valid: true,
        incumbent_cost_usd: 0, candidate_cost_usd: 0, incumbent_latency_ms: 0, candidate_latency_ms: 0,
        incumbent_tokens: Number(p.incumbent_tokens), candidate_tokens: Number(p.candidate_tokens),
        provenance: p.label_status,
      }
    })
    const result = g2Answer(capability, incumbent, candidate, Number(body.delta ?? 0.05), scored)
    if (body.persist !== false) {
      const id = `aa9e7100-${String(tierRuns.length + 1).padStart(4, '0')}-4abc-8def-71e9c0a9a1e2`
      tierRuns.push(tierRunRow({
        id, status: result.verdict, manifest: result.manifest, manifest_checksum_sha256: result.manifest_checksum,
        agent_id: agent, baseline_tier: incumbent, candidate_tier: candidate, sample_count: result.sample_count,
        evaluated_by: user.id, evaluated_at: NOW.toISOString(),
      }))
      return { ...result, gate_run_id: id }
    }
    return result
  }

  /** POST `/runs/evaluate/{dataset_id}` (ADMIN): no body = the legacy run (201); a body = a queued replay (202). */
  function evaluate(request: ApiRequest) {
    const body = bodyOf(request) as { candidate?: { tier?: string; model?: string | null; prompt_version?: string | null }; project_id?: string; limit?: number } | null
    record(request, body)
    if (!isAdmin) return needsAdmin()
    const dataset = datasetOf(decodeURIComponent(EVALUATE_PATH.exec(request.path)?.[1] ?? ''))
    if (!dataset) return respond(404, { detail: 'Dataset not found' })
    const task = String(dataset.task_type)
    if (!body) {
      const legacy = evalRun({
        id: createdRunId(900 + runs.size), dataset_id: String(dataset.id), task_type: task, accuracy: 0.82,
        precision: 0.81, recall: 0.79, f1_score: 0.8, total_items: Number(dataset.item_count),
        correct_items: Math.round(Number(dataset.item_count) * 0.82), evaluated_at: NOW.toISOString(),
      })
      runs.set(legacy.id, legacy)
      return respond(201, legacy)
    }
    if (body.project_id !== project) return noProject()
    const tier = String(body.candidate?.tier ?? '')
    if (!CANDIDATE_TIERS.includes(tier)) return refuse(422, 'candidate_tier_invalid', 'tier must be rules, ml or llm.')
    const allowed = TASK_TIERS[task] ?? []
    if (!allowed.includes(tier)) {
      return refuse(422, 'candidate_tier_unsupported', `${task} replays the ${allowed.join(' or ')} tier only.`)
    }
    const option = (options.tiers as Row[]).find((t) => t.tier === tier)
    if (!option?.available) return refuse(422, 'candidate_tier_unavailable', String(option?.reason ?? 'Unavailable.'))
    if (tier === 'llm' && body.candidate?.model && body.candidate.model !== option.model) {
      return refuse(422, 'candidate_model_not_configured',
        `Only the configured model (${String(option.model)}) can be evaluated; configure ${body.candidate.model} under Settings › AI first.`)
    }
    if (body.candidate?.prompt_version && body.candidate.prompt_version !== options.prompt_version) {
      return refuse(422, 'candidate_prompt_not_current',
        `Only the current prompt version (${String(options.prompt_version)}) can be replayed.`)
    }
    const busy = [...runs.values()].some((r) => r.dataset_id === dataset.id && r.metric_kind === 'replay'
      && (r.status === 'queued' || r.status === 'running') && (r.candidate as Row | null)?.tier === tier)
    if (busy) return refuse(409, 'candidate_run_in_progress', `A ${tier} evaluation of this dataset is already running.`)
    const size = Number(dataset.item_count)
    if (!size) return refuse(422, 'dataset_empty', 'The dataset has no items to replay.')
    created += 1
    const total = Math.min(size, body.limit ?? 200, 200)
    const run = evalRun({
      id: createdRunId(created), dataset_id: String(dataset.id), task_type: task, metric_kind: 'replay', status: 'queued',
      model_name: (tier === 'llm' && option.model ? String(option.model) : tier), total_items: total, items_capped: total < size,
      candidate: resolvedCandidate(tier, { tier, model: body.candidate?.model ?? null, prompt_version: body.candidate?.prompt_version ?? null }),
      project_id: project, requested_by: user.id, evaluated_at: NOW.toISOString(), duration_ms: null,
    })
    runs.set(run.id, run)
    plans.set(run.id, { plan: plansInOrder[(created - 1) % plansInOrder.length] ?? 'good', tier })
    return respond(202, { run_id: run.id, status: 'queued' })
  }

  /** The gate's rules (`_evaluate_rules`) of `current` against `baseline` (or the 0.70 default). */
  function rules(current: Row, baseline: Row | undefined): Row[] {
    const out: Row[] = []
    const accuracy = current.accuracy as number | null
    const f1 = current.f1_score as number | null
    if (baseline) {
      const minAcc = Number(baseline.min_accuracy)
      const minF1 = Number(baseline.min_f1)
      if (accuracy != null) {
        out.push({ rule: 'min_accuracy', passed: accuracy >= minAcc, detail: accuracy >= minAcc
          ? `Accuracy ${accuracy.toFixed(4)} >= ${minAcc.toFixed(2)} threshold` : `Accuracy ${accuracy.toFixed(4)} below ${minAcc.toFixed(2)} threshold` })
      }
      if (f1 != null) {
        out.push({ rule: 'min_f1', passed: f1 >= minF1, detail: f1 >= minF1
          ? `F1 ${f1.toFixed(4)} >= ${minF1.toFixed(2)} threshold` : `F1 ${f1.toFixed(4)} below ${minF1.toFixed(2)} threshold` })
      }
      if (accuracy != null && baseline.baseline_accuracy != null) {
        const drop = (Number(baseline.baseline_accuracy) - accuracy) * 100
        const max = Number(baseline.max_regression_pct)
        out.push({ rule: 'no_regression', passed: drop <= max, detail: drop <= max
          ? `Accuracy drop ${drop.toFixed(1)}% within ${max.toFixed(1)}% tolerance`
          : `Accuracy dropped ${drop.toFixed(1)}% (max allowed: ${max.toFixed(1)}%)`, severity: drop > 0 && drop <= max ? 'warn' : null })
      }
    } else if (accuracy != null) {
      out.push({ rule: 'default_min_accuracy', passed: accuracy >= 0.7, detail: `Accuracy ${accuracy.toFixed(4)} (no baseline — using 0.70 default)` })
    }
    const total = Number(current.total ?? 0)
    out.push({ rule: 'sufficient_data', passed: total >= 3, detail: `Dataset has ${total} items${total >= 3 ? '' : ' (minimum 3 required)'}` })
    return out
  }

  /** The run a candidate body names, if it is a completed replay of `task` (else the server's refusal). */
  function gateableRun(id: string, task: string): RunRow | ReturnType<typeof refuse> {
    const run = runs.get(id)
    if (!run) return refuse(404, 'candidate_run_not_found', 'Candidate run not found.')
    if (run.metric_kind !== 'replay' || run.status !== 'completed') {
      const shown = run.metric_kind === 'replay' ? run.status : 'a legacy label-agreement run'
      return refuse(409, 'candidate_run_not_complete', `Only a completed replay run can be gated; this one is ${shown}.`)
    }
    if (run.task_type !== task) {
      return refuse(422, 'candidate_run_task_mismatch', `The candidate run evaluated ${run.task_type}, not ${task}.`)
    }
    return run
  }

  const baselineMetrics = (b: Row | undefined) => (b ? {
    accuracy: b.baseline_accuracy, precision: b.baseline_precision, recall: b.baseline_recall, f1_score: b.baseline_f1,
    min_accuracy: b.min_accuracy, min_f1: b.min_f1, max_regression_pct: b.max_regression_pct,
    prompt_version: b.prompt_version, model_name: b.model_name, metric_kind: b.metric_kind,
    eval_run_id: b.eval_run_id, dataset_id: b.dataset_id,
  } : null)

  /** POST `/pre-release-gate` (ADMIN): the legacy metric, or (with `candidate_run_id`) the replay run's stored scores. */
  function preReleaseGate(request: ApiRequest) {
    const body = (bodyOf(request) ?? {}) as { task_type?: string; agent_name?: string; candidate_run_id?: string | null }
    record(request, body)
    if (!isAdmin) return needsAdmin()
    const task = String(body.task_type ?? 'classification')
    const agent = String(body.agent_name ?? 'AnalysisAgent')
    const baseline = activeBaseline(task, agent)
    const head = { task_type: task, agent_name: agent, evaluated_at: NOW.toISOString() }
    if (body.candidate_run_id) {
      const run = gateableRun(String(body.candidate_run_id), task)
      if (!('id' in run)) return run
      const current = { accuracy: run.accuracy, precision: run.precision, recall: run.recall, f1_score: run.f1_score,
        total: run.scored_items, correct: run.correct_items, unscored_items: run.unscored_items, error_items: run.error_items,
        metrics: run.metrics ?? [] }
      let unusable: Row | null = null
      if (baseline && baseline.metric_kind !== 'replay') {
        unusable = { rule: 'baseline_metric_kind', passed: false,
          detail: 'The active baseline uses the legacy label-agreement metric; set a replay run as the baseline first.' }
      } else if (baseline && baseline.dataset_id && baseline.dataset_id !== run.dataset_id) {
        unusable = { rule: 'baseline_dataset', passed: false, detail: 'The baseline was measured on another dataset.' }
      }
      const ruleResults = unusable ? [unusable, ...rules(current, undefined)] : rules(current, baseline)
      const status = unusable || !baseline ? 'insufficient_samples' : ruleResults.some((r) => !r.passed) ? 'fail' : 'pass'
      return { status, ...head, current_metrics: current, baseline_metrics: baselineMetrics(baseline), rule_results: ruleResults,
        metric_kind: 'replay', candidate_run_id: run.id, baseline_run_id: baseline?.eval_run_id ?? null, dataset_id: run.dataset_id }
    }
    const legacy = [...runs.values()].filter((r) => r.task_type === task && r.metric_kind === 'label_agreement').sort(newestFirst)[0]
    const tail = { metric_kind: 'label_agreement', candidate_run_id: null, baseline_run_id: baseline?.eval_run_id ?? null, dataset_id: null }
    if (!legacy) {
      return { status: 'insufficient_samples', ...head, current_metrics: null, baseline_metrics: null,
        rule_results: [{ rule: 'dataset_exists', passed: false, detail: 'No evaluation dataset found' }], ...tail }
    }
    const current = { accuracy: legacy.accuracy, precision: legacy.precision, recall: legacy.recall, f1_score: legacy.f1_score,
      total: legacy.total_items, correct: legacy.correct_items }
    const ruleResults = rules(current, baseline)
    const status = !baseline ? 'insufficient_samples' : ruleResults.some((r) => !r.passed) ? 'fail' : 'pass'
    return { status, ...head, current_metrics: current, baseline_metrics: baselineMetrics(baseline), rule_results: ruleResults, ...tail }
  }

  /** POST `/baselines` (ADMIN): with `eval_run_id` the replay run's scores (`replay-<tier>`); else a legacy baseline. */
  function setBaseline(request: ApiRequest) {
    const body = (bodyOf(request) ?? {}) as Row
    record(request, body)
    if (!isAdmin) return needsAdmin()
    const task = String(body.task_type ?? 'classification')
    const agent = String(body.agent_name ?? 'AnalysisAgent')
    let fields: Row
    if (body.eval_run_id) {
      const run = gateableRun(String(body.eval_run_id), task)
      if (!('id' in run)) return run
      const tier = String((run.candidate as Row | null)?.tier ?? 'unknown')
      fields = { prompt_version: `replay-${tier}`, model_name: (run.candidate as Row | null)?.model ?? null,
        baseline_accuracy: run.accuracy, baseline_precision: run.precision, baseline_recall: run.recall,
        baseline_f1: run.f1_score, metric_kind: 'replay', eval_run_id: run.id, dataset_id: run.dataset_id }
    } else {
      const legacy = [...runs.values()].filter((r) => r.task_type === task && r.metric_kind === 'label_agreement').sort(newestFirst)[0]
      if (!legacy) return respond(404, { detail: `No dataset found for task_type=${task}` })
      fields = { prompt_version: String(body.prompt_version ?? 'v1'), model_name: body.model_name ?? null,
        baseline_accuracy: legacy.accuracy, baseline_precision: legacy.precision, baseline_recall: legacy.recall,
        baseline_f1: legacy.f1_score, metric_kind: 'label_agreement', eval_run_id: null, dataset_id: body.dataset_id ?? null }
    }
    const thresholds = { min_accuracy: Number(body.min_accuracy ?? 0.8), min_f1: Number(body.min_f1 ?? 0.75),
      max_regression_pct: Number(body.max_regression_pct ?? 5) }
    let row = baselines.find((b) => b.task_type === task && b.agent_name === agent && b.prompt_version === fields.prompt_version)
    for (const b of baselines) if (b !== row && b.task_type === task && b.agent_name === agent) b.is_active = false
    if (!row) {
      row = { id: `bae11e9a-${String(baselines.length + 1).padStart(4, '0')}-4abc-8def-ba5e1e9ac1a5`, task_type: task, agent_name: agent }
      baselines.push(row)
    }
    Object.assign(row, fields, thresholds, { is_active: true, created_at: NOW.toISOString() })
    return respond(201, {
      baseline_id: row.id, task_type: task, agent_name: agent,
      metrics: { accuracy: row.baseline_accuracy, precision: row.baseline_precision, recall: row.baseline_recall, f1_score: row.baseline_f1 },
      thresholds, metric_kind: row.metric_kind, eval_run_id: row.eval_run_id,
    })
  }

  /** POST `/agent-stack-release-gate` (ADMIN): the four gates' verdicts, persisted as a history row. */
  function runAgentStack(request: ApiRequest) {
    const body = (bodyOf(request) ?? {}) as { change_id?: string }
    record(request, body)
    if (!isAdmin) return needsAdmin()
    const changeId = String(body.change_id ?? '').trim()
    if (!changeId) {
      return respond(422, { detail: [{ type: 'missing', loc: ['body', 'change_id'], msg: 'Field required', input: body }] })
    }
    stackRuns += 1
    const id = `aa9e5ac0-${String(stackRuns).padStart(4, '0')}-4abc-8def-5ac0a1ea0a01`
    const { answer, row } = agentStackRun(changeId, opts.stackStatuses ?? DEFAULT_STACK_STATUSES, NOW.toISOString(), id)
    gateRuns.push(row)
    return answer
  }

  const TIER_PATTERN = ['deterministic', 'slm', 'llm', 'auto']
  /** FastAPI's 422 for a tier outside `TierComparisonRequest`'s pattern; else the comparison. */
  function tierComparison(request: ApiRequest) {
    const body = (bodyOf(request) ?? {}) as Row
    const bad = (['incumbent_tier', 'candidate_tier'] as const).find((k) => !TIER_PATTERN.includes(String(body[k])))
    if (bad) {
      record(request, body)
      return respond(422, { detail: [{ type: 'string_pattern_mismatch', loc: ['body', bad],
        msg: "String should match pattern '^(deterministic|slm|llm|auto)$'", input: body[bad] }] })
    }
    return compareTiers(request)
  }

  /** A write this world does not model: recorded, and refused in the server's W16 shape (a spec expecting none fails). */
  const unmodelled = (request: ApiRequest) => {
    record(request, bodyOf(request))
    return refuse(405, 'not_modelled', `The hermetic AI-eval world does not model ${request.method} ${request.path}.`)
  }

  const writeHandlers: ApiHandlers = [
    [EVALUATE_PATH, evaluate, 'POST'],
    [AI_EVAL_PATHS.preReleaseGate, preReleaseGate, 'POST'],
    [AI_EVAL_PATHS.baselines, setBaseline, 'POST'],
    [AI_EVAL_PATHS.agentStackGate, runAgentStack, 'POST'],
    [AI_EVAL_PATHS.tierComparison, tierComparison, 'POST'],
    [LABEL_PATH, labelPair, 'POST'],
    ...(['POST', 'PUT', 'PATCH', 'DELETE'] as const).map((verb) => [ANY_AI_EVAL, unmodelled, verb] as const),
  ]

  return {
    /** Every §4.2 answer, then the app shell's (`LAYOUT`). */
    handlers: [...reads, ...writeHandlers, ...LAYOUT] as ApiHandlers,
    writes,
    /** The signed-in user (`ADMIN` or the QA-lead fixture) for `openRollout({ user })`. */
    user,
    project,
    runs,
    items,
    baselines,
    gateRuns,
    tierRuns,
    pairs,
    datasets,
  }
}

/** Wilson 95 % bounds of `k` of `n` (null when n is 0). */
function wilson(k: number, n: number): [number | null, number | null] {
  if (!n) return [null, null]
  const z = 1.96
  const p = k / n
  const centre = (p + (z * z) / (2 * n)) / (1 + (z * z) / n)
  const half = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / (1 + (z * z) / n)
  return [round4(Math.max(0, centre - half)), round4(Math.min(1, centre + half))]
}

/** The G2 answer (`compare_tier_outputs`) over scored rows: floors 100 (root cause) / 20, Wilson bounds. */
function g2Answer(capability: string, incumbent: string, candidate: string, delta: number, scored: Row[]) {
  const n = scored.length
  const inc = scored.filter((r) => r.incumbent_correct).length
  const cand = scored.filter((r) => r.candidate_correct).length
  const floor = capability === 'root_cause_analysis' ? 100 : 20
  const incCi = wilson(inc, n)
  const candCi = wilson(cand, n)
  const verdict = n < floor ? 'insufficient_samples' : 'pass'
  const reason = n < floor ? `need at least ${floor} paired samples; got ${n}` : 'candidate tier is non-inferior on the paired corpus'
  const sampleIds = scored.map((r) => String(r.sample_id)).sort()
  const manifestBody = { schema_version: 1, gate: 'G2', capability, incumbent_tier: incumbent, candidate_tier: candidate,
    sample_ids: sampleIds, delta, live_label_count: n }
  const checksum = checksumOf(JSON.stringify(manifestBody))
  const value = (k: number) => (n ? round4(k / n) : null)
  return {
    verdict, status: verdict, manifest_checksum: checksum, manifest: { ...manifestBody, manifest_checksum_sha256: checksum },
    per_gate: {
      G2: [
        { name: 'incumbent_accuracy', value: value(inc), n, ci_low: incCi[0], ci_high: incCi[1], kind: 'ground_truth' },
        { name: 'candidate_accuracy', value: value(cand), n, ci_low: candCi[0], ci_high: candCi[1], kind: 'ground_truth' },
        { name: 'paired_accuracy_delta', value: n ? round4((cand - inc) / n) : null, n, ci_low: null, ci_high: null, kind: 'ground_truth' },
        { name: 'candidate_validity', value: n ? 1 : null, n, ci_low: null, ci_high: null, kind: 'validity' },
      ],
    },
    regressions: verdict === 'pass' ? [] : [{ metric: 'paired_accuracy', baseline: value(inc), candidate: value(cand), ci_low: null, ci_high: null, tolerance: delta }],
    smoke_mode: n < 100, reason, sample_count: n, scored_pairs: scored, live_labelled_pairs: n,
  }
}
