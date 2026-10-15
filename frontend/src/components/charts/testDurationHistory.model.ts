/**
 * EXEC-04: one test's duration across its runs (`TestDurationHistory`), as
 * data. Pure: no React, no DOM, no colours.
 *
 * Input is `GET /api/v1/runs/{run_id}/tests/{test_id}/history` with the
 * EXEC-03 §4 additions: the points (newest first, as the API sends them), the
 * window's `duration_stats` (p50 / p95 / max over every execution in the
 * window, not only the points returned) and the project's `baseline` for the
 * test (`perf_baselines`: mean, stddev, p95).
 *
 *   - a run that reported no duration is a GAP, never 0 ms;
 *   - the baseline band is the mean ± one standard deviation, drawn only when
 *     the test has a baseline;
 *   - a run at least `OUTLIER_RATIO` x the reference (the baseline mean, else
 *     the window's p50) is an OUTLIER: the chart rings it and states its
 *     ratio, so a 4x run cannot pass for an ordinary peak.
 */
import type { VizStatus } from '@/lib/viz/contracts'
import type { DurationBaseline, TestDurationStats, TestHistoryPointTiming } from '@/types/executionMetrics'
import type { TestCaseHistoryPoint } from '@/types/runs'
import { formatDuration, formatNumber, formatRunWhen } from '@/utils/formatters'
import { vizStatusOf } from './executionTimeline.model'
import { tipContent, type TooltipContent, type TooltipRow } from './tooltip'

/** A run this many times the reference duration is an outlier. */
export const OUTLIER_RATIO = 2

export type DurationHistoryInputPoint = TestCaseHistoryPoint & TestHistoryPointTiming

export interface DurationHistoryPoint {
  key: string
  /** The run, as the API labels it ("Run #12 · build 240"). */
  label: string
  when: string | null
  status: VizStatus
  rawStatus: string
  /** ms; `null` = not reported (a gap). */
  durationMs: number | null
  /** `durationMs` over the reference (2 places); `null` without both. */
  ratio: number | null
  outlier: boolean
  setupMs: number | null
  callMs: number | null
  teardownMs: number | null
  workerId: string | null
}

export interface DurationHistoryBaseline {
  mean: number
  stddev: number
  /** The band: `max(0, mean - stddev)` … `mean + stddev`. */
  low: number
  high: number
  p95: number | null
  samples: number
}

export interface DurationHistoryModel {
  /** Oldest first. */
  points: DurationHistoryPoint[]
  /** Points carrying a duration. */
  measured: number
  baseline: DurationHistoryBaseline | null
  /** Window percentiles (`duration_stats`), `null` when not measured or not sent. */
  p50: number | null
  p95: number | null
  max: number | null
  n: number | null
  windowDays: number | null
  /** Why the window percentiles are missing, as the server put it. */
  statsReason: string | null
  /** What `ratio` is measured against. */
  reference: 'baseline' | 'p50' | null
  outliers: number
  /** The largest value the y axis must reach, ms. */
  yMax: number
}

const finiteNonNegative = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0

const orNull = (value: unknown): number | null => (finiteNonNegative(value) ? value : null)

