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
 */
import { forwardRef, useMemo } from 'react'
import type { ChartState } from './chartState'
import ChartFrame, { type ChartFrameProps } from './ChartFrame'
import ReleaseMarkerTable from './ReleaseMarkerTable'
import TimeSeriesChart, { type TimeSeriesChartProps } from './TimeSeriesChart'
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
}

const TimeSeriesChartFrame = forwardRef<HTMLDivElement, TimeSeriesChartFrameProps>(function TimeSeriesChartFrame(
  { model, inProgressRuns, timeZone, locale, now, animate, height = 280, ...frameProps },
  ref,
) {
  const series = useMemo(() => (model ? timeSeriesToChartSeries(model) : null), [model])
  return (
    <ChartFrame
      {...frameProps}
      ref={ref}
      height={height}
      series={series}
      chartType="Line and bar chart"
      axes={{ x: 'Day (UTC)', y: `${RATE_AXIS_TITLE} / ${EXECUTIONS_AXIS_TITLE}` }}
      tableExtras={
        model ? <ReleaseMarkerTable markers={model.markers} outsideWindow={model.markersOutsideWindow} /> : null
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
        />
      ) : null}
    </ChartFrame>
  )
})

export default TimeSeriesChartFrame
