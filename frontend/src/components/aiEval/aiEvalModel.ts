/**
 * The AGT-09 tabs' pure model (W16 G-E): the server's vocabularies turned into the labels, tones and copy the
 * components render — one place, so a component and its test read the same string.
 *
 * The Gate tab used to compare `PASS` / `FAIL` against a server that answers its `EvalVerdict` VALUES (`pass` /
 * `fail` / `insufficient_samples`): every run toasted "No baseline configured" and the result card had no colour.
 * Every verdict here is read in the server's own words (`verdictLabel`, `verdictTone`); an unknown string renders as
 * itself, never as a guess.
 *
 * Absence is not health: a metric computed from zero scored items is `—` + `Not measured`, never `0%`; a run without
 * `metric_kind` (an older server, a legacy row) is the legacy label-agreement metric.
 */
import { extractErrorMessage } from '@/services/apiErrors'
import { DEFAULT_GATES, EVAL_VERDICT_LABELS, METRIC_LABELS, type ReviewerQualityRun } from '@/types/aiEval'

export type Tone = 'success' | 'danger' | 'warning' | 'info' | 'muted'

/** The chip look per tone (§4.2): `--ui-*` families only, the legacy chip muted. */
export const TONE_CLASS: Record<Tone, string> = {
  success: 'border-[var(--ui-success-bd)] bg-[var(--ui-success-bg)] text-[var(--ui-success-ink)]',
  danger: 'border-[var(--ui-danger-bd)] bg-[var(--ui-danger-bg)] text-[var(--ui-danger-ink)]',
  warning: 'border-[var(--ui-warning-bd)] bg-[var(--ui-warning-bg)] text-[var(--ui-warning-ink)]',
  info: 'border-[var(--ui-info-bd)] bg-[var(--ui-info-bg)] text-[var(--ui-info-ink)]',
  muted: 'border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)]',
}

/** The one chip shape (a text-2xs pill); the tone class goes after it. */
export const CHIP_CLASS = 'inline-flex shrink-0 items-center whitespace-nowrap rounded border px-1.5 py-px text-2xs font-medium'

export interface ChipSpec {
  label: string
  tone: Tone
  title?: string
}

export const COPY = {
  needsAdmin: 'Needs an administrator.',
  allProjectsReplay: 'Choose a project in the top bar: the replay uses its LLM budget and release policy.',
  noBaselines: 'No baselines yet. Set one from a candidate run (Datasets) or a gate result.',
  reviewerReadOnly:
    "Measured nightly from the reviewer's recorded observations. This page never changes the reviewer's configuration.",
  reviewerProjectRequired: 'Reviewer quality is measured per project: choose one to see its history.',
  reviewerEmpty: 'No reviewer-quality measurement recorded for this project yet.',
  stackHistoryEmpty: 'No agent-stack gate has been run yet.',
  runsEmpty: 'No evaluation runs for this dataset yet.',
  itemsEmpty: 'No items in this view.',
  legacyItems: 'A legacy label-agreement run stores no per-item outputs.',
  notMeasured: 'Not measured',
  legacyChip: 'Legacy label agreement',
  legacyMetric: METRIC_LABELS.label_agreement,
  replayMetric: METRIC_LABELS.replay,
  loadError: {
    stack: 'Could not load the agent-stack history.',
    runs: 'Could not load the evaluation runs.',
    items: "Could not load the run's items.",
    reviewer: 'Could not load the reviewer-quality history.',
    options: 'Could not load the candidate options.',
    manifest: 'Could not load the manifest.',
  },
} as const

// ── Verdicts ────────────────────────────────────────────────────────────────

/** `pass` → `PASS`, `fail` → `FAIL`, `insufficient_samples` → `Not enough evidence`; an unknown verdict as itself. */
export function verdictLabel(status: string | null | undefined): string {
  if (!status) return '—'
  return (EVAL_VERDICT_LABELS as Record<string, string>)[status] ?? status
}

export function verdictTone(status: string | null | undefined): Tone {
  if (status === 'pass') return 'success'
  if (status === 'fail') return 'danger'
  if (status === 'insufficient_samples') return 'warning'
  return 'muted'
}

