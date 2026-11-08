/**
 * W39 STRAT-07: the "Observability evidence" card's plots — plain SVG on ONE shared time axis (engine-free, like
 * `ExecutionTimeline`: the Run page loads no chart engine). What is drawn is `evidenceLanes.model.ts`; this file only
 * lays it out:
 *
 *   EvidenceAxisRow     the tick labels, once, above the lanes;
 *   EvidenceMarksPlot   a Splunk / OpenShift lane — an instant is a dot, an OpenShift first → last is a bar; every
 *                       mark carries its item as an SVG `<title>` (hover) and is focusable, and tells the card which
 *                       item is under the pointer / focus (the card prints it under the lanes);
 *   EvidenceSeriesPlot  one Prometheus series as a small line on the same axis (its own value range; a `null` value
 *                       is a gap, never a zero).
 *
 * The run window is shaded behind every plot. Colours come from `tokens.ts` only; text is React text children.
 */
import { CHART_FONT, CHART_VARS, RECHARTS_GRID } from './tokens'
import {
  instantLabel,
  markText,
  xOf,
  type EvidenceAxis,
  type EvidenceLaneView,
  type EvidenceSeries,
} from './evidenceLanes.model'

/** One packed row of marks, px. */
export const MARK_ROW_HEIGHT = 18
/** One Prometheus series' line, px. */
export const SERIES_ROW_HEIGHT = 28
const AXIS_HEIGHT = 20
const DOT_RADIUS = 4
const BAR_HEIGHT = 10
const MIN_BAR_WIDTH = 3
/** Room at each end of a plot, so a dot or a tick label at the edge is not cut. */
export const PLOT_PAD = 8

export type RunWindowMs = { startMs: number; endMs: number } | null

interface PlotBase {
  axis: EvidenceAxis
  width: number
  runWindow: RunWindowMs
}

const scaleX = (axis: EvidenceAxis, width: number) => (ms: number) =>
  PLOT_PAD + xOf(axis, ms, Math.max(1, width - 2 * PLOT_PAD))

/** The run window (shaded) and the tick gridlines, behind a plot `height` px tall. */
function Backdrop({ axis, width, runWindow, height }: PlotBase & { height: number }) {
  const x = scaleX(axis, width)
  return (
    <g aria-hidden="true">
      {runWindow && (
        <rect
          data-evidence-run-window=""
          x={x(runWindow.startMs)}
          y={0}
          width={Math.max(1, x(runWindow.endMs) - x(runWindow.startMs))}
          height={height}
          fill={CHART_VARS.measureTint}
          fillOpacity={0.45}
        />
      )}
      {axis.ticks.map((tick) => (
        <line key={tick} x1={x(tick)} x2={x(tick)} y1={0} y2={height} stroke={RECHARTS_GRID.stroke} strokeWidth={RECHARTS_GRID.strokeWidth} />
      ))}
    </g>
  )
}

export function EvidenceAxisRow({ axis, width }: { axis: EvidenceAxis; width: number }) {
  const x = scaleX(axis, width)
  return (
    <svg width={width} height={AXIS_HEIGHT} data-evidence-axis="" aria-hidden="true" focusable="false" className="block">
      {axis.ticks.map((tick, index) => {
        const anchor = axis.ticks.length > 1 && index === axis.ticks.length - 1 ? 'end' : index === 0 ? 'start' : 'middle'
        return (
          <g key={tick} data-evidence-tick="">
            <text x={x(tick)} y={AXIS_HEIGHT - 8} textAnchor={anchor} fill={CHART_VARS.axis} fontSize={CHART_FONT.tick}>
              {instantLabel(tick, axis.spanMs)}
            </text>
            <line x1={x(tick)} x2={x(tick)} y1={AXIS_HEIGHT - 5} y2={AXIS_HEIGHT} stroke={CHART_VARS.axis} strokeWidth={1} />
          </g>
        )
      })}
    </svg>
  )
}

/** A Splunk / OpenShift lane's timed items. `onActive` gets the item text under the pointer / focus (null on leave). */
export function EvidenceMarksPlot({
  view,
  axis,
  width,
  runWindow,
  onActive,
}: PlotBase & { view: EvidenceLaneView; onActive: (text: string | null) => void }) {
  const x = scaleX(axis, width)
  const height = view.rows * MARK_ROW_HEIGHT
  const fill = view.lane.source.startsWith('ocp') ? CHART_VARS.series[2] : CHART_VARS.series[0]
  return (
    <svg
      width={width}
      height={height}
      data-evidence-plot={view.lane.source}
      role="group"
      aria-label={`${view.lane.label}: ${view.marks.length} timed item${view.marks.length === 1 ? '' : 's'}`}
      className="block"
    >
      <Backdrop axis={axis} width={width} runWindow={runWindow} height={height} />
      {view.marks.map((mark, index) => {
        const text = markText(mark, axis.spanMs)
        const cy = mark.row * MARK_ROW_HEIGHT + MARK_ROW_HEIGHT / 2
        return (
          <g
            key={`${mark.startMs}:${index}`}
            data-evidence-mark={mark.kind}
            tabIndex={0}
            onMouseEnter={() => onActive(text)}
            onMouseLeave={() => onActive(null)}
            onFocus={() => onActive(text)}
            onBlur={() => onActive(null)}
          >
            <title>{text}</title>
            {mark.kind === 'span' ? (
              <rect
                x={x(mark.startMs)}
                y={cy - BAR_HEIGHT / 2}
                width={Math.max(MIN_BAR_WIDTH, x(mark.endMs) - x(mark.startMs))}
                height={BAR_HEIGHT}
                rx={2}
                fill={fill}
                stroke={CHART_VARS.card}
                strokeWidth={0.5}
              />
            ) : (
              <circle cx={x(mark.startMs)} cy={cy} r={DOT_RADIUS} fill={fill} stroke={CHART_VARS.card} strokeWidth={1} />
            )}
          </g>
        )
      })}
    </svg>
  )
}

/** One Prometheus series on the shared axis, scaled to its own value range. */
export function EvidenceSeriesPlot({ view, axis, width, runWindow }: PlotBase & { view: EvidenceSeries }) {
  const x = scaleX(axis, width)
  const pad = DOT_RADIUS
  const range = view.max - view.min
  const y = (value: number) =>
    range > 0 ? pad + (1 - (value - view.min) / range) * (SERIES_ROW_HEIGHT - 2 * pad) : SERIES_ROW_HEIGHT / 2
  const name = `${view.series.label}: ${view.drawn} point${view.drawn === 1 ? '' : 's'}`
  return (
    <svg width={width} height={SERIES_ROW_HEIGHT} data-evidence-series-plot="" role="img" aria-label={name} className="block">
      <title>{name}</title>
      <Backdrop axis={axis} width={width} runWindow={runWindow} height={SERIES_ROW_HEIGHT} />
      {view.segments.map((segment, index) =>
        segment.length === 1 ? (
          <circle key={index} cx={x(segment[0][0])} cy={y(segment[0][1])} r={2} fill={CHART_VARS.measure} />
        ) : (
          <polyline
            key={index}
            data-evidence-line=""
            points={segment.map(([at, value]) => `${x(at)},${y(value)}`).join(' ')}
            fill="none"
            stroke={CHART_VARS.measure}
            strokeWidth={1.5}
            strokeLinejoin="round"
          />
        ),
      )}
    </svg>
  )
}
