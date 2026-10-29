/**
 * Cross-run insights (W23a FAIL-11, contract §4.1 / §4.3): the pure model of
 * the Overview card and Inbox › Insights. Every sentence the list renders is
 * here, so a test imports the one string it asserts.
 *
 * Absence is not health: a detector that did not measure says so with its
 * server sentence ("not measured: …"), never a ✓; a detector last evaluated
 * more than {@link STALE_CHECK_HOURS} h ago adds "checked 5 h ago", so a
 * stopped beat is visible instead of hiding behind "No insights".
 *
 * The titles, subject labels and people come from the server and are React
 * text only; nothing here builds markup.
 */
import { INSIGHT_KINDS, NOT_MEASURED, type InsightRange } from '@/types/insights'

/** The detector names, in `INSIGHT_KINDS` order. */
export const KIND_LABEL: Readonly<Record<string, string>> = {
  perf_drift: 'Perf drift',
  flake_rising: 'Flake rising',
  coverage_drop: 'Coverage drop',
  cost_budget: 'AI budget',
}

export const INSIGHTS_TITLE = 'Insights'
export const CARD_SUBTITLE = 'Patterns across runs · checked every hour'
export const OPEN_IN_INBOX = 'Open in Inbox'
export const SHOW_ALL_IN_INBOX = 'Show all in the Inbox'
/** Inbox › Insights (the card's "Open in Inbox"). */
export const INBOX_INSIGHTS_PATH = '/my-failures?tab=insights'
/** `frontend.load-error-wording`: the card's and the tab's failed read. */
export const LOAD_ERROR = 'Could not load the insights.'
/** The whole body while the project's `cross_run_insights` flag is off (the server's own sentence). */
export const FLAG_OFF_SENTENCE = 'Insights are off for this project (feature flag cross_run_insights).'
export const LATEST_RUN = 'Latest run'
export const DISMISS_LABEL = 'Dismiss'
export const DISMISS_FAILED = 'Could not dismiss the insight.'
/** How many rows the Overview card shows before "Showing 5 of 12". */
export const CARD_ROWS = 5
/** A detector evaluated longer ago than this says when ("· checked 5 h ago"). */
export const STALE_CHECK_HOURS = 3

/** The range toggle's two buttons. */
export const RANGE_LABEL: Readonly<Record<InsightRange, string>> = { '24h': '24 h', '7d': '7 d' }
export const RANGE_OPTIONS: readonly InsightRange[] = ['24h', '7d']
const RANGE_WORDS: Readonly<Record<InsightRange, string>> = { '24h': 'the last 24 hours', '7d': 'the last 7 days' }

/** The inline question under a row (InlineConfirm). */
export function dismissQuestion(projectName: string): string {
  return `Dismiss for everyone in ${projectName}?`
}

/** The empty list's sentence. */
export function emptySentence(range: InsightRange): string {
  return `No insights in ${RANGE_WORDS[range] ?? RANGE_WORDS['7d']}.`
}

/** `Showing 5 of 12` — `total` is the server's count, never the page length. */
export function showingLine(shown: number, total: number): string {
  return `Showing ${shown} of ${total}`
}

/** Inbox › Insights in All Projects: the project prompt and the header's subtitle. */
export const INSIGHTS_PROJECT_PROMPT = 'Insights belong to one project: pick it to see them.'

/** The Inbox header's subtitle on the Insights tab. */
export function insightsSubtitle(projectName: string | null): string {
  return projectName
    ? `Patterns across runs in ${projectName}: slower tests and suites, rising flakiness, falling coverage and AI spend, checked every hour.`
    : INSIGHTS_PROJECT_PROMPT
}

/** `Show dismissed (n)` (Inbox › Insights). */
export function showDismissedLabel(count: number): string {
  return `Show dismissed (${count})`
}
export const HIDE_DISMISSED = 'Hide dismissed'

export function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind
}

/** `high` → `High`; the pill's word. */
export function severityWord(severity: string | null | undefined): string {
  const word = (severity ?? '').trim().toLowerCase()
  if (!word) return 'Unknown'
  return word.charAt(0).toUpperCase() + word.slice(1)
}

const SEVERITY_RANK: Readonly<Record<string, number>> = { high: 0, medium: 1, low: 2 }

