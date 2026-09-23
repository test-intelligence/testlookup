/**
 * The three VIZ-406 charts inside `ChartFrame` — the components a page mounts.
 *
 * One composite rather than three because the wiring is identical and the only
 * thing that differs is which model becomes the frame's `series`. That series is
 * built FROM the drawn model (`histogramToChartSeries` / `bandToChartSeries` /
 * `slowestToChartSeries`), so the plot, the screen-reader summary and the table
 * view cannot disagree: a percentile the server did not measure is `y: null`
 * with its reason, which the table prints as "—" and never as 0 ms.
 *
 * Durations in the table are formatted with `formatDuration` — `TimingCell`'s
 * formatter — so the table and the chart read the same. The slowest-tests
 * chart carries two units and therefore a formatter PER SERIES
 * (`SLOWEST_FORMAT`); one formatter for the whole chart printed its run counts
 * as durations.
 */
import { forwardRef } from 'react'
import { formatDuration } from '@/utils/formatters'
import type { ChartState } from './chartState'
import ChartFrame, { type ChartFrameProps } from './ChartFrame'
import { formatPlainValue, type SeriesFormat } from './chartText'
import DurationHistogram from './DurationHistogram'
import DurationTrend from './DurationTrend'
import SlowestTests from './SlowestTests'
import {
  bandToChartSeries,
  histogramToChartSeries,
  slowestToChartSeries,
  type DurationBandModel,
  type DurationHistogramModel,
  type SlowestTestsModel,
} from './durationBuckets'

/**
 * The slowest-tests chart draws TWO series in two different units — a p95
 * duration and a run count — so it needs a formatter per series.
 *
 * The run count stays a series rather than being dropped: it is the thing that
 * makes the ranking actionable (a p95 over 2 runs is noise, a p95 over 200 is a
 * fact), the plot already prints it beside every bar, and a table reader who
 * lost it would be reading a strictly lesser view of the same chart. What was
 * wrong was never the series, it was the single `formatDuration` that printed
 * "10 runs" as "10ms" in the table AND in the generated summary.
 *
 * MODULE-LEVEL, so its identity is stable: `format` is a dependency of the
 * frame's summary `useMemo`, and a fresh object each render would recompute the
 * summary — and re-announce the chart — on every render.
 */
const SLOWEST_FORMAT: SeriesFormat = { p95: formatDuration, runs: formatPlainValue }

type FrameShell = Omit<ChartFrameProps, 'children' | 'series' | 'chartType' | 'axes' | 'format'> & {
  state: ChartState<unknown>
  animate?: boolean
}

export type DurationChartFrameProps = FrameShell &
  (
    | { kind: 'histogram'; histogram: DurationHistogramModel | null }
    | { kind: 'trend'; band: DurationBandModel | null }
    | { kind: 'slowest'; slowest: SlowestTestsModel | null }
  )

const DurationChartFrame = forwardRef<HTMLDivElement, DurationChartFrameProps>(function DurationChartFrame(
  props,
  ref,
) {
  const { animate, height = 260, ...rest } = props

  if (rest.kind === 'histogram') {
    const { histogram, kind: _kind, ...frameProps } = rest
    void _kind
    return (
      <ChartFrame
        {...frameProps}
        ref={ref}
        height={height}
        series={histogram ? histogramToChartSeries(histogram) : null}
        chartType="Histogram"
        axes={{ x: 'Duration bucket', y: 'Executions' }}
        format={formatPlainValue}
        // NOT `footer={histogram.excludedStatement}`: the histogram states the
        // exclusions inside its own figure, where the axis they qualify is.
        // Both put the same sentence on screen twice, which a screen reader
        // reads twice and a sighted reader reads as two different facts.
      >
        {histogram ? (
          <DurationHistogram model={histogram} title={frameProps.title} height={height} animate={animate} />
        ) : null}
      </ChartFrame>
    )
  }

  if (rest.kind === 'trend') {
    const { band, kind: _kind, ...frameProps } = rest
    void _kind
    return (
      <ChartFrame
        {...frameProps}
        ref={ref}
        height={height}
        series={band ? bandToChartSeries(band) : null}
        chartType="Line chart with a shaded band"
        axes={{ x: 'Day (UTC)', y: 'Duration' }}
        format={formatDuration}
        // NOT `footer={band.notice}`, for the same reason as the histogram's
        // exclusions: `DurationTrend` states the p95-below-p50 notice inside
        // its own figure, next to the lines it qualifies. Both did, and the
        // first Linux baselines showed the sentence printed twice.
      >
        {band ? <DurationTrend model={band} title={frameProps.title} height={height} animate={animate} /> : null}
      </ChartFrame>
    )
  }

  const { slowest, kind: _kind, ...frameProps } = rest
  void _kind
  return (
    <ChartFrame
      {...frameProps}
      ref={ref}
      height={height}
      series={slowest ? slowestToChartSeries(slowest) : null}
      chartType="Ranked bars"
      axes={{ x: 'Test', y: 'p95 duration' }}
      format={SLOWEST_FORMAT}
    >
      {slowest ? <SlowestTests model={slowest} /> : null}
    </ChartFrame>
  )
})

export default DurationChartFrame
