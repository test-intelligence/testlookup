/**
 * The four-signal flakiness score on Flaky tests › Detected (FLK-03).
 *
 * The coach entry carries `flaky_score` (the server joins the project's
 * nightly `flaky_score` row onto each listed test). That answer is a
 * SECONDARY read on the page: `parseFlakyScore` turns anything malformed into
 * `null`, which the column prints as "—" ("Score not available"), so a bad
 * answer can never crash the table or pass for a measurement.
 *
 * "insufficient" and "not scored yet" are not zero: a test below the evidence
 * floor has no score at all (`content/guide/flaky.md`, "A new test is not a
 * stable test").
 *
 * Pure: no React, no request.
 */
import type { FlakyCoachEntry, FlakyScoreBrief, FlakyScoreComponent } from '@/services/testHealthService'

/** The server's vocabulary (`flaky_score_brief.STATUSES`). */
export const SCORE_STATUSES = ['scored', 'insufficient', 'pending'] as const

/** The confidence bands that earn a chip (`none` is "below the floor", never chipped). */
const BANDS = ['low', 'medium', 'high'] as const

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function finite(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v)
}

/** A field that may be absent (→ the fallback) but, when present, must be a finite number. */
function numberOr(v: unknown, fallback: number): number | undefined {
  if (v === undefined || v === null) return fallback
  return finite(v) ? v : undefined
}

function parseComponent(raw: unknown): FlakyScoreComponent | null {
  if (!isRecord(raw)) return null
  const { name, value, weight } = raw
  if (typeof name !== 'string' || name === '') return null
  if (!finite(value) || !finite(weight)) return null
  return { name, value, weight }
}

/**
 * The defensive guard over `entry.flaky_score`. `null` for anything missing or
 * malformed: no object, an unknown status, a wrong-typed field, a "scored"
 * answer without a 0-1 score, or a component that is not `{name, value,
 * weight}` (a partial breakdown would print a weights sum that is not the
 * score's).
 */
export function parseFlakyScore(raw: unknown): FlakyScoreBrief | null {
  if (!isRecord(raw)) return null
  const status = raw.status
  if (typeof status !== 'string' || !(SCORE_STATUSES as readonly string[]).includes(status)) return null

  const confidence = raw.confidence === undefined || raw.confidence === null ? 'none' : raw.confidence
  if (typeof confidence !== 'string') return null
  const observations = numberOr(raw.observations, 0)
  const minObservations = numberOr(raw.min_observations, 5)
  const windowDays = numberOr(raw.window_days, 30)
  if (observations === undefined || minObservations === undefined || windowDays === undefined) return null

  if (status !== 'scored') {
    return {
      status,
      score: null,
      confidence,
      observations,
      min_observations: minObservations,
      window_days: windowDays,
      computed_at: null,
      components: [],
    }
  }

  const score = raw.score
  if (!finite(score) || score < 0 || score > 1) return null
  const computedAt = raw.computed_at
  if (computedAt !== undefined && computedAt !== null && typeof computedAt !== 'string') return null
  const rawComponents = raw.components ?? []
  if (!Array.isArray(rawComponents)) return null
  const components: FlakyScoreComponent[] = []
  for (const item of rawComponents) {
    const component = parseComponent(item)
    if (!component) return null
    components.push(component)
  }
  return {
    status,
    score,
    confidence,
    observations,
    min_observations: minObservations,
    window_days: windowDays,
    computed_at: typeof computedAt === 'string' ? computedAt : null,
    components,
  }
}

/** The Detected tab's sort (local state, D-14): the server's order, or most flaky first. */
export type FlakySort = 'impact' | 'score'

export const SORT_OPTIONS: ReadonlyArray<{ id: FlakySort; label: string }> = [
  { id: 'impact', label: 'Impact' },
  { id: 'score', label: 'Flakiness score' },
]

function scoredValue(entry: FlakyCoachEntry): number | null {
  const brief = parseFlakyScore(entry.flaky_score)
  return brief && brief.status === 'scored' ? brief.score : null
}

/**
 * 'impact' = the server's order, untouched (a copy). 'score' = the scored rows
 * by score, highest first (ties keep the server's order), then every unscored
 * row in the server's order.
 */
