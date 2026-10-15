/**
 * `ExecutionTimeline` (EXEC-04) — the run's waterfall: one row per worker (or
 * per packed row when the report named no worker), one bar per test from its
 * start to its finish, coloured AND patterned by status (failed bars carry the
 * kit's diagonal decal, so a failure is never told by hue alone).
 *
 * What is drawn is `executionTimeline.model.ts`; this file only lays it out.
 * Engine-free (plain SVG): a waterfall of up to 2,000 rectangles needs no
 * chart engine, and the Run page loads none for it.
 *
 * Text: the row labels are HTML (they truncate with CSS, so no width is sized
 * to one font), the axis ticks are SVG text in the axis's own unit
 * (`durationTickLabel`), and the tooltip is the kit's `ChartTooltipBody` —
 * every name a React text child, never markup (names come from ingested CI
 * files).
 */
import { memo, useCallback, useMemo, useRef, useState, type MouseEvent } from 'react'
import type { VizStatus } from '@/lib/viz/contracts'
import { TIP_BOX_STYLE, ChartTooltipBody } from './ChartTooltipBody'
import { useContainerWidth } from './chartLayout'
import { durationTickLabel } from './durationBuckets'
import {
  timelineBarTipContent,
  type ExecutionTimelineModel,
  type TimelineBar,
} from './executionTimeline.model'
import { durationScale } from './niceScale'
import {
  ChartLegend,
  patternFill,
  renderPatterns,
  statusPatternId,
  statusPatternSpecs,
  useChartPatternPrefix,
  type LegendEntry,
} from './patterns'
import { CHART_VARS, STATUS_ENCODING, STATUS_STACK_ORDER } from './tokens'

export interface ExecutionTimelineProps {
  model: ExecutionTimelineModel
  /** Clicking a bar (the Run page opens that test). */
  onSelect?: (testId: string) => void
  /** The test open beside the chart: its bar is outlined. */
  selectedTestId?: string | null
}

/** The row-label column, px. */
export const LANE_LABEL_WIDTH = 168
/** One row's height and the bar inside it, px. */
export const LANE_HEIGHT = 22
const BAR_HEIGHT = 14
const AXIS_HEIGHT = 26
/** The plot's width before it is measured (and in jsdom, which has no layout). */
const FALLBACK_WIDTH = 720
/** Room right of the plot so the last tick label is not cut. */
const RIGHT_PAD = 8
/** The thinnest bar drawn: a 3 ms test in a 20 min run is still a visible mark. */
export const MIN_BAR_WIDTH = 2
/** The rows scroll past this height. */
const MAX_ROWS_HEIGHT = 440
const AXIS_INTERVALS = 6
/** The tip's distance from the pointer, and its widest, px. */
const TIP_OFFSET = 14
const TIP_MAX_WIDTH = 320

export const TIMELINE_CAPTION =
  'Each bar is one test, from its start to its finish, measured from the earliest start in the run.'

interface Scale {
  x: (ms: number) => number
  width: (bar: TimelineBar) => number
}

const BarsLayer = memo(function BarsLayer({
  bars,
  scale,
  prefix,
  clickable,
  onEnter,
  onLeave,
  onSelect,
}: {
  bars: readonly TimelineBar[]
  scale: Scale
  prefix: string
  clickable: boolean
  onEnter: (index: number, event: MouseEvent<SVGRectElement>) => void
  onLeave: () => void
  onSelect: (index: number) => void
}) {
  return (
    <g data-timeline-bars="">
      {bars.map((bar, index) => (
        <rect
          key={`${bar.testId}:${index}`}
          data-timeline-bar=""
          data-test-id={bar.testId}
          data-status={bar.status}
          data-lane={bar.lane}
          x={scale.x(bar.start)}
          y={bar.lane * LANE_HEIGHT + (LANE_HEIGHT - BAR_HEIGHT) / 2}
          width={scale.width(bar)}
          height={BAR_HEIGHT}
          rx={2}
          fill={patternFill(statusPatternId(prefix, bar.status))}
          stroke={CHART_VARS.card}
          strokeWidth={0.5}
          style={clickable ? { cursor: 'pointer' } : undefined}
          onMouseEnter={(event) => onEnter(index, event)}
          onMouseLeave={onLeave}
          onClick={clickable ? () => onSelect(index) : undefined}
        />
      ))}
    </g>
  )
})

