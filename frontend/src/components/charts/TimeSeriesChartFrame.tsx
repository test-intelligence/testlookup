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

export interface TimeSeriesChartFrameProps
  extends Omit<ChartFrameProps, 'children' | 'series' | 'chartType' | 'axes' | 'tableExtras'>,
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
}

const TimeSeriesChartFrame = forwardRef<HTMLDivElement, TimeSeriesChartFrameProps>(function TimeSeriesChartFrame(
  { model, inProgressRuns, timeZone, locale, now, animate, height = 280, trendAnalysis, ...frameProps },
  ref,
) {
  const series = useMemo(() => (model ? timeSeriesToChartSeries(model) : null), [model])

  const trendRequested = trendAnalysis !== undefined && trendAnalysis !== false
  const analysis = useMemo(
    () => (trendRequested && model ? analyzeTrend(model.points) : null),
    [trendRequested, model],
  )
  const [shown, setShown] = useState<TrendOverlayState>(() => ({
    ...TREND_OVERLAYS_OFF,
    ...(typeof trendAnalysis === 'object' ? trendAnalysis.initialShown : undefined),
  }))
  const takeaway = analysis ? trendFrameTakeaway(analysis, shown, frameProps.takeaway) : frameProps.takeaway

  const releaseTable = model ? (
    <ReleaseMarkerTable markers={model.markers} outsideWindow={model.markersOutsideWindow} />
  ) : null

  return (
    <ChartFrame
      {...frameProps}
      ref={ref}
      height={height}
      takeaway={takeaway}
      series={series}
      chartType="Line and bar chart"
      axes={{ x: 'Day (UTC)', y: `${RATE_AXIS_TITLE} / ${EXECUTIONS_AXIS_TITLE}` }}
      tableExtras={
        analysis && model ? (
          <>
            {releaseTable}
            <TimeSeriesChartTrendTable analysis={analysis} days={model.points.map((point) => point.x)} />
          </>
        ) : (
          releaseTable
        )
      }
    >
      {model ? (
        <TimeSeriesChart
          model={model}
          // The frame's own title names the keyboard cursor's surface, so the
          // reader hears the chart they can see rather than a generic default.
          title={frameProps.title}
          height={height}
          animate={animate}
          inProgressRuns={inProgressRuns}
          timeZone={timeZone}
          locale={locale}
          now={now}
          trendOverlays={analysis ? { analysis, shown, onShownChange: setShown } : undefined}
        />
      ) : null}
    </ChartFrame>
  )
})

export default TimeSeriesChartFrame
