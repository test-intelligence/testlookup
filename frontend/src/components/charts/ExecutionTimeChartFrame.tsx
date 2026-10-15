/**
 * `ExecutionTimeTrend` inside `ChartFrame` (EXEC-04b) — the component a page
 * mounts. The frame's series (summary, "View as table", CSV) is built FROM the
 * drawn model (`executionTimeToChartSeries`), so a wall clock the report did
 * not carry is "—" with its reason in the table, exactly as it is a gap in
 * the plot, and never 0 ms.
 */
import { forwardRef, useMemo } from 'react'
import type { ChartState } from './chartStateCore'
import ChartFrame, { type ChartFrameProps } from './ChartFrame'
import ExecutionTimeTrend from './ExecutionTimeTrend'
import { EXECUTION_TREND_X_AXIS, EXECUTION_TREND_Y_AXIS } from './executionTimeTip'
import { executionTimeToChartSeries, formatMs, type ExecutionTimeModel } from './executionTimeModel'

export type ExecutionTimeChartFrameProps = Omit<ChartFrameProps, 'children' | 'series' | 'chartType' | 'axes' | 'format'> & {
  state: ChartState<unknown>
  model: ExecutionTimeModel | null
  animate?: boolean
}

const ExecutionTimeChartFrame = forwardRef<HTMLDivElement, ExecutionTimeChartFrameProps>(function ExecutionTimeChartFrame(
  { model, animate, height = 260, ...frameProps },
  ref,
) {
  const series = useMemo(() => (model ? executionTimeToChartSeries(model) : null), [model])
  return (
    <ChartFrame
      {...frameProps}
      ref={ref}
      height={height}
      series={series}
      chartType="Two-line chart"
      axes={{ x: model ? EXECUTION_TREND_X_AXIS[model.grain] : EXECUTION_TREND_X_AXIS.day, y: EXECUTION_TREND_Y_AXIS }}
      format={formatMs}
    >
      {model ? <ExecutionTimeTrend model={model} title={frameProps.title} height={height} animate={animate} /> : null}
    </ChartFrame>
  )
})

export default ExecutionTimeChartFrame