/**
 * A refused write's sentence, shown where the action was taken (the writes pass `suppressToast`): the `{code,
 * message}` detail's message, a string detail, or `fallback` when the server sent nothing readable.
 */
export function refusalText(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail
  return extractErrorMessage(detail, fallback)
}

export function verdictChip(status: string | null | undefined): ChipSpec {
  return { label: verdictLabel(status), tone: verdictTone(status) }
}

/**
 * The gate result in the vocabulary the shared evaluation-workflow timeline still reads (`PASS` / `WARN` / other):
 * without it a passing gate drew as a failed stage (components/workflow/workflowPresets.ts compares `'PASS'`).
 * `insufficient_samples` is undecided, not failed: `WARN`.
 */
export function workflowGateResult<T extends { status: string }>(result: T | null): T | null {
  if (!result) return null
  const status = result.status === 'pass' ? 'PASS' : result.status === 'fail' ? 'FAIL' : 'WARN'
  return { ...result, status }
}

// ── Metrics ─────────────────────────────────────────────────────────────────

export interface MetricLike {
  value?: number | null
  measured?: boolean
}

export function isMeasured(metric: MetricLike | null | undefined): boolean {
  return !!metric && metric.measured !== false && typeof metric.value === 'number' && Number.isFinite(metric.value)
}

/** A 0..1 rate as `84.0%`; nothing to show → `—`. */
export function percent(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '—'
}

/** A metric's text: `84.0%`, or `—` when it was not measured (zero scored items) or carries no value. */
export function metricText(metric: MetricLike | null | undefined): string {
  return isMeasured(metric) ? percent(metric?.value) : '—'
}

// ── Runs ────────────────────────────────────────────────────────────────────

/** The run fields the tabs read (an `AIEvalRun`; every W16 field optional — a legacy row has none of them). */
export interface RunLike {
  metric_kind?: string | null
  status?: string | null
  candidate?: { tier?: string | null; model?: string | null; provider?: string | null } | null
  accuracy?: number | null
  f1_score?: number | null
  measured?: boolean
  total_items?: number | null
  scored_items?: number | null
  unscored_items?: number | null
  error_items?: number | null
  items_capped?: boolean | null
}

export const TIER_LABELS: Record<string, string> = { rules: 'Rules', ml: 'ML', llm: 'Local model' }

export function tierLabel(tier: string | null | undefined): string {
  if (!tier) return '—'
  return TIER_LABELS[tier] ?? tier
}

/** `metric_kind` missing (an older server, a legacy row) → the legacy label-agreement metric. */
export function isReplay(run: RunLike | null | undefined): boolean {
  return run?.metric_kind === 'replay'
}

/** The Overview / runs-list chip: `Legacy label agreement` (muted) or `Replay · {tier}` (info). */
export function metricChip(run: RunLike): ChipSpec {
  if (!isReplay(run)) return { label: COPY.legacyChip, tone: 'muted', title: COPY.legacyMetric }
  const tier = run.candidate?.tier
  return { label: tier ? `Replay · ${tierLabel(tier)}` : 'Replay', tone: 'info', title: COPY.replayMetric }
}

const RUN_STATUS: Record<string, ChipSpec> = {
  queued: { label: 'Queued', tone: 'info' },
  running: { label: 'Running', tone: 'info' },
  completed: { label: 'Completed', tone: 'success' },
  partial: { label: 'Partial', tone: 'warning' },
  failed: { label: 'Failed', tone: 'danger' },
}

/** A legacy row has no status column: it is `completed` (the column default, §5). */
export function runStatusChip(status: string | null | undefined): ChipSpec {
  const key = status || 'completed'
  return RUN_STATUS[key] ?? { label: key, tone: 'muted' }
}

export function isActiveRun(run: RunLike | null | undefined): boolean {
  return run?.status === 'queued' || run?.status === 'running'
}

function count(n: number | null | undefined): number {
  return typeof n === 'number' && Number.isFinite(n) ? n : 0
}

