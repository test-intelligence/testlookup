/**
 * The ONE status vocabulary (W29a UIX-03): the words a result status and a
 * release-gate decision print, and the tokens they paint with.
 *
 * - Words are SENTENCE CASE from these maps ("Failed", "Conditional",
 *   "No-Go"), never the raw enum and never `uppercase` CSS on a chip. The five
 *   result labels and "Flaky" are the chart kit's own (`STATUS_ENCODING`,
 *   `FLAKY_MARKER`), so a chart legend and a badge can never disagree.
 * - Text on a status tint reads the `-ink` token (75 % hue + 25 % body text,
 *   index.css): >= 4.68:1 on its own tint in every theme, where the bare hue
 *   missed 4.5:1 in 21 of 36 theme pairs.
 * - Conditional is the WARNING hue (broken), never skipped-gold.
 * - Workflow states (a skipped pipeline stage, a release phase) are not
 *   results: they keep their neutral looks and do not read this map.
 *
 * A page never re-declares these maps; it imports them. This file and
 * `severity.ts` are the only places outside the chart kit allowed to name the
 * failed / broken hues directly (quality gate `frontend.status-hue-semantics`).
 */
import { FLAKY_MARKER, STATUS_ENCODING } from '@/components/charts/tokens'

export type ResultStatus = 'passed' | 'failed' | 'broken' | 'skipped' | 'unknown'

/** Display words, keyed by `statusKey` (lower-case, `_`-separated). */
export const STATUS_LABEL: Readonly<Record<string, string>> = {
  passed: STATUS_ENCODING.passed.label,
  failed: STATUS_ENCODING.failed.label,
  broken: STATUS_ENCODING.broken.label,
  skipped: STATUS_ENCODING.skipped.label,
  unknown: STATUS_ENCODING.unknown.label,
  flaky: FLAKY_MARKER.label,
  passed_run: 'Passed',
  in_progress: 'In progress',
  stopped: 'Stopped',
  not_run: 'Not run',
  blocked: 'Blocked',
  error: 'Error',
  pending: 'Pending',
}

/** `' IN_PROGRESS '` / `'in-progress'` / `'In Progress'` → `'in_progress'`. */
export function statusKey(raw: string | null | undefined): string {
  return (raw ?? '').trim().toLowerCase().replace(/[\s-]+/g, '_')
}

/** `"raw_value"` → `"Raw value"`. */
function sentenceCase(key: string): string {
  const words = key.replace(/_+/g, ' ').trim()
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : ''
}

/** Own keys only: a status named `constructor` is not `Object.prototype.constructor`. */
const own = (map: object, key: string): boolean => Object.prototype.hasOwnProperty.call(map, key)

/** The word a status prints: the map's, else the raw value in sentence case; `'—'` when empty. */
export function statusLabel(raw: string | null | undefined): string {
  const key = statusKey(raw)
  if (!key) return '—'
  return own(STATUS_LABEL, key) ? STATUS_LABEL[key] : sentenceCase(key)
}

/** The four tokens a status chip paints with: a mark, its tint, its border, its text. */
export interface StatusToken {
  hue: string
  tint: string
  border: string
  ink: string
}

export const RESULT_STATUS_TOKEN: Readonly<Record<ResultStatus | 'flaky', StatusToken>> = {
  passed: { hue: 'var(--status-passed)', tint: 'var(--status-passed-bg)', border: 'var(--status-passed-bd)', ink: 'var(--status-passed-ink)' },
  failed: { hue: 'var(--status-failed)', tint: 'var(--status-failed-bg)', border: 'var(--status-failed-bd)', ink: 'var(--status-failed-ink)' },
  broken: { hue: 'var(--status-broken)', tint: 'var(--status-broken-bg)', border: 'var(--status-broken-bd)', ink: 'var(--status-broken-ink)' },
  skipped: { hue: 'var(--status-skipped)', tint: 'var(--status-skipped-bg)', border: 'var(--status-skipped-bd)', ink: 'var(--status-skipped-ink)' },
  flaky: { hue: 'var(--status-flaky)', tint: 'var(--status-flaky-bg)', border: 'var(--status-flaky-bd)', ink: 'var(--status-flaky-ink)' },
  unknown: {
    hue: 'var(--status-unknown)',
    tint: 'var(--color-bg-secondary)',
    border: 'var(--color-border-light)',
    ink: 'var(--color-text-muted)',
  },
}

/** The token set of a RESULT status (`passed_run` reads as passed); `null` for anything that is not a result. */
export function resultStatusToken(raw: string | null | undefined): StatusToken | null {
  const key = statusKey(raw)
  if (key === 'passed_run') return RESULT_STATUS_TOKEN.passed
  return own(RESULT_STATUS_TOKEN, key) ? RESULT_STATUS_TOKEN[key as ResultStatus | 'flaky'] : null
}

// ── Release-gate decisions ───────────────────────────────────────────────────

export type GateDecision = 'go' | 'conditional' | 'no_go'

/** Go / Conditional / No-Go: the dominant existing spelling (contract W29a §0.9). */
export const DECISION_LABEL: Readonly<Record<GateDecision, string>> = {
  go: 'Go',
  conditional: 'Conditional',
  no_go: 'No-Go',
}

/** Every spelling the API and the pages use: GO, CONDITIONAL_GO, CONDITIONAL, NO_GO, NO-GO, NOGO, … */
export function decisionKey(raw: string | null | undefined): GateDecision | null {
  switch (statusKey(raw)) {
    case 'go':
      return 'go'
    case 'conditional':
    case 'conditional_go':
      return 'conditional'
    case 'no_go':
    case 'nogo':
      return 'no_go'
    default:
      return null
  }
}

/** The decision's word, else the raw value's sentence case (`statusLabel`). */
export function decisionLabel(raw: string | null | undefined): string {
  const key = decisionKey(raw)
  return key ? DECISION_LABEL[key] : statusLabel(raw)
}

export const DECISION_TOKEN: Readonly<Record<GateDecision, StatusToken>> = {
  go: { hue: 'var(--gate-go)', tint: 'var(--status-passed-bg)', border: 'var(--status-passed-bd)', ink: 'var(--status-passed-ink)' },
  // The WARNING hue (broken), never skipped-gold.
  conditional: { hue: 'var(--gate-conditional)', tint: 'var(--status-broken-bg)', border: 'var(--status-broken-bd)', ink: 'var(--status-broken-ink)' },
  no_go: { hue: 'var(--gate-no-go)', tint: 'var(--status-failed-bg)', border: 'var(--status-failed-bd)', ink: 'var(--status-failed-ink)' },
}
