/**
 * Trends page types (W08): the server-side trend summary (TRND-06) and the
 * trends report's email / availability shapes (TRND-01).
 *
 * `parseTrendSummary` is the defensive guard every reader of
 * `GET /api/v1/analytics/trend-summary` goes through: a secondary read must
 * never crash its page (W07's "batch.items is not iterable"), so a missing or
 * malformed answer is `null` and the page renders "Score not available".
 */
import type { EnvelopeMeta } from '@/lib/viz/contracts'

export interface TrendSummaryDimension {
  id: string
  label: string
  score: number
  weight: number
}

export interface TrendSummaryGap {
  silent_days: number
  start: string
  end: string
  ongoing: boolean
  expected_runs_missed: number
}

export interface TrendSummary {
  window_days: number
  today: string
  measured: boolean
  reason: string | null
  days_with_runs: number
  empty_days: number
  active_day_share: number
  executions: {
    total: number
    passed: number
    failed: number
    broken: number
    skipped: number
    evaluated: number
  }
  pass_rate: number | null
  variance: { stddev_pp: number | null; stability: number }
  gap: TrendSummaryGap | null
  last_run_day: string | null
  previous_run_day: string | null
  untagged_share: number
  confidence: {
    score: number
    dimensions: TrendSummaryDimension[]
    verdict: string
    band: string
  }
  meta?: EnvelopeMeta
}

export interface EmailAvailability {
  available: boolean
  code: string | null
  reason: string | null
}

export interface EmailTrendsBody {
  project_id: string
  days: number
  release_id?: string | string[]
  suite_name?: string[]
  recipient_user_ids: string[]
  recipient_emails: string[]
}

export interface EmailTrendsResult {
  status: string
  sent_to: number
  recipient: string | null
  window_days: number
}

// ── guards ───────────────────────────────────────────────────────────────

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function isNum(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v)
}

function isNumOrNull(v: unknown): v is number | null {
  return v === null || isNum(v)
}

function isStrOrNull(v: unknown): v is string | null {
  return v === null || typeof v === 'string'
}

function parseDimension(raw: unknown): TrendSummaryDimension | null {
  if (!isRecord(raw)) return null
  const { id, label, score, weight } = raw
  if (typeof id !== 'string' || typeof label !== 'string') return null
  if (!isNum(score) || !isNum(weight)) return null
  return { id, label, score, weight }
}

function parseGap(raw: unknown): TrendSummaryGap | null | undefined {
  // `undefined` = malformed (the whole summary is refused); `null` = no gap.
  if (raw === null || raw === undefined) return null
  if (!isRecord(raw)) return undefined
  const { silent_days, start, end, ongoing, expected_runs_missed } = raw
  if (!isNum(silent_days) || !isNum(expected_runs_missed)) return undefined
  if (typeof start !== 'string' || typeof end !== 'string') return undefined
  if (typeof ongoing !== 'boolean') return undefined
  return { silent_days, start, end, ongoing, expected_runs_missed }
}

const EXECUTION_KEYS = ['total', 'passed', 'failed', 'broken', 'skipped', 'evaluated'] as const

/**
 * The §0 defensive guard over `GET /api/v1/analytics/trend-summary`.
 * Returns `null` for anything that is not a complete, well-typed summary —
 * the reader then says "Score not available" instead of drawing a half-read
 * verdict. Unknown extra keys are ignored (shapes only grow).
 */
export function parseTrendSummary(raw: unknown): TrendSummary | null {
  if (!isRecord(raw)) return null
  const {
    window_days,
    today,
    measured,
    reason,
    days_with_runs,
    empty_days,
    active_day_share,
    executions,
    pass_rate,
    variance,
    gap,
    last_run_day,
    previous_run_day,
    untagged_share,
    confidence,
    meta,
  } = raw
  if (!isNum(window_days) || typeof today !== 'string' || typeof measured !== 'boolean') return null
  if (!isStrOrNull(reason ?? null)) return null
  if (!isNum(days_with_runs) || !isNum(empty_days) || !isNum(active_day_share)) return null
  if (!isRecord(executions)) return null
  const ex: Record<string, number> = {}
  for (const key of EXECUTION_KEYS) {
    const v = executions[key]
    if (!isNum(v)) return null
    ex[key] = v
  }
  if (!isNumOrNull(pass_rate ?? null)) return null
  if (!isRecord(variance)) return null
  if (!isNumOrNull(variance.stddev_pp ?? null) || !isNum(variance.stability)) return null
  const parsedGap = parseGap(gap)
  if (parsedGap === undefined) return null
  if (!isStrOrNull(last_run_day ?? null) || !isStrOrNull(previous_run_day ?? null)) return null
  if (!isNum(untagged_share)) return null
  if (!isRecord(confidence)) return null
  const { score, dimensions, verdict, band } = confidence
  if (!isNum(score) || typeof verdict !== 'string' || typeof band !== 'string') return null
  if (!Array.isArray(dimensions)) return null
  const dims: TrendSummaryDimension[] = []
  for (const d of dimensions) {
    const parsed = parseDimension(d)
    if (!parsed) return null
    dims.push(parsed)
  }
  return {
    window_days,
    today,
    measured,
    reason: (reason as string | null | undefined) ?? null,
    days_with_runs,
    empty_days,
    active_day_share,
    executions: {
      total: ex.total,
      passed: ex.passed,
      failed: ex.failed,
      broken: ex.broken,
      skipped: ex.skipped,
      evaluated: ex.evaluated,
    },
    pass_rate: (pass_rate as number | null | undefined) ?? null,
    variance: {
      stddev_pp: (variance.stddev_pp as number | null | undefined) ?? null,
      stability: variance.stability,
    },
    gap: parsedGap,
    last_run_day: (last_run_day as string | null | undefined) ?? null,
    previous_run_day: (previous_run_day as string | null | undefined) ?? null,
    untagged_share,
    confidence: { score, dimensions: dims, verdict, band },
    ...(isRecord(meta) ? { meta: meta as unknown as EnvelopeMeta } : {}),
  }
}

/** A guard for the availability read: anything malformed is "not available". */
export function parseEmailAvailability(raw: unknown): EmailAvailability | null {
  if (!isRecord(raw)) return null
  const { available, code, reason } = raw
  if (typeof available !== 'boolean') return null
  return {
    available,
    code: typeof code === 'string' ? code : null,
    reason: typeof reason === 'string' ? reason : null,
  }
}
