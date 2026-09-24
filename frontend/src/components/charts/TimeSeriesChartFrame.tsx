/**
 * `TimeSeriesChart` inside `ChartFrame` (VIZ-403) — the component a page mounts.
 *
 * The frame owns every non-data state, the table view, the announcer and the
 * export ref; this only decides what the frame is given. The two things worth
 * noticing:
 *
 *   - the frame's `series` is built FROM the drawn model
 *     (`timeSeriesToChartSeries`), not fetched separately, so the plot, the
 *     screen-reader summary and the table cannot disagree — a gap in the line is
 *     a "—" in the table;
 *   - the release markers ride along as `tableExtras`, because a marker is drawn
 *     over the plot rather than being a value in the series, and a table reader
 *     must not lose the release boundaries.
 *
 * `measured: false` never becomes a zero here: the frame's own `not-measured`
 * state shows "—" with the server's reason, and inside a drawn chart a
 * `measured: false` bucket is a gap carrying its reason into the tooltip.
 *
 * VIZ-405: `trendAnalysis` turns on the trend overlays. The frame then owns
 * which overlays are shown (its takeaway follows them), computes ONE analysis
 * that the chart, the takeaway and the table all read, and adds the analysis
 * to the table view. Without the prop, the frame is exactly the VIZ-403 frame.
 *
 * VIZ-407: `zoom` adds the range brush under the plot. The DRAWN model is then
 * the full model sliced to the zoomed days (`sliceTimeSeriesModel`), and the
 * summary, the table and the export follow it and say so (`zoomNote`). Two
 * things deliberately do NOT follow the slice:
 *
 *   - the trend analysis is computed on the FULL model and only its per-day
 *     values are cut to the range — its anomaly rule looks back four weeks and
 *     a moving average needs the six days before a day, so a value recomputed
 *     on a ten-day slice would be a different (and wrong) number;
 *   - the release table keeps every marker, marking the ones the zoom took off
 *     the plot rather than dropping them.
 *
 * Without the prop, nothing about the frame changes.
 */
import { forwardRef, useMemo, useState } from 'react'
import { analyzeTrend, trendFrameTakeaway, type TrendOverlayState } from '@/lib/trendStats'
import type { ChartState } from './chartState'
import ChartFrame, { type ChartFrameProps } from './ChartFrame'
import ReleaseMarkerTable from './ReleaseMarkerTable'
import TimeSeriesChart, { type TimeSeriesChartProps } from './TimeSeriesChart'
import { TREND_OVERLAYS_OFF } from './TimeSeriesChartOverlayStyle'
import TimeSeriesChartTrendTable from './TimeSeriesChartTrendTable'
import {
  EXECUTIONS_AXIS_TITLE,
  RATE_AXIS_TITLE,
  timeSeriesToChartSeries,
  type TimeSeriesModel,
} from './timeSeriesModel'
import ChartRangeBrush from './zoom/ChartRangeBrush'
import { useFrameZoom } from './zoom/useFrameZoom'
import {
  CALENDAR_WORDS,
  markersOutsideRange,
  sliceTimeSeriesModel,
  sliceTrendAnalysis,
  zoomOptionsOf,
  type ChartZoomOptions,
} from './zoom/zoomModel'

export interface TimeSeriesChartFrameProps
  extends Omit<ChartFrameProps, 'children' | 'series' | 'chartType' | 'axes' | 'tableExtras' | 'zoomNote'>,
    Pick<TimeSeriesChartProps, 'inProgressRuns' | 'timeZone' | 'locale' | 'now' | 'animate'> {
  /**
   * The model to draw. Required for `ready` / `truncated`; ignored (and
   * allowed to be null) for every other state, which the frame owns.
   */
  model: TimeSeriesModel | null
  state: ChartState<unknown>
  /**
   * VIZ-405 trend overlays: `true` offers them (both off), an object can
   * start some on. Leave it out and nothing about the frame changes.
   */
  trendAnalysis?: boolean | { initialShown?: Partial<TrendOverlayState> }
  /**
   * VIZ-407 local zoom: `true` draws the range brush; the object form can open
   * zoomed and can offer "Apply as time filter" (see `ChartZoomOptions`).
   */
  zoom?: boolean | ChartZoomOptions
}

const NO_DAYS: readonly string[] = []

