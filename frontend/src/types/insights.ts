/**
 * W23a FAIL-11: cross-run insights — the wire vocabulary, the shapes and their defensive parser.
 *
 * The constants mirror `backend/app/services/insight_vocab.py` value for value (pinned by
 * `backend/tests/test_insight_cadence_vocab_frontend_parity.py`, which reads this file as TEXT — keep every
 * constant a plain literal on its own `export const` line, strings in one piece, one `key: 'value',` per
 * record line). Enum-like fields are plain strings on the wire.
 *
 * Rules only (no LLM); insights notify no one. A detector that could not measure says so with one of the
 * `NOT_MEASURED` sentences — absence is never read as "no insights".
 *
 * Every title, name and sentence below is rendered as React text, never as HTML; a link is rendered only
 * when `isInsightPath` accepts its (server-built) path.
 */

export const INSIGHT_FLAG = 'cross_run_insights'

export const INSIGHT_KINDS = ['perf_drift', 'flake_rising', 'coverage_drop', 'cost_budget'] as const
export type InsightKind = (typeof INSIGHT_KINDS)[number]

/** Ranked low → high; the card's tone: info / warning / danger. */
export const INSIGHT_SEVERITIES = ['low', 'medium', 'high'] as const
export type InsightSeverity = (typeof INSIGHT_SEVERITIES)[number]

export const INSIGHT_RESOLVE_REASONS = ['cleared', 'stale'] as const
export type InsightResolveReason = (typeof INSIGHT_RESOLVE_REASONS)[number]

/** `GET …/insights?range=` */
export const INSIGHT_RANGES = ['24h', '7d'] as const
export type InsightRange = (typeof INSIGHT_RANGES)[number]
export const INSIGHT_RANGE_DEFAULT = '7d'

export const INSIGHT_LIST_MAX = 100
export const INSIGHT_LIST_DEFAULT = 50

/** The ONLY link targets (server-built; the SPA re-checks with `isInsightPath`). */
export const INSIGHT_LINK_PATHS = ['/trends?tab=durations', '/flaky', '/coverage?tab=history', '/settings/billing'] as const

/** Detector state sentences (fixed; the card shows them verbatim). */
export const NOT_MEASURED = {
  not_evaluated: 'Not evaluated yet — the first check runs within the hour.',
  flag_off: 'Insights are off for this project (feature flag cross_run_insights).',
  perf_no_findings: 'No run in the last 7 days was checked for duration regressions.',
  perf_tests_flag_off: 'Per-test duration spikes are off for this project (feature flag perf_regression_detection); suites are still checked.',
  flake_few_runs: 'No suite had 5 runs in both of the last two weeks.',
  coverage_no_snapshot: 'No coverage snapshot yet: the first is taken at 00:05 UTC.',
  coverage_stale_snapshot: 'The newest coverage snapshot is more than 2 days old.',
  coverage_no_previous: 'There is no coverage snapshot from a week before the newest one.',
  coverage_too_small: 'Fewer than 20 tests were exercised a week ago.',
  cost_flag_off: 'The LLM cost budget is off (feature flag llm_cost_budget).',
  cost_no_budget: 'No AI budget is set for this project.',
  detector_failed: 'This check failed on its last run; it is retried within the hour.',
} as const
export type NotMeasuredCode = keyof typeof NOT_MEASURED

/** detail = {code, message} on the insight routes' own 4xx (`extractErrorMessage` reads the message). */
export const INSIGHT_ERRORS = {
  insight_not_found: 'Insight not found.',
  unknown_range: 'Unknown range. Valid: 24h, 7d.',
} as const
export type InsightErrorCode = keyof typeof INSIGHT_ERRORS

// ── Shapes (every field beyond a row's identity is OPTIONAL, so a fixture written today type-checks when
// the shape grows; the parser fills the defaults) ──────────────────────────────────────────────────────

export interface InsightLink {
  path?: string
  label?: string
}

/** One EPISODE of one detector firing on one subject (`InsightItem`). */
export interface Insight {
  id: string
  kind: InsightKind
  severity: InsightSeverity | string
  /** 'open' | 'resolved' */
  status?: 'open' | 'resolved' | string
  resolved_reason?: InsightResolveReason | string | null
  /** A server-rendered sentence; React text. */
  title?: string
  subject_label?: string
  window_key?: string
  evidence?: Record<string, unknown>
  first_seen_at?: string
  last_seen_at?: string
  resolved_at?: string | null
  detection_count?: number
  dismissed_at?: string | null
  dismissed_by_name?: string | null
  link?: InsightLink
  /** perf_drift only: "/runs/<uuid>?tab=changes". */
  run_path?: string | null
}

/** The last evaluation of one detector (`InsightDetectorStatus`). */
export interface InsightDetector {
  kind: InsightKind
  measured: boolean
  /** A `NOT_MEASURED` sentence; null when measured. */
  reason?: string | null
  /** null = never evaluated. */
  evaluated_at?: string | null
  subjects_measured?: number
  firing?: number
}

/** `GET /api/v1/projects/{project_id}/insights` (`InsightListResponse`). */
export interface InsightList {
  enabled?: boolean
  range?: InsightRange | string
  items: Insight[]
  /** Every row matching the filter (`items` is at most the limit). */
  total?: number
  /** Dismissed rows in the range ("Show dismissed (n)"). */
  dismissed_total?: number
  /** Always four after `parseInsightList`, in `INSIGHT_KINDS` order. */
  detectors: InsightDetector[]
  generated_at?: string
}