export function buildDurationHistoryModel({
  history,
  durationStats,
  baseline,
}: {
  history: readonly DurationHistoryInputPoint[] | null | undefined
  durationStats?: TestDurationStats | null
  baseline?: DurationBaseline | null
}): DurationHistoryModel {
  const band: DurationHistoryBaseline | null =
    baseline && finiteNonNegative(baseline.mean_ms)
      ? (() => {
          const stddev = finiteNonNegative(baseline.stddev_ms) ? baseline.stddev_ms : 0
          return {
            mean: baseline.mean_ms,
            stddev,
            low: Math.max(0, baseline.mean_ms - stddev),
            high: baseline.mean_ms + stddev,
            p95: orNull(baseline.p95_ms),
            samples: finiteNonNegative(baseline.sample_count) ? baseline.sample_count : 0,
          }
        })()
      : null
  const statsMeasured = Boolean(durationStats && durationStats.measured !== false)
  const p50 = statsMeasured ? orNull(durationStats?.p50_ms) : null
  const p95 = statsMeasured ? orNull(durationStats?.p95_ms) : null
  const max = statsMeasured ? orNull(durationStats?.max_ms) : null

  const reference: DurationHistoryModel['reference'] = band && band.mean > 0 ? 'baseline' : p50 !== null && p50 > 0 ? 'p50' : null
  const referenceMs = reference === 'baseline' ? (band as DurationHistoryBaseline).mean : reference === 'p50' ? (p50 as number) : null

  const ordered = [...(history ?? [])].reverse()
  let outliers = 0
  const points = ordered.map((point, index): DurationHistoryPoint => {
    const durationMs = orNull(point.duration_ms)
    const exact = durationMs !== null && referenceMs !== null ? durationMs / referenceMs : null
    const ratio = exact !== null ? Math.round(exact * 100) / 100 : null
    // The rule reads the exact ratio: 1.999x rounds to "2.0x" on screen but is not 2x.
    const outlier = exact !== null && exact >= OUTLIER_RATIO
    if (outlier) outliers += 1
    return {
      key: point.run_id ?? `h-${index}`,
      label: point.run_label || (point.build_number ? `Build ${point.build_number}` : `Run ${index + 1}`),
      when: point.created_at ?? null,
      status: vizStatusOf(point.status),
      rawStatus: point.status ?? 'UNKNOWN',
      durationMs,
      ratio,
      outlier,
      setupMs: orNull(point.setup_ms),
      callMs: orNull(point.call_ms),
      teardownMs: orNull(point.teardown_ms),
      workerId: point.worker_id ?? null,
    }
  })
  const values = points.map((point) => point.durationMs).filter((v): v is number => v !== null)
  const yMax = Math.max(0, ...values, p95 ?? 0, p50 ?? 0, band?.high ?? 0)

  return {
    points,
    measured: values.length,
    baseline: band,
    p50,
    p95,
    max,
    n: statsMeasured && durationStats ? durationStats.n : null,
    windowDays: durationStats?.window_days ?? null,
    statsReason: durationStats && durationStats.measured === false ? (durationStats.reason ?? 'not measured') : null,
    reference,
    outliers,
    yMax,
  }
}

/** `2.0× baseline` / `2.0× p50` for an outlier or any point with a reference. */
export function ratioText(point: Pick<DurationHistoryPoint, 'ratio'>, reference: DurationHistoryModel['reference']): string | null {
  if (point.ratio === null || reference === null) return null
  return `${formatNumber(point.ratio, { maximumFractionDigits: 1 })}× ${reference === 'baseline' ? 'baseline' : 'p50'}`
}

/** One run's tooltip: the run, its status, the duration (or "not reported"), the ratio and the phases. */
export function durationHistoryTipContent(model: DurationHistoryModel, index: number): TooltipContent {
  const point = model.points[index]
  if (!point) return { rows: [] }
  const when = formatRunWhen(point.when)
  const phases = [
    point.setupMs !== null ? `setup ${formatDuration(point.setupMs)}` : null,
    point.callMs !== null ? `test ${formatDuration(point.callMs)}` : null,
    point.teardownMs !== null ? `teardown ${formatDuration(point.teardownMs)}` : null,
  ].filter(Boolean)
  const ratio = ratioText(point, model.reference)
  const rows: (TooltipRow | null)[] = [
    when ? { kind: 'dimension', key: 'when', label: 'When', value: when } : null,
    { kind: 'dimension', key: 'status', label: 'Status', value: point.rawStatus },
    {
      kind: 'value',
      key: 'duration',
      label: 'Duration',
      value: point.durationMs === null ? 'not reported' : formatDuration(Math.round(point.durationMs)),
    },
    ratio ? { kind: 'value', key: 'ratio', label: 'Against', value: ratio } : null,
    phases.length > 0 ? { kind: 'value', key: 'phases', label: 'Phases', value: phases.join(' · ') } : null,
    point.workerId ? { kind: 'dimension', key: 'worker', label: 'Worker', value: point.workerId } : null,
    point.outlier ? { kind: 'note', key: 'outlier', label: '', value: `At least ${OUTLIER_RATIO}× the reference: an outlier.` } : null,
  ]
  return tipContent(point.label, rows)
}
