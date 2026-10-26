/**
 * The Tiers tab's pure model (W16 AGT-13): the copy, the two agents a shadow pair can come from, the G2 floor and
 * smoke lines, the queue's expiry / source text and the label body. Components and tests import one string from here.
 *
 * Stored model output is DATA: `outputText` turns it into text for a `<pre>` (JSON.stringify), never markup.
 */
import { extractErrorMessage } from '@/services/apiErrors'
import { EVAL_VERDICT_LABELS, type ShadowPairLabel, type ShadowPairSource } from '@/types/aiEval'

/** The two shadow-mode agents, their G2 capability and its sample floor (`agent_eval_samples.py:19-20`). */
export const TIER_AGENTS = [
  { id: 'agent.root_cause_analysis.v1', capability: 'root_cause_analysis', label: 'Root-cause analysis', floor: 100 },
  { id: 'agent.summary.v1', capability: 'summary', label: 'Run summary', floor: 20 },
] as const
export type TierAgent = (typeof TIER_AGENTS)[number]

/** Below this many paired samples G2 is a smoke gate (ANALYSIS_SAMPLE_FLOOR). */
export const SMOKE_FLOOR = 100
export const DEFAULT_DELTA = 0.05
export const ROOT_CAUSE_AGENT_ID = 'agent.root_cause_analysis.v1'
/** The failure categories a root-cause label may carry (`FailureCategory`). */
export const FAILURE_CATEGORIES = ['PRODUCT_BUG', 'INFRASTRUCTURE', 'TEST_DATA', 'AUTOMATION_DEFECT', 'FLAKY', 'UNKNOWN'] as const
export const QUEUE_PAGE_SIZE = 25

export const COPY = {
  projectRequired: 'Tier comparisons and shadow pairs belong to one project: choose the project whose agents to compare.',
  runTitle: 'Run tier comparison (G2)',
  runSubtitle: 'Scores the version-controlled samples and, when included, the live pairs people labelled.',
  recordHint: 'A recorded result is the evidence a tier change is checked against.',
  smoke: 'Smoke check: both tiers must be valid and their 95 % intervals must overlap.',
  historyTitle: 'Recorded comparisons',
  historyEmpty: 'No recorded comparisons for this project yet.',
  noPairs:
    'No shadow pairs: no live model tier. Pairs are recorded only while an agent runs in shadow mode and a local SLM and LLM both answer.',
  allLabelled: 'Every live pair is labelled.',
  sourceGone: 'Source no longer available.',
  loadError: {
    pairs: 'Could not load the shadow pairs.',
    history: 'Could not load the tier comparisons.',
  },
  runFailed: 'The tier comparison could not run.',
  labelFailed: 'The label could not be saved.',
} as const

export function agentFor(agentId: string | null | undefined): TierAgent {
  return TIER_AGENTS.find((agent) => agent.id === agentId) ?? TIER_AGENTS[0]
}

/** `Not enough evidence yet: G2 needs {floor} paired samples for this agent.` — null once the floor is met. */
export function floorSentence(agentId: string, n: number): string | null {
  const { floor } = agentFor(agentId)
  return n < floor ? `Not enough evidence yet: G2 needs ${floor} paired samples for this agent.` : null
}

/** `{n} paired samples · {live} live labels` (a missing count reads 0: the server omits it without live labels). */
export function samplesLine(sampleCount: number | null | undefined, live: number | null | undefined): string {
  const n = typeof sampleCount === 'number' ? sampleCount : 0
  const l = typeof live === 'number' ? live : 0
  return `${n} paired samples · ${l} live labels`
}

/** The server's verdict in the page's words; an unknown string renders as itself. */
export function verdictText(verdict: string | null | undefined): string {
  if (!verdict) return '—'
  return EVAL_VERDICT_LABELS[verdict] ?? verdict
}

export type Tone = 'success' | 'danger' | 'warning' | 'info' | 'muted'

export function verdictTone(verdict: string | null | undefined): Tone {
  if (verdict === 'pass') return 'success'
  if (verdict === 'fail') return 'danger'
  if (verdict === 'insufficient_samples') return 'warning'
  return 'muted'
}

