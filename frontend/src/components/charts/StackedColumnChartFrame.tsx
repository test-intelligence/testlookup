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
 *     measured value is 0 (the kit's rule: all-zero is not a chart of zeros —
 *     `handOverWhenEmpty`). Buckets of which nothing was measured are
 *     `not-measured`: "—" with a reason, never an empty chart that reads as zero.
 *   - that empty state blames the filters ONLY when the page says filters are
 *     set (`filtersApplied`). Otherwise it states the window, neutrally: "No
 *     executions in this window" (R1 F3). A new project whose only run is
 *     still streaming has an all-zero window and no filter at all; "No data
 *     matches the current filters" told that reader to clear filters they
 *     never set.
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
  /**
   * Whether the page has a filter set that could have emptied this window (a
   * suite, a release, a branch). Only then does an all-zero or bucketless
   * window read "No data matches the current filters" (with "Clear filters"
   * when `onClearFilters` is given); otherwise it reads `emptyWindowMessage`.
   * Default `false`: the neutral words are true either way.
   */
  filtersApplied?: boolean
}

/** The chart type the summary and the export name it by. */
export const STACKED_COLUMN_CHART_TYPE = 'Stacked column chart'
/** The reason a drawn state of which nothing was measured shows. */
export const NOTHING_MEASURED_REASON = 'no value in this window was measured'

/** What an empty window says when no filter emptied it: "No executions in this window". */
export function emptyWindowMessage(model: Pick<StackedColumnModel, 'valueTitle'>): string {
  return `No ${model.valueTitle.toLowerCase()} in this window`
}

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
  { model, state, animate, bucketNoun, height = 280, filtersApplied = false, emptyMessage, ...frameProps },
  ref,
) {
  const frameState = stackedFrameState(state, model)
  // The frame decided "empty" from the model (not the page): say why in the words that are true.
  const derivedEmpty = frameState.status === 'filtered-empty' && state.status !== 'filtered-empty'
  const emptyWords = emptyMessage ?? (derivedEmpty && model && !filtersApplied ? emptyWindowMessage(model) : undefined)
  const drawn = model !== null && (frameState.status === 'ready' || frameState.status === 'truncated')
  const series = useMemo(() => (model && drawn ? stackedColumnToChartSeries(model) : null), [model, drawn])

  return (
    <ChartFrame
      {...frameProps}
      ref={ref}
      state={frameState}
      emptyMessage={emptyWords}
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