/** `{scored + unscored + error} of {total_items} items` — the polling line of a queued / running replay. */
export function runProgress(run: RunLike): string {
  const done = count(run.scored_items) + count(run.unscored_items) + count(run.error_items)
  return `${done} of ${count(run.total_items)} items`
}

/** `{n} scored · {u} unscored · {e} errors` (a terminal replay row). */
export function runCounts(run: RunLike): string {
  const errors = count(run.error_items)
  return `${count(run.scored_items)} scored · ${count(run.unscored_items)} unscored · ${errors} ${errors === 1 ? 'error' : 'errors'}`
}

/** A run's accuracy / F1 as a metric: a replay with nothing scored (`measured: false`) is not measured. */
export function runMetric(run: RunLike, field: 'accuracy' | 'f1_score'): MetricLike {
  return { value: run[field] ?? null, measured: run.measured !== false }
}

// ── Candidate options ───────────────────────────────────────────────────────

/** `llm_policy_service.LOCAL_PROVIDERS`: an `llm` tier on one of these is a local model. */
export const LOCAL_PROVIDERS: readonly string[] = ['ollama', 'lmstudio', 'localai', 'vllm']

export interface TierOptionLike {
  tier: string
  model?: string | null
  provider?: string | null
}

/**
 * The radio label: `Rules`, `ML`, `Local model · {provider} · {model}`. An `llm` tier on a cloud provider (offline
 * mode off) is `Language model · …`: calling it local would be untrue.
 */
export function candidateLabel(option: TierOptionLike): string {
  if (option.tier !== 'llm') return tierLabel(option.tier)
  const local = !option.provider || LOCAL_PROVIDERS.includes(option.provider.toLowerCase())
  return [local ? 'Local model' : 'Language model', option.provider, option.model].filter(Boolean).join(' · ')
}

// ── Items (the per-item diff) ───────────────────────────────────────────────

const ITEM_STATUS: Record<string, ChipSpec> = {
  correct: { label: 'Correct', tone: 'success' },
  incorrect: { label: 'Incorrect', tone: 'danger' },
  unscored: { label: 'Unscored', tone: 'warning' },
  error: { label: 'Error', tone: 'warning' },
}

export function itemStatusChip(status: string | null | undefined): ChipSpec {
  return ITEM_STATUS[status ?? ''] ?? { label: status || '—', tone: 'muted' }
}

/** The status filter chips above the items table: `All` is no filter. */
export const ITEM_FILTERS: readonly { id: string; label: string; status: string | undefined }[] = [
  { id: 'all', label: 'All', status: undefined },
  { id: 'incorrect', label: 'Incorrect', status: 'incorrect' },
  { id: 'unscored', label: 'Unscored', status: 'unscored' },
  { id: 'error', label: 'Error', status: 'error' },
]

export interface ItemLike {
  item_index: number
  item_key?: string | null
  status?: string | null
  input?: Record<string, unknown> | null
  expected?: Record<string, unknown> | null
  output?: Record<string, unknown> | null
  latency_ms?: number | null
  mode_used?: string | null
}

export interface ItemCells {
  item: string
  expected: string
  candidate: string
  engine: string
  ms: string
}

/** A scalar as text; anything else (an object, a list) is not a table cell: `—`. */
function scalar(value: unknown): string {
  if (typeof value === 'string') return value.trim() || '—'
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  return '—'
}

function duplicateText(value: unknown, ratio?: unknown): string {
  if (typeof value !== 'boolean') return '—'
  const word = value ? 'Duplicate' : 'Distinct'
  return typeof ratio === 'number' && Number.isFinite(ratio) ? `${word} (${ratio.toFixed(2)})` : word
}