export const TONE_CLASS: Record<Tone, string> = {
  success: 'border-[var(--ui-success-bd)] bg-[var(--ui-success-bg)] text-[var(--ui-success-ink)]',
  danger: 'border-[var(--ui-danger-bd)] bg-[var(--ui-danger-bg)] text-[var(--ui-danger-ink)]',
  warning: 'border-[var(--ui-warning-bd)] bg-[var(--ui-warning-bg)] text-[var(--ui-warning-ink)]',
  info: 'border-[var(--ui-info-bd)] bg-[var(--ui-info-bg)] text-[var(--ui-info-ink)]',
  muted: 'border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)]',
}

export const CHIP_CLASS = 'inline-flex shrink-0 items-center whitespace-nowrap rounded border px-1.5 py-px text-2xs font-medium'

/** A 0..1 rate as `66.7%`; a signed one (the paired delta) as `−33.3%`; nothing → `—`. */
export function rateText(value: number | null | undefined, signed = false): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  const text = `${(Math.abs(value) * 100).toFixed(1)}%`
  if (value < 0) return `−${text}`
  return signed && value > 0 ? `+${text}` : text
}

/** The G2 metric names in the page's words (an unknown name renders as itself). */
export const METRIC_LABELS: Record<string, string> = {
  incumbent_accuracy: 'Incumbent accuracy',
  candidate_accuracy: 'Candidate accuracy',
  paired_accuracy_delta: 'Paired accuracy Δ',
  candidate_validity: 'Candidate validity',
  cost_delta_usd: 'Cost Δ per sample',
  latency_delta_ms: 'Latency Δ',
}

function signed(value: number | null | undefined, format: (abs: number) => string): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  const text = format(Math.abs(value))
  return value < 0 ? `−${text}` : value > 0 ? `+${text}` : text
}

/** A metric's value / bound as text: rates in %, the paired Δ signed, cost in $, latency in ms. */
export function metricCell(metric: { name: string }, value: number | null | undefined): string {
  if (metric.name === 'cost_delta_usd') return signed(value, (abs) => `$${abs.toFixed(4)}`)
  if (metric.name === 'latency_delta_ms') return signed(value, (abs) => `${abs.toFixed(0)} ms`)
  return rateText(value, metric.name === 'paired_accuracy_delta')
}

/** `Expires in {n} days` from the server's `expires_at`; `Expires today` on the last day; null without one. */
export function expiresIn(expiresAt: string | null | undefined, now: Date): string | null {
  if (!expiresAt) return null
  const at = new Date(expiresAt).getTime()
  if (!Number.isFinite(at)) return null
  const days = Math.ceil((at - now.getTime()) / 86_400_000)
  if (days <= 0) return 'Expires today'
  return days === 1 ? 'Expires in 1 day' : `Expires in ${days} days`
}

/** The source link's text: `Run #{build}` (or the run's id when it has no build); null = no source. */
export function sourceText(source: ShadowPairSource | null | undefined): string | null {
  if (!source || !source.href) return null
  if (source.build_number) return `Run #${source.build_number}`
  return source.run_id ? `Run ${source.run_id.slice(0, 8)}` : 'Open the run'
}

/** The label body: the verdict, the category for a root-cause pair only, trimmed notes when given. */
export function verdictBody(verdict: string, category: string | null | undefined, notes: string | null | undefined): ShadowPairLabel {
  const body: ShadowPairLabel = { verdict }
  if (category) body.failure_category = category
  const trimmed = (notes ?? '').trim()
  if (trimmed) body.notes = trimmed
  return body
}

/** A stored output as text for a `<pre>` (never HTML). */
export function outputText(output: unknown): string {
  if (output == null) return '—'
  try {
    return JSON.stringify(output, null, 2) ?? String(output)
  } catch {
    return String(output)
  }
}

/** A refused write's sentence: the `{code, message}` detail's message, a string detail, or `fallback`. */
export function serverMessage(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail
  return extractErrorMessage(detail, fallback)
}