const TimeSeriesChartFrame = forwardRef<HTMLDivElement, TimeSeriesChartFrameProps>(function TimeSeriesChartFrame(
  { model, inProgressRuns, timeZone, locale, now, animate, height = 280, trendAnalysis, zoom, ...frameProps },
  ref,
) {
  const trendRequested = trendAnalysis !== undefined && trendAnalysis !== false
  // ONE analysis, always on the FULL model — never recomputed on a zoomed slice.
  const analysis = useMemo(
    () => (trendRequested && model ? analyzeTrend(model.points) : null),
    [trendRequested, model],
  )
  const [shown, setShown] = useState<TrendOverlayState>(() => ({
    ...TREND_OVERLAYS_OFF,
    ...(typeof trendAnalysis === 'object' ? trendAnalysis.initialShown : undefined),
  }))
  const takeaway = analysis ? trendFrameTakeaway(analysis, shown, frameProps.takeaway) : frameProps.takeaway

  const xs = useMemo(() => (model ? model.points.map((point) => point.x) : NO_DAYS), [model])
  const zoomState = useFrameZoom({
    options: zoomOptionsOf(zoom),
    xs,
    model,
    title: frameProps.title,
    words: CALENDAR_WORDS,
    trendAnalysis: analysis !== null,
    markersOutside: (range) => (model ? markersOutsideRange(model.markers, xs, range).length : 0),
  })
  const { range } = zoomState
  // The strip's context: the pass rate over the WHOLE window, gaps kept.
  const spark = useMemo(() => (model ? [model.points.map((point) => point.rate)] : undefined), [model])

  // What is DRAWN: the full model, or its zoomed slice.
  const view = useMemo(() => (model ? sliceTimeSeriesModel(model, range) : null), [model, range])
  const viewDays = useMemo(() => (view ? view.points.map((point) => point.x) : NO_DAYS), [view])
  const viewAnalysis = useMemo(
    () => (analysis && range ? sliceTrendAnalysis(analysis, viewDays) : analysis),
    [analysis, range, viewDays],
  )
  const series = useMemo(() => (view ? timeSeriesToChartSeries(view) : null), [view])

  const outsideView = useMemo(
    () => (model && range ? markersOutsideRange(model.markers, xs, range).map((marker) => marker.x) : undefined),
    [model, xs, range],
  )
  const releaseTable = model ? (
    // The FULL model's markers: a zoom marks the ones it hides, it never drops them.
    <ReleaseMarkerTable markers={model.markers} outsideWindow={model.markersOutsideWindow} outsideView={outsideView} />
  ) : null
  const zoomTableNote = zoomState.note ? (
    <p data-chart-zoom-table-note="" className="mt-2 px-2 text-xs text-[var(--color-text-secondary)]">
      {zoomState.note}
    </p>
  ) : null

  return (
    <ChartFrame
      {...frameProps}
      ref={ref}
      height={height}
      takeaway={takeaway}
      series={series}
      scopeLabel={zoomState.scopeLabel(frameProps.scopeLabel)}
      changeLabel={zoomState.changeLabel ?? frameProps.changeLabel}
      zoomNote={zoomState.note}
      chartType="Line and bar chart"
      axes={{ x: 'Day (UTC)', y: `${RATE_AXIS_TITLE} / ${EXECUTIONS_AXIS_TITLE}` }}
      tableExtras={
        <>
          {zoomTableNote}
          {releaseTable}
          {viewAnalysis && view ? (
            <TimeSeriesChartTrendTable analysis={viewAnalysis} days={viewDays} zoomNote={range ? zoomState.note : undefined} />
          ) : null}
        </>
      }
    >
      {view ? (
        <>
          <TimeSeriesChart
            model={view}
            // The frame's own title names the keyboard cursor's surface, so the
            // reader hears the chart they can see rather than a generic default.
            title={frameProps.title}
            height={height}
            animate={animate}
            inProgressRuns={inProgressRuns}
            timeZone={timeZone}
            locale={locale}
            now={now}
            trendOverlays={viewAnalysis ? { analysis: viewAnalysis, shown, onShownChange: setShown } : undefined}
          />
          {zoomState.brush ? (
            // A BAND scale: the execution bars give every day a slot, so the
            // strip's days are slots too, centred as the bars are.
            <ChartRangeBrush {...zoomState.brush} scale="band" spark={spark} />
          ) : null}
        </>
      ) : null}
    </ChartFrame>
  )
})

export default TimeSeriesChartFrame