export default function ExecutionTimeline({ model, onSelect, selectedTestId = null }: ExecutionTimelineProps) {
  const prefix = useChartPatternPrefix()
  const [measureRef, measured] = useContainerWidth<HTMLDivElement>()
  const figureRef = useRef<HTMLElement | null>(null)
  /** The hovered bar and where the pointer entered it, in the figure's box (the tip is placed there). */
  const [hover, setHover] = useState<{ index: number; x: number; y: number; width: number } | null>(null)

  const width = measured > 0 ? measured : FALLBACK_WIDTH
  const plotWidth = Math.max(40, width - RIGHT_PAD)
  const axis = useMemo(() => durationScale(model.spanMs > 0 ? model.spanMs : 1, { intervals: AXIS_INTERVALS }), [model.spanMs])
  const top = axis.domain[1]
  const scale = useMemo<Scale>(() => {
    const x = (ms: number) => (ms / top) * plotWidth
    return {
      x,
      width: (bar) => Math.max(MIN_BAR_WIDTH, x(bar.end) - x(bar.start)),
    }
  }, [top, plotWidth])

  const statuses = useMemo<VizStatus[]>(() => {
    const present = new Set(model.bars.map((bar) => bar.status))
    return STATUS_STACK_ORDER.filter((status) => present.has(status))
  }, [model.bars])
  const legend = useMemo<LegendEntry[]>(
    () =>
      statuses.map((status) => ({
        key: status,
        label: STATUS_ENCODING[status].label,
        status,
        fill: patternFill(statusPatternId(prefix, status)),
      })),
    [statuses, prefix],
  )

  const onEnter = useCallback((index: number, event: MouseEvent<SVGRectElement>) => {
    const box = figureRef.current?.getBoundingClientRect()
    setHover({
      index,
      x: event.clientX - (box?.left ?? 0),
      y: event.clientY - (box?.top ?? 0),
      width: box?.width ?? 0,
    })
  }, [])
  const onLeave = useCallback(() => setHover(null), [])
  const select = useCallback(
    (index: number) => {
      const bar = model.bars[index]
      if (bar && onSelect) onSelect(bar.testId)
    },
    [model.bars, onSelect],
  )

  const rowsHeight = model.lanes.length * LANE_HEIGHT
  const hoveredBar = hover !== null ? model.bars[hover.index] : undefined
  const selectedBar = selectedTestId ? model.bars.find((bar) => bar.testId === selectedTestId) : undefined

  return (
    <figure ref={figureRef} data-chart="execution-timeline" data-lane-mode={model.mode} className="relative m-0 flex flex-col gap-1">
      <svg width={0} height={0} aria-hidden="true" focusable="false" className="absolute">
        <defs>{renderPatterns(statusPatternSpecs(prefix, statuses))}</defs>
      </svg>

      {/* The time axis, over the plot column. */}
      <div className="flex">
        <div className="shrink-0" style={{ width: LANE_LABEL_WIDTH }} />
        <div className="min-w-0 flex-1 overflow-hidden">
          <svg width={width} height={AXIS_HEIGHT} data-timeline-axis="" aria-hidden="true" focusable="false">
            {axis.ticks.map((tick, index) => {
              const x = scale.x(tick)
              const anchor = index === 0 ? 'start' : index === axis.ticks.length - 1 ? 'end' : 'middle'
              return (
                <g key={tick}>
                  <text x={x} y={AXIS_HEIGHT - 10} textAnchor={anchor} fill={CHART_VARS.axis} fontSize={11}>
                    {durationTickLabel(tick, axis)}
                  </text>
                  <line x1={x} x2={x} y1={AXIS_HEIGHT - 6} y2={AXIS_HEIGHT} stroke={CHART_VARS.axis} strokeWidth={1} />
                </g>
              )
            })}
          </svg>
        </div>
      </div>

      <div className="overflow-y-auto" style={{ maxHeight: MAX_ROWS_HEIGHT }} data-timeline-rows="">
        <div className="flex">
          <ul className="m-0 shrink-0 list-none p-0 pr-2" style={{ width: LANE_LABEL_WIDTH }} data-timeline-lanes="">
            {model.lanes.map((lane) => (
              <li
                key={lane.key}
                data-timeline-lane={lane.packed ? 'packed' : 'worker'}
                data-worker-id={lane.workerId ?? undefined}
                title={lane.label}
                className="flex items-center justify-between gap-2 truncate text-xs text-[var(--color-text-secondary)]"
                style={{ height: LANE_HEIGHT }}
              >
                <span className="truncate">{lane.label}</span>
                <span className="shrink-0 tabular-nums text-[10px] text-[var(--color-text-muted)]">{lane.bars.length}</span>
              </li>
            ))}
          </ul>
          <div ref={measureRef} className="relative min-w-0 flex-1">
            <svg width={width} height={rowsHeight} data-timeline-plot="" role="img" aria-label={`Execution timeline: ${model.shown} tests on ${model.lanes.length} rows`}>
              {model.lanes.map((lane) =>
                lane.index % 2 === 1 ? (
                  <rect
                    key={lane.key}
                    x={0}
                    y={lane.index * LANE_HEIGHT}
                    width={plotWidth}
                    height={LANE_HEIGHT}
                    fill={CHART_VARS.grid}
                    fillOpacity={0.35}
                  />
                ) : null,
              )}
              {axis.ticks.map((tick) => (
                <line
                  key={tick}
                  x1={scale.x(tick)}
                  x2={scale.x(tick)}
                  y1={0}
                  y2={rowsHeight}
                  stroke={CHART_VARS.grid}
                  strokeDasharray="3 3"
                />
              ))}
              <BarsLayer
                bars={model.bars}
                scale={scale}
                prefix={prefix}
                clickable={Boolean(onSelect)}
                onEnter={onEnter}
                onLeave={onLeave}
                onSelect={select}
              />
              {[selectedBar, hoveredBar].map((bar, index) =>
                bar ? (
                  <rect
                    key={index === 0 ? 'selected' : 'hovered'}
                    data-timeline-mark={index === 0 ? 'selected' : 'hovered'}
                    x={scale.x(bar.start) - 1}
                    y={bar.lane * LANE_HEIGHT + (LANE_HEIGHT - BAR_HEIGHT) / 2 - 1}
                    width={scale.width(bar) + 2}
                    height={BAR_HEIGHT + 2}
                    rx={3}
                    fill="none"
                    stroke={CHART_VARS.text}
                    strokeWidth={index === 0 ? 2 : 1.5}
                    pointerEvents="none"
                  />
                ) : null,
              )}
            </svg>
          </div>
        </div>
      </div>

      {/* The tip lives in the figure, not in the rows' scroll box, so the box never cuts it. */}
      {hoveredBar && hover && (
        <div
          data-timeline-tip=""
          className="pointer-events-none absolute z-10"
          style={{
            left: Math.max(0, Math.min(hover.x + TIP_OFFSET, hover.width - TIP_MAX_WIDTH)),
            top: hover.y + TIP_OFFSET,
            maxWidth: TIP_MAX_WIDTH,
          }}
        >
          <ChartTooltipBody
            marked
            content={timelineBarTipContent(hoveredBar, model.lanes[hoveredBar.lane])}
            style={TIP_BOX_STYLE}
          />
        </div>
      )}

      <ChartLegend entries={legend} />
      <figcaption data-chart-axis-caption="" className="text-xs text-[var(--color-text-secondary)]">
        {TIMELINE_CAPTION}
      </figcaption>
      {model.notes.map((note) => (
        <p key={note} data-timeline-note="" className="m-0 text-xs text-[var(--color-text-secondary)]">
          {note}
        </p>
      ))}
    </figure>
  )
}