/** An age read against `now`: `just now`, `12 min ago`, `3 h ago`, `2 days ago`. */
export function agoText(iso: string | null | undefined, now: number): string {
  const at = iso ? Date.parse(iso) : Number.NaN
  if (!Number.isFinite(at)) return ''
  const minutes = Math.floor((now - at) / 60_000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  const days = Math.floor(hours / 24)
  return days === 1 ? '1 day ago' : `${days} days ago`
}

/** The fields of an insight the model reads (the server's `InsightItem`; G-A's `Insight` fits). */
export interface InsightLike {
  id: string
  kind: string
  severity: string
  status?: string | null
  resolved_reason?: string | null
  first_seen_at?: string
  last_seen_at?: string
  resolved_at?: string | null
  dismissed_at?: string | null
  dismissed_by_name?: string | null
}

/** The fields of a detector state the model reads (the server's `InsightDetectorStatus`). */
export interface DetectorLike {
  kind: string
  measured?: boolean | null
  reason?: string | null
  evaluated_at?: string | null
}

export function isResolved(insight: InsightLike): boolean {
  return insight.status === 'resolved' || Boolean(insight.resolved_at)
}

/**
 * The row's second line: `Perf drift · first seen 3 h ago · confirmed 12 min ago` (open),
 * `Cleared 2 h ago` / `Not seen for 3 days` (resolved), `Dismissed by Alice Lead · 1 h ago`.
 */
export function metaLine(insight: InsightLike, now: number): string {
  if (insight.dismissed_at) {
    const who = insight.dismissed_by_name?.trim()
    return `${who ? `Dismissed by ${who}` : 'Dismissed'} · ${agoText(insight.dismissed_at, now)}`
  }
  if (isResolved(insight)) {
    if (insight.resolved_reason === 'stale') {
      const lastSeen = Date.parse(insight.last_seen_at ?? '')
      const days = Number.isFinite(lastSeen) ? Math.floor((now - lastSeen) / 86_400_000) : 3
      return `Not seen for ${Math.max(3, days)} days`
    }
    return `Cleared ${agoText(insight.resolved_at ?? insight.last_seen_at, now)}`
  }
  return `${kindLabel(insight.kind)} · first seen ${agoText(insight.first_seen_at, now)} · confirmed ${agoText(insight.last_seen_at, now)}`
}

/** The server's order (open first, high → low, newest confirmation, id), kept stable on the client. */
export function orderInsights<T extends InsightLike>(items: readonly T[]): T[] {
  return [...items].sort((a, b) =>
    Number(isResolved(a)) - Number(isResolved(b))
    || (SEVERITY_RANK[a.severity] ?? 3) - (SEVERITY_RANK[b.severity] ?? 3)
    || (Date.parse(b.last_seen_at ?? '') || 0) - (Date.parse(a.last_seen_at ?? '') || 0)
    || a.id.localeCompare(b.id))
}

/**
 * The four detectors in `INSIGHT_KINDS` order; a kind the server omitted reads
 * "Not evaluated yet…" (never measured, never a ✓).
 */
export function normaliseDetectors(detectors: readonly DetectorLike[] | null | undefined): DetectorLike[] {
  const byKind = new Map((detectors ?? []).map((d) => [d.kind, d]))
  return INSIGHT_KINDS.map((kind) => byKind.get(kind) ?? {
    kind, measured: false, reason: NOT_MEASURED.not_evaluated, evaluated_at: null,
  })
}

function staleSuffix(detector: DetectorLike, now: number): string {
  const at = detector.evaluated_at ? Date.parse(detector.evaluated_at) : Number.NaN
  if (!Number.isFinite(at) || now - at <= STALE_CHECK_HOURS * 3_600_000) return ''
  return ` · checked ${agoText(detector.evaluated_at, now)}`
}

/**
 * One muted line per detector worth a word: not measured (`Coverage drop — not
 * measured: <reason>`), measured with a server note (perf's half-measured
 * case), or last checked more than 3 h ago (`· checked 5 h ago`).
 */
export function detectorLines(detectors: readonly DetectorLike[] | null | undefined, now: number): string[] {
  const lines: string[] = []
  for (const detector of normaliseDetectors(detectors)) {
    const label = kindLabel(detector.kind)
    const suffix = staleSuffix(detector, now)
    if (!detector.measured) {
      lines.push(`${label} — not measured: ${detector.reason || NOT_MEASURED.not_evaluated}${suffix}`)
    } else if (detector.reason) {
      lines.push(`${label} — ${detector.reason}${suffix}`)
    } else if (suffix) {
      lines.push(`${label}${suffix}`)
    }
  }
  return lines
}

/** The empty state's checks: `Perf drift ✓ · Flake rising ✓ · …`; a check that did not run says so. */
export function emptyChecksLine(detectors: readonly DetectorLike[] | null | undefined): string {
  return normaliseDetectors(detectors)
    .map((d) => `${kindLabel(d.kind)} ${d.measured ? '✓' : 'not measured'}`)
    .join(' · ')
}
