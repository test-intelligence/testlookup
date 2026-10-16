/**
 * TEST HELPER (TRND-06): the server's trend summary (`GET /analytics/
 * trend-summary`, contract §1.3) computed from a trend tail and the coverage
 * suites, the way `trend_summary_service.summarize()` computes it. Page tests
 * mock `useTrendSummary` with it, so a test that hands the page a trend still
 * sees the verdict, the score and the gap that trend implies — now drawn from
 * the server's answer rather than derived on the page.
 */
import { shiftDayIso, utcDayIso } from '@/utils/calendarDay'
import type { TrendSummary } from '@/types/trends'

interface Row {
  date: string
  passed: number
  failed: number
  skipped: number
  broken?: number
}
interface SuiteRow {
  suite_name: string
  passed: number
  failed: number
  skipped: number
}

const UNTAGGED = ['', 'unknown suite', 'unknown', 'untagged']
const DIMENSIONS = [
  ['data_coverage', 'Data coverage', 0.4],
  ['sample_size', 'Sample size', 0.25],
  ['variance_stability', 'Variance stability', 0.2],
  ['tag_quality', 'Tag quality', 0.15],
] as const

const halfUp = (x: number) => Math.floor(x + 0.5)
const round = (x: number, dp: number) => Math.round(x * 10 ** dp) / 10 ** dp
const mean = (xs: readonly number[]) => xs.reduce((s, x) => s + x, 0) / xs.length

export function summaryFromTrend(
  trend: readonly Row[] | null | undefined,
  days: number,
  suites: readonly SuiteRow[] | null | undefined = [],
  today: string = utcDayIso(),
): TrendSummary {
  const rows = Array.isArray(trend) ? trend : []
  const byDay = new Map(rows.map((r) => [r.date.slice(0, 10), r]))
  const cells = Array.from({ length: days }, (_, i) => {
    const iso = shiftDayIso(today, -(days - 1 - i))
    const r = byDay.get(iso)
    return {
      iso,
      ex: r ? r.passed + r.failed + r.skipped + (r.broken ?? 0) : 0,
      passed: r?.passed ?? 0,
      failed: r?.failed ?? 0,
      broken: r?.broken ?? 0,
    }
  })
  const active = cells.filter((c) => c.ex > 0)
  const daysWithRuns = active.length
  const passed = rows.reduce((s, r) => s + r.passed, 0)
  const failed = rows.reduce((s, r) => s + r.failed, 0)
  const skipped = rows.reduce((s, r) => s + r.skipped, 0)
  const broken = rows.reduce((s, r) => s + (r.broken ?? 0), 0)
  const total = passed + failed + skipped + broken
  const evaluated = passed + failed + broken
  const perDay = active.map((c) => {
    const e = c.passed + c.failed + c.broken
    return e > 0 ? (c.passed / e) * 100 : 0
  })
  let stability = 0
  let sd: number | null = null
  if (active.length === 1) stability = 45
  else if (active.length >= 2) {
    const m = mean(perDay)
    sd = Math.sqrt(mean(perDay.map((x) => (x - m) ** 2)))
    stability = Math.max(0, Math.min(100, 100 - (sd / 30) * 100))
  }
  const suiteList = Array.isArray(suites) ? suites : []
  const suiteTotal = suiteList.reduce((s, x) => s + x.passed + x.failed + x.skipped, 0)
  const untagged = suiteList
    .filter((s) => UNTAGGED.includes((s.suite_name ?? '').trim().toLowerCase()))
    .reduce((s, x) => s + x.passed + x.failed + x.skipped, 0)
  const untaggedShare = suiteTotal > 0 ? untagged / suiteTotal : 0
  const scores: Record<string, number> = {
    data_coverage: days > 0 ? Math.min(100, (daysWithRuns / days) * 100) : 0,
    sample_size: Math.min(100, total),
    variance_stability: stability,
    tag_quality: Math.max(0, 100 - untaggedShare * 100),
  }
  const score = halfUp(DIMENSIONS.reduce((s, [id, , w]) => s + scores[id] * w, 0))
  const isos = active.map((c) => c.iso).sort()

  let gap: TrendSummary['gap'] = null
  if (daysWithRuns > 0 && daysWithRuns < days) {
    let best = 0
    let bestStart = -1
    let bestEnd = -1
    let runStart = -1
    for (let i = 0; i <= cells.length; i++) {
      const empty = i < cells.length && cells[i].ex === 0
      if (empty) {
        if (runStart === -1) runStart = i
        continue
      }
      if (runStart !== -1) {
        const len = i - runStart
        if (len > best) {
          best = len
          bestStart = runStart
          bestEnd = i - 1
        }
        runStart = -1
      }
    }
    if (best > 0) {
      gap = {
        silent_days: best,
        start: cells[bestStart].iso,
        end: cells[bestEnd].iso,
        ongoing: cells[bestEnd].iso === today,
        expected_runs_missed: best,
      }
    }
  }

  let verdict: string
  if (total === 0) verdict = 'PENDING'
  else if (score < 33 || daysWithRuns < 3) verdict = 'INSUFFICIENT'
  else {
    const half = Math.max(1, Math.floor(perDay.length / 2))
    const recent = mean(perDay.slice(-half))
    const prior = mean(perDay.slice(0, half))
    verdict = recent < prior - 10 ? 'DECLINING' : score >= 66 ? 'HEALTHY' : 'MIXED'
  }

  return {
    window_days: days,
    today,
    measured: total > 0,
    reason: total > 0 ? null : 'No executions in this window.',
    days_with_runs: daysWithRuns,
    empty_days: days - daysWithRuns,
    active_day_share: round(days > 0 ? daysWithRuns / days : 0, 4),
    executions: { total, passed, failed, broken, skipped, evaluated },
    pass_rate: evaluated > 0 ? round((passed / evaluated) * 100, 1) : null,
    variance: { stddev_pp: sd === null ? null : round(sd, 2), stability: round(stability, 2) },
    gap,
    last_run_day: isos.length ? isos[isos.length - 1] : null,
    previous_run_day: isos.length > 1 ? isos[isos.length - 2] : null,
    untagged_share: round(untaggedShare, 4),
    confidence: {
      score,
      dimensions: DIMENSIONS.map(([id, label, weight]) => ({ id, label, score: round(scores[id], 2), weight })),
      verdict,
      band: score >= 66 ? 'High' : score >= 33 ? 'Moderate' : 'Low',
    },
  }
}
