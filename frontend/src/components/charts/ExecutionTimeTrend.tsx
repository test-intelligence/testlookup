/**
 * `ExecutionTimeTrend` (EXEC-04b) — Wall clock and Test time (summed) as two
 * lines, per UTC day (Trends › Execution time) or per run (a suite's "Suite
 * execution time"). The distance between the lines is the parallelism.
 *
 * Built like `DurationTrend` (a zero-based duration axis computed here, tick
 * elements instead of a formatter key, the pinned React tooltip, the keyboard
 * cursor reading the same content), without the band: two different
 * measurements, not two percentiles of one.
 *
 * A gap is a gap. A run whose report carried no timestamps has no wall clock;
 * the wall line breaks there and the tooltip says why, while the summed line
 * goes on — the summed time never stands in for the wall clock. Every point is
 * also a dot, so a measured value between two gaps is still drawn.
 *
 * Names (a build number) reach the plot only as text: the tooltip is React
 * (`PinnedTip`), never a string formatter. The words (axis titles, caption,
 * gap notice, tooltip content) are in `executionTimeTip.ts`.
 */
import { useMemo } from 'react'
import { CartesianGrid, ComposedChart, Legend, Line, Tooltip, XAxis, YAxis } from 'recharts'
import { CHART_FONT, CHART_VARS, RECHARTS_AXIS_TICK, RECHARTS_AXIS_TITLE, RECHARTS_GRID } from './tokens'
import { useChartAnimation } from './motion'
import { cursorPoint, useChartCursor, type ChartCursorPoint } from './ChartCursor'
import { PinnedTip, useColumnMark } from './ChartTooltip'
import { useFramePlotLayoutHeight } from './framePlotHeight'
import { COLUMN_SIDES } from './tipPlacement'
import ChartResponsive from './ChartResponsive'
import { ChartLegend, type LegendEntry } from './patterns'
import { durationTickLabel } from './durationBuckets'
import { durationScale, type NiceScale } from './niceScale'
import { SUMMED_LABEL, WALL_CLOCK_LABEL, type ExecutionTimeModel } from './executionTimeModel'
import {
  EXECUTION_TREND_CAPTION,
  EXECUTION_TREND_X_AXIS,
  EXECUTION_TREND_Y_AXIS,
  executionTimeTipContent,
  SUMMED_COLOR,
  SUMMED_DASH,
  WALL_COLOR,
  wallGapNotice,
} from './executionTimeTip'

/** Half a point's drawn mark (the active dot is 4 px). */
const POINT_HALF = 4
const NOTE = 'text-xs text-[var(--color-text-secondary)]'
/** What the caption and the gap notice under the plot keep back in full screen, px. */
const NOTES_RESERVE = 56

export interface ExecutionTimeTrendProps {
  model: ExecutionTimeModel
  /** Names the focusable drawing surface (see `useChartCursor`). */
  title?: string
  height?: number
  animate?: boolean
}

/** A duration tick as an element: no `*formatter` key (chart-guard), the axis's own unit. */
function DurationTick({ x, y, payload, axis }: { x?: number; y?: number; payload?: { value?: number }; axis: NiceScale | null }) {
  return (
    <text x={x} y={y} dy={4} textAnchor="end" fill={CHART_VARS.axis} fontSize={CHART_FONT.tick}>
      {durationTickLabel(payload?.value ?? 0, axis)}
    </text>
  )
}