export function sortEntries(entries: readonly FlakyCoachEntry[], by: FlakySort): FlakyCoachEntry[] {
  if (by !== 'score') return [...entries]
  const scored: Array<{ entry: FlakyCoachEntry; score: number; index: number }> = []
  const unscored: FlakyCoachEntry[] = []
  entries.forEach((entry, index) => {
    const score = scoredValue(entry)
    if (score === null) unscored.push(entry)
    else scored.push({ entry, score, index })
  })
  scored.sort((a, b) => (b.score - a.score) || (a.index - b.index))
  return [...scored.map((s) => s.entry), ...unscored]
}

function plural(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? '' : 's'}`
}

/** A scored brief's band chip: its confidence when it is low / medium / high, else none. */
export function scoreBand(brief: FlakyScoreBrief | null): string | null {
  if (!brief || brief.status !== 'scored') return null
  return (BANDS as readonly string[]).includes(brief.confidence) ? brief.confidence : null
}

/** The Flakiness score cell: ONE line of text, an optional band chip, and the cell's `title`. */
export function scoreCell(brief: FlakyScoreBrief | null): { text: string; band: string | null; title: string } {
  if (!brief) return { text: '—', band: null, title: 'Score not available' }
  if (brief.status === 'scored' && brief.score !== null) {
    const text = brief.score.toFixed(2)
    return {
      text,
      band: scoreBand(brief),
      title: `Flakiness score ${text} · ${brief.confidence} confidence · ${plural(brief.observations, 'observation')}`,
    }
  }
  if (brief.status === 'insufficient') {
    return {
      text: 'insufficient',
      band: null,
      title: `${brief.observations} of ${brief.min_observations} observations`,
    }
  }
  if (brief.status === 'pending') {
    return { text: 'not scored yet', band: null, title: 'Scores are computed nightly' }
  }
  return { text: '—', band: null, title: 'Score not available' }
}

/** The column header's `title`: which detector is which (R-10). */
export const SCORE_HEADER_TITLE =
  'Result volatility, retries, environment instability and duration variance, from the nightly score. ' +
  'Separate from Fail %, which is the failure-rate detector.'

/** Human labels of the four signals (`flaky_score_service.DEFAULT_WEIGHTS`). */
export const COMPONENT_LABELS: Record<string, string> = {
  result_volatility: 'Result volatility (pass/fail flips)',
  retry_rate: 'Retries',
  environment_instability: 'Environment instability',
  duration_variance: 'Duration variance',
}

/** A signal's label; an unknown name renders as itself (React text, never markup). */
export function componentLabel(name: string): string {
  return Object.prototype.hasOwnProperty.call(COMPONENT_LABELS, name) ? COMPONENT_LABELS[name] : name
}

/** `Result volatility (pass/fail flips) 0.70 × 0.45` */
export function componentLine(component: FlakyScoreComponent): string {
  return `${componentLabel(component.name)} ${component.value.toFixed(2)} × ${component.weight.toFixed(2)}`
}

/** `Weights sum to 1.00` (from the row's stored weights, never assumed). */
export function weightsLine(components: readonly FlakyScoreComponent[]): string {
  const sum = components.reduce((total, c) => total + c.weight, 0)
  return `Weights sum to ${sum.toFixed(2)}`
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** `16 Oct 02:10 UTC`, or null for a missing or unparseable timestamp. */
export function formatScoredAt(iso: string | null): string | null {
  if (!iso) return null
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return null
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())} UTC`
}

/** The details section's evidence line for a scored test: `medium · 23 observations`. */
export function evidenceLine(brief: FlakyScoreBrief): string {
  return `${brief.confidence} · ${plural(brief.observations, 'observation')}`
}

/** `over the last 30 days, scored 16 Oct 02:10 UTC` (the time omitted when unknown). */
export function windowLine(brief: FlakyScoreBrief): string {
  const at = formatScoredAt(brief.computed_at)
  const window = `over the last ${plural(brief.window_days, 'day')}`
  return at ? `${window}, scored ${at}` : window
}

/** `insufficient: 3 of 5 observations` */
export function insufficientLine(brief: FlakyScoreBrief): string {
  return `insufficient: ${brief.observations} of ${brief.min_observations} observations`
}

/** The guide's rule (`content/guide/flaky.md`), stated where the number is missing. */
export const INSUFFICIENT_NOTE =
  'A new test is not a stable test: below the evidence floor there is no score, rather than a zero that would read as stability.'

/** `Not scored yet: the score is computed nightly from the last 30 days.` */
export function pendingLine(brief: FlakyScoreBrief): string {
  return `Not scored yet: the score is computed nightly from the last ${plural(brief.window_days, 'day')}.`
}