// ── guards ──────────────────────────────────────────────────────────────────

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function isNum(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v)
}

function strOrNull(v: unknown): string | null {
  return typeof v === 'string' ? v : null
}

function isKind(v: unknown): v is InsightKind {
  return typeof v === 'string' && (INSIGHT_KINDS as readonly string[]).includes(v)
}

const RUN_PATH_RE = /^\/runs\/[0-9a-f-]{36}\?tab=changes$/

/**
 * True only for a path the SPA may link to: one of `INSIGHT_LINK_PATHS`, or a run's Changes tab
 * (`/runs/<uuid>?tab=changes`). Anything else — `javascript:`, an absolute URL, an off-list route — is
 * rendered as plain text, never as a link.
 */
export function isInsightPath(path: unknown): path is string {
  if (typeof path !== 'string') return false
  return (INSIGHT_LINK_PATHS as readonly string[]).includes(path) || RUN_PATH_RE.test(path)
}

/** `undefined` = malformed (the whole answer is refused); `null` = an unknown kind (the row is dropped). */
function parseInsight(raw: unknown): Insight | null | undefined {
  if (!isRecord(raw)) return undefined
  const { id, kind, severity } = raw
  if (typeof id !== 'string' || typeof kind !== 'string' || typeof severity !== 'string') return undefined
  if (!isKind(kind)) return null
  const link = isRecord(raw.link)
    ? {
        path: typeof raw.link.path === 'string' ? raw.link.path : '',
        label: typeof raw.link.label === 'string' ? raw.link.label : '',
      }
    : { path: '', label: '' }
  return {
    id,
    kind,
    severity,
    status: raw.status === 'resolved' ? 'resolved' : 'open',
    resolved_reason: strOrNull(raw.resolved_reason),
    title: typeof raw.title === 'string' ? raw.title : '',
    subject_label: typeof raw.subject_label === 'string' ? raw.subject_label : '',
    window_key: typeof raw.window_key === 'string' ? raw.window_key : '',
    evidence: isRecord(raw.evidence) ? raw.evidence : {},
    first_seen_at: typeof raw.first_seen_at === 'string' ? raw.first_seen_at : '',
    last_seen_at: typeof raw.last_seen_at === 'string' ? raw.last_seen_at : '',
    resolved_at: strOrNull(raw.resolved_at),
    detection_count: isNum(raw.detection_count) ? raw.detection_count : 1,
    dismissed_at: strOrNull(raw.dismissed_at),
    dismissed_by_name: strOrNull(raw.dismissed_by_name),
    link,
    run_path: strOrNull(raw.run_path),
  }
}

function notEvaluated(kind: InsightKind): InsightDetector {
  return { kind, measured: false, reason: NOT_MEASURED.not_evaluated, evaluated_at: null, subjects_measured: 0, firing: 0 }
}

/**
 * The defensive guard over `GET /api/v1/projects/{project_id}/insights` (the `parseTrendSummary`
 * precedent). Returns `null` for an answer that is not a list (the card then says "Could not load the
 * insights." instead of crashing the page). Rows of an unknown `kind` are DROPPED, never rendered; missing
 * optional fields are defaulted; `detectors` always comes back as the four kinds in `INSIGHT_KINDS` order
 * (a kind the server omitted reads "Not evaluated yet…", never "all clear"). Unknown extra keys are ignored.
 */
export function parseInsightList(raw: unknown): InsightList | null {
  if (!isRecord(raw)) return null
  const { items, detectors } = raw
  if (!Array.isArray(items)) return null
  if (detectors !== undefined && detectors !== null && !Array.isArray(detectors)) return null
  if (raw.enabled !== undefined && typeof raw.enabled !== 'boolean') return null
  if (raw.total !== undefined && !isNum(raw.total)) return null
  const rows: Insight[] = []
  for (const item of items) {
    const parsed = parseInsight(item)
    if (parsed === undefined) return null
    if (parsed !== null) rows.push(parsed)
  }
  const byKind = new Map<InsightKind, InsightDetector>()
  for (const d of Array.isArray(detectors) ? detectors : []) {
    if (!isRecord(d) || !isKind(d.kind) || byKind.has(d.kind)) continue
    byKind.set(d.kind, {
      kind: d.kind,
      measured: d.measured === true,
      reason: strOrNull(d.reason),
      evaluated_at: strOrNull(d.evaluated_at),
      subjects_measured: isNum(d.subjects_measured) ? d.subjects_measured : 0,
      firing: isNum(d.firing) ? d.firing : 0,
    })
  }
  return {
    enabled: typeof raw.enabled === 'boolean' ? raw.enabled : true,
    range: typeof raw.range === 'string' ? raw.range : INSIGHT_RANGE_DEFAULT,
    items: rows,
    total: isNum(raw.total) ? raw.total : rows.length,
    dismissed_total: isNum(raw.dismissed_total) ? raw.dismissed_total : 0,
    detectors: INSIGHT_KINDS.map((kind) => byKind.get(kind) ?? notEvaluated(kind)),
    generated_at: typeof raw.generated_at === 'string' ? raw.generated_at : '',
  }
}