export default function ExecutionTimeTrend({
  model,
  title = 'Execution time',
  height: requestedHeight = 260,
  animate: requested,
}: ExecutionTimeTrendProps) {
  const height = useFramePlotLayoutHeight(requestedHeight, NOTES_RESERVE)
  const animate = useChartAnimation(requested)
  // From zero to a nice top over the larger line (a duration starts at zero; so does this axis).
  const axis = useMemo(() => (model.max !== null && model.max > 0 ? durationScale(model.max, { intervals: 4 }) : null), [model.max])
  const ticks = useMemo(() => new Map(model.points.map((point) => [point.key, point.tick])), [model.points])
  const rows = model.points.map((point) => ({ key: point.key, wall: point.wall, summed: point.summed }))
  const legend: LegendEntry[] = [
    { key: 'wall', label: WALL_CLOCK_LABEL, stroke: WALL_COLOR },
    { key: 'summed', label: SUMMED_LABEL, stroke: SUMMED_COLOR, dash: SUMMED_DASH },
  ]

  const cursorPoints = useMemo<ChartCursorPoint[]>(
    () => model.points.map((point, index) => cursorPoint(point.key, executionTimeTipContent(model, index))),
    [model],
  )
  const cursor = useChartCursor({ title, chartType: 'two-line chart', points: cursorPoints, noun: model.grain })
  const notice = wallGapNotice(model)

  return (
    <figure data-chart="execution-time-trend" data-grain={model.grain} className="m-0 flex flex-col gap-1">
      <div
        data-execution-trend-plot=""
        className="w-full focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
        {...cursor.surfaceProps}
      >
        <ChartResponsive height={height}>
          {/* `accessibilityLayer={false}` — explicitly; see `ChartCursor`. */}
          <ComposedChart data={rows} margin={{ top: 8, right: 12, left: 8, bottom: 8 }} accessibilityLayer={false}>
            <CartesianGrid {...RECHARTS_GRID} vertical={false} />
            <XAxis
              dataKey="key"
              axisLine={false}
              tickLine={false}
              tick={RECHARTS_AXIS_TICK}
              // The point's short name ("Oct 14", or "#" and the build number); the tooltip and the table keep the full one.
              tickFormatter={(key: string) => ticks.get(key) ?? key}
              minTickGap={12}
              dy={8}
              height={48}
              label={{ value: EXECUTION_TREND_X_AXIS[model.grain], position: 'insideBottom', ...RECHARTS_AXIS_TITLE }}
            />
            <YAxis
              domain={axis?.domain}
              ticks={axis?.ticks}
              interval={0}
              axisLine={false}
              tickLine={false}
              tick={<DurationTick axis={axis} />}
              width={78}
              label={{ value: EXECUTION_TREND_Y_AXIS, angle: -90, position: 'insideLeft', ...RECHARTS_AXIS_TITLE }}
            />
            {/* Pinned beside its point (`PinnedTip`): a fixed origin, no slide. */}
            <Tooltip
              key={cursor.tipKey}
              content={<ExecutionTimeTooltip model={model} />}
              position={{ x: 0, y: 0 }}
              isAnimationActive={false}
              {...cursor.tipProps}
            />
            <Legend content={() => <ChartLegend entries={legend} />} />
            <Line
              dataKey="wall"
              name={WALL_CLOCK_LABEL}
              type="linear"
              stroke={WALL_COLOR}
              strokeWidth={2}
              dot={{ r: 2, strokeWidth: 0, fill: WALL_COLOR }}
              activeDot={{ r: POINT_HALF }}
              connectNulls={false}
              isAnimationActive={animate}
            />
            <Line
              dataKey="summed"
              name={SUMMED_LABEL}
              type="linear"
              stroke={SUMMED_COLOR}
              strokeDasharray={SUMMED_DASH}
              strokeWidth={2}
              dot={{ r: 2, strokeWidth: 0, fill: SUMMED_COLOR }}
              activeDot={{ r: POINT_HALF }}
              connectNulls={false}
              isAnimationActive={animate}
            />
          </ComposedChart>
        </ChartResponsive>
        {cursor.readout}
      </div>

      <figcaption data-chart-axis-caption="" className={NOTE}>
        {EXECUTION_TREND_CAPTION[model.grain]}
      </figcaption>
      {notice && (
        <p data-chart-wall-gaps="" className={NOTE}>
          {notice}
        </p>
      )}
    </figure>
  )
}

/** Recharts' tooltip: the shared content model, pinned beside the point's column. */
function ExecutionTimeTooltip({
  active,
  label,
  coordinate,
  model,
}: {
  active?: boolean
  label?: string | number
  coordinate?: { x?: number; y?: number }
  model: ExecutionTimeModel
}) {
  const index = model.points.findIndex((point) => point.key === String(label))
  const { mark, gap, chartBox, sweep } = useColumnMark(coordinate, model.points.length, POINT_HALF)
  const content = active && index >= 0 ? executionTimeTipContent(model, index) : null
  return <PinnedTip content={content} mark={mark} sides={COLUMN_SIDES} align="start" gap={gap} chartBox={chartBox} sweep={sweep} />
}