/** The scored field per task type, expected vs the candidate's output (§2.1's table): plain text for the cells. */
export function itemCells(taskType: string, item: ItemLike): ItemCells {
  const input = item.input ?? {}
  const expected = item.expected ?? {}
  const output = item.output ?? null
  let want = '—'
  let got = '—'
  if (taskType === 'classification') {
    want = scalar(expected.failure_category)
    got = output ? scalar(output.failure_category) : '—'
  } else if (taskType === 'root_cause') {
    want = scalar(expected.category)
    got = output ? scalar(output.failure_category) : '—'
  } else if (taskType === 'duplicate_detection') {
    want = duplicateText(expected.is_duplicate)
    got = output ? duplicateText(output.predicted_duplicate, output.ratio) : '—'
  } else if (taskType === 'release_decision') {
    want = scalar(expected.recommendation)
    got = output ? scalar(output.recommendation) : '—'
  }
  const label =
    (item.item_key && item.item_key.trim()) ||
    [input.test_name, input.title_a].map(scalar).find((v) => v !== '—') ||
    `Item ${item.item_index + 1}`
  return {
    item: label,
    expected: want,
    candidate: got,
    engine: item.mode_used ? tierLabel(item.mode_used) : '—',
    ms: typeof item.latency_ms === 'number' ? String(item.latency_ms) : '—',
  }
}

/** A value for the diff's `<pre>`: JSON text (React renders it as text; nothing is parsed as markup). */
export function prettyJson(value: unknown): string {
  if (value === undefined || value === null) return '—'
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

// ── Gates and baselines ─────────────────────────────────────────────────────

export interface GateSpec {
  task_type: string
  agent_name: string
  label: string
}

/** The gate of a task type (`DEFAULT_GATES` mirrors the server's `DEFAULT_AGENT_STACK_GATES`). */
export function gateFor(taskType: string | null | undefined): GateSpec | undefined {
  return (DEFAULT_GATES as readonly GateSpec[]).find((gate) => gate.task_type === taskType)
}

export function taskLabel(taskType: string | null | undefined): string {
  return gateFor(taskType)?.label ?? (taskType || '—')
}

/** The Set-as-baseline question (an InlineConfirm). */
export function baselineConfirmSentence(taskType: string, agentName: string): string {
  return (
    `Make this run the ${taskLabel(taskType)} baseline for ${agentName}? ` +
    'The gate compares later candidates against its accuracy and F1.'
  )
}

/** A checksum's first 12 characters (the full one goes in `title`). */
export function shortChecksum(checksum: string | null | undefined): string {
  return checksum ? checksum.slice(0, 12) : '—'
}

/**
 * The body Set as baseline sends for a gate result: a baseline of the result's OWN metric kind (§7.2 D-17) — a
 * replay result names its run (`eval_run_id`), a legacy result sends today's `{task_type, agent_name}`.
 */
export function baselineBody(result: {
  task_type: string
  agent_name: string
  metric_kind?: string | null
  candidate_run_id?: string | null
}): { task_type: string; agent_name: string; eval_run_id?: string } {
  const base = { task_type: result.task_type, agent_name: result.agent_name }
  return result.metric_kind === 'replay' && result.candidate_run_id ? { ...base, eval_run_id: result.candidate_run_id } : base
}

// ── Reviewer quality (G3) ───────────────────────────────────────────────────

/** The four headline G3 metrics, by their name in a row's `metrics` list (reviewer_quality_service). */
export const REVIEWER_CARDS: readonly { name: string; title: string; field: keyof ReviewerQualityRun }[] = [
  { name: 'deterministic_semantic_recall', title: 'Deterministic recall', field: 'deterministic_recall' },
  { name: 'second_model_semantic_recall', title: 'Second-model recall', field: 'second_model_recall' },
  { name: 'clean_false_flag_rate', title: 'False-flag rate', field: 'false_flag_rate' },
  { name: 'human_false_omission_rate', title: 'False-omission rate', field: 'false_omission_rate' },
]

/** A signed difference in percentage points: `+4.0 pp`, `−2.5 pp`; nothing → `—`. */
export function pointsText(delta: number | null | undefined): string {
  if (typeof delta !== 'number' || !Number.isFinite(delta)) return '—'
  const pp = (delta * 100).toFixed(1)
  return delta > 0 ? `+${pp} pp` : delta < 0 ? `−${pp.slice(1)} pp` : `${pp} pp`
}

/** `manual-YYYY-MM-DD` for the given day (the Agent stack form's default change id). */
export function defaultChangeId(now: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `manual-${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}
