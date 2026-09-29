/**
 * `StackedColumnChart` inside `ChartFrame` (VIZ-104, Wave 2.5 K1) — the
 * component a page mounts.
 *
 * The frame owns every non-data state, the table view, the announcer, export
 * and full screen; this decides only what the frame is given:
 *
 *   - the frame's `series` is built FROM the drawn model
 *     (`stackedColumnToChartSeries`), so the plot, the screen-reader summary,
 *     the table and the CSV cannot disagree — a gap in a column is "—" in its
 *     cell, and the last column is each bucket's total;
 *   - a drawn state whose model holds nothing is NOT drawn as an empty plot.
 *     No bucket at all is `filtered-empty`, and so are buckets whose every
 *     measured value is 0 (the kit's rule: all-zero is a filter that matched
 *     nothing, not a chart of zeros — `handOverWhenEmpty`). Buckets of which
 *     nothing was measured are `not-measured`: "—" with a reason, never an
 *     empty chart that reads as zero.
 *
 * Its props mirror `TimeSeriesChartFrameProps`: a page passes `title`,
 * `takeaway`, `state` (`readyState(...)` from `chartState.ts` when the page
 * has its own loading and empty branches), `headingLevel`, `height`, `footer`,
 * and the `model` from `buildStackedColumnModel`.
 */
import { forwardRef, useMemo } from 'react'
import type { ChartState } from './chartState'
import ChartFrame, { type ChartFrameProps } from './ChartFrame'
import StackedColumnChart, { type StackedColumnChartProps } from './StackedColumnChart'
import { stackedColumnToChartSeries, type StackedColumnModel } from './stackedColumnModel'

export interface StackedColumnChartFrameProps
  extends Omit<ChartFrameProps, 'children' | 'series' | 'chartType' | 'axes' | 'format'>,
    Pick<StackedColumnChartProps, 'animate' | 'bucketNoun'> {
  /**
   * The model to draw. Required for `ready` / `truncated`; ignored (and
   * allowed to be null) for every other state, which the frame owns.
   */
  model: StackedColumnModel | null
  state: ChartState<unknown>
}

/** The chart type the summary and the export name it by. */
export const STACKED_COLUMN_CHART_TYPE = 'Stacked column chart'
/** The reason a drawn state of which nothing was measured shows. */
export const NOTHING_MEASURED_REASON = 'no value in this window was measured'

/** The state the frame is given: the page's, unless the model says there is nothing to draw. */
export function stackedFrameState(state: ChartState<unknown>, model: StackedColumnModel | null): ChartState<unknown> {
  if (!model || (state.status !== 'ready' && state.status !== 'truncated')) return state
  if (model.buckets.length > 0 && model.series.length > 0 && model.empty) {
    return { status: 'not-measured', reason: NOTHING_MEASURED_REASON, meta: state.meta }
  }
  // `handOverWhenEmpty`'s rule, written out: importing it would pull the donut into this chunk.
  if (model.empty || model.allZero) return { status: 'filtered-empty', meta: state.meta }
  return state
}

const StackedColumnChartFrame = forwardRef<HTMLDivElement, StackedColumnChartFrameProps>(function StackedColumnChartFrame(
  { model, state, animate, bucketNoun, height = 280, ...frameProps },
  ref,
) {
  const frameState = stackedFrameState(state, model)
  const drawn = model !== null && (frameState.status === 'ready' || frameState.status === 'truncated')
  const series = useMemo(() => (model && drawn ? stackedColumnToChartSeries(model) : null), [model, drawn])

  return (
    <ChartFrame
      {...frameProps}
      ref={ref}
      state={frameState}
      height={height}
      series={series}
      format={model?.format}
      chartType={STACKED_COLUMN_CHART_TYPE}
      axes={model ? { x: model.bucketTitle, y: model.valueTitle } : undefined}
    >
      {model && drawn ? (
        <StackedColumnChart
          model={model}
          // The frame's own title names the keyboard cursor's surface.
          title={frameProps.title}
          height={height}
          animate={animate}
          bucketNoun={bucketNoun}
        />
      ) : null}
    </ChartFrame>
  )
})

export default StackedColumnChartFrame
