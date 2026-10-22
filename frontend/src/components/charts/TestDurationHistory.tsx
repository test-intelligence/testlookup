/**
 * `TestDurationHistory` (EXEC-04) — one test's duration per run, oldest left,
 * on the test's History tab: the duration line (a run that reported no
 * duration is a gap), the window's p50 and p95 as reference lines, and the
 * project baseline as a hatched band (mean ± 1σ) with its mean. A run at
 * least 2x the reference is ringed and labelled with its ratio, so one slow
 * run stands out rather than merely topping the scale.
 *
 * What is drawn is `testDurationHistory.model.ts`. Plain SVG (no engine): the
 * test page loads none for it. The tooltip is the kit's `ChartTooltipBody`;
 * run labels are React text children, never markup.
 */
import { useMemo, useState } from 'react'
import { TIP_BOX_STYLE, ChartTooltipBody } from './ChartTooltipBody'
import { useContainerWidth } from './chartLayout'
import { durationTickLabel } from './durationBuckets'
import { zeroBasedScale } from './niceScale'
import { ChartLegend, useChartPatternPrefix, type LegendEntry } from './patterns'
import {
  durationHistoryTipContent,
  ratioText,
  type DurationHistoryModel,
} from './testDurationHistory.model'
import { CHART_FONT, CHART_VARS, RECHARTS_GRID } from './tokens'

export interface TestDurationHistoryProps {
  model: DurationHistoryModel
  height?: number
}

const FALLBACK_WIDTH = 560
const MARGIN = { top: 14, right: 12, bottom: 24, left: 60 }
/** p95's dash, as on the kit's duration trend. */
const P95_DASH = '7 3'
const MEAN_DASH = '2 3'
/** The duration line: a hue apart from the p50 (yellow), p95 (blue) and band (green) the kit's duration trend uses. */
const LINE_COLOR = CHART_VARS.series[6]
const TIP_GAP = 10
const TIP_MAX_WIDTH = 280
const P50_COLOR = CHART_VARS.series[3]
const P95_COLOR = CHART_VARS.series[4]
const BAND_COLOR = CHART_VARS.series[2]

export const DURATION_HISTORY_CAPTION =
  'Duration of this test per run, oldest left. A run that reported no duration is a gap, never 0 ms.'

export default function TestDurationHistory({ model, height = 190 }: TestDurationHistoryProps) {
  const [measureRef, measured] = useContainerWidth<HTMLDivElement>()
  const [hovered, setHovered] = useState<number | null>(null)
  const prefix = useChartPatternPrefix()
  const bandPattern = `${prefix}-baseline-band`

  const width = measured > 0 ? measured : FALLBACK_WIDTH
  const plotW = Math.max(40, width - MARGIN.left - MARGIN.right)
  const plotH = Math.max(40, height - MARGIN.top - MARGIN.bottom)
  const axis = useMemo(() => zeroBasedScale(model.yMax > 0 ? model.yMax : 1, { intervals: 4 }), [model.yMax])
  const top = axis.domain[1]
  const n = model.points.length
  const step = n > 0 ? plotW / n : plotW
  const x = (index: number) => MARGIN.left + (index + 0.5) * step
  const y = (ms: number) => MARGIN.top + plotH - (ms / top) * plotH

  // The duration line, broken at every run without a duration (at most 50 runs: no memo needed).
  const segments: string[] = []
  let current: string[] = []
  model.points.forEach((point, index) => {
    if (point.durationMs === null) {
      if (current.length > 1) segments.push(current.join(' '))
      current = []
      return
    }
    current.push(`${current.length === 0 ? 'M' : 'L'}${x(index).toFixed(1)},${y(point.durationMs).toFixed(1)}`)
  })
  if (current.length > 1) segments.push(current.join(' '))

  const legend: LegendEntry[] = [
    { key: 'duration', label: 'Duration', stroke: LINE_COLOR, dot: true },
    ...(model.p50 !== null ? [{ key: 'p50', label: `p50 (${model.windowDays ?? 30} d)`, stroke: P50_COLOR }] : []),
    ...(model.p95 !== null ? [{ key: 'p95', label: `p95 (${model.windowDays ?? 30} d)`, stroke: P95_COLOR, dash: P95_DASH }] : []),
    ...(model.baseline ? [{ key: 'baseline', label: 'Baseline mean ± 1σ', fill: `url(#${bandPattern})` }] : []),
  ]

  const hoveredPoint = hovered !== null ? model.points[hovered] : undefined
  const firstLabel = model.points[0]?.label
  const lastLabel = n > 1 ? model.points[n - 1]?.label : undefined

  return (
    <figure data-chart="test-duration-history" className="relative m-0 flex flex-col gap-1">
      <div ref={measureRef} className="relative w-full" data-duration-history-plot="">
        <svg width={width} height={height} role="img" aria-label={`Duration over ${n} runs`}>
          <defs>
            <pattern id={bandPattern} width={6} height={6} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <rect width={6} height={6} fill={CHART_VARS.card} />
              <rect width={1.5} height={6} fill={BAND_COLOR} />
            </pattern>
          </defs>

          {axis.ticks.map((tick) => (
            <g key={tick}>
              <line x1={MARGIN.left} x2={MARGIN.left + plotW} y1={y(tick)} y2={y(tick)} stroke={RECHARTS_GRID.stroke} strokeWidth={RECHARTS_GRID.strokeWidth} />
              <text x={MARGIN.left - 6} y={y(tick)} dy={4} textAnchor="end" fill={CHART_VARS.axis} fontSize={CHART_FONT.tick}>
                {durationTickLabel(tick, axis)}
              </text>
            </g>
          ))}

          {model.baseline && (
            <g data-duration-baseline="">
              <rect
                data-duration-baseline-band=""
                x={MARGIN.left}
                y={y(model.baseline.high)}
                width={plotW}
                height={Math.max(1, y(model.baseline.low) - y(model.baseline.high))}
                fill={`url(#${bandPattern})`}
                stroke={BAND_COLOR}
                strokeWidth={0.75}
                fillOpacity={0.9}
              />
              <line
                x1={MARGIN.left}
                x2={MARGIN.left + plotW}
                y1={y(model.baseline.mean)}
                y2={y(model.baseline.mean)}
                stroke={BAND_COLOR}
                strokeWidth={1.5}
                strokeDasharray={MEAN_DASH}
              />
            </g>
          )}

          {model.p50 !== null && (
            <line data-duration-ref="p50" x1={MARGIN.left} x2={MARGIN.left + plotW} y1={y(model.p50)} y2={y(model.p50)} stroke={P50_COLOR} strokeWidth={1.5} />
          )}
          {model.p95 !== null && (
            <line
              data-duration-ref="p95"
              x1={MARGIN.left}
              x2={MARGIN.left + plotW}
              y1={y(model.p95)}
              y2={y(model.p95)}
              stroke={P95_COLOR}
              strokeWidth={1.5}
              strokeDasharray={P95_DASH}
            />
          )}

          {segments.map((d) => (
            <path key={d} d={d} fill="none" stroke={LINE_COLOR} strokeWidth={2} />
          ))}

          {model.points.map((point, index) =>
            point.durationMs === null ? null : (
              <g key={`${point.key}:${index}`} data-duration-point={point.outlier ? 'outlier' : 'run'} data-status={point.status}>
                {point.outlier && (
                  <>
                    <circle cx={x(index)} cy={y(point.durationMs)} r={7} fill="none" stroke={CHART_VARS.status.failed} strokeWidth={2} />
                    <text
                      data-duration-outlier-label=""
                      x={x(index)}
                      // Above the ring, or under it when the run is at the top of the scale.
                      y={y(point.durationMs) - 11 < 10 ? y(point.durationMs) + 21 : y(point.durationMs) - 11}
                      textAnchor={index === n - 1 && n > 1 ? 'end' : index === 0 && n > 1 ? 'start' : 'middle'}
                      fill={CHART_VARS.text}
                      fontSize={CHART_FONT.dataLabel}
                    >
                      {ratioText(point, model.reference)}
                    </text>
                  </>
                )}
                <circle cx={x(index)} cy={y(point.durationMs)} r={3.5} fill={CHART_VARS.status[point.status]} stroke={CHART_VARS.card} strokeWidth={1} />
              </g>
            ),
          )}

          {hoveredPoint && (
            <line
              x1={x(hovered as number)}
              x2={x(hovered as number)}
              y1={MARGIN.top}
              y2={MARGIN.top + plotH}
              stroke={CHART_VARS.axis}
              strokeWidth={1}
              pointerEvents="none"
            />
          )}

          {/* One hover column per run (a gap too: its tooltip says "not reported"). */}
          {model.points.map((point, index) => (
            <rect
              key={`hit:${point.key}:${index}`}
              data-duration-hit=""
              x={MARGIN.left + index * step}
              y={MARGIN.top}
              width={step}
              height={plotH}
              fill="transparent"
              onMouseEnter={() => setHovered(index)}
              onMouseLeave={() => setHovered(null)}
            />
          ))}

          {firstLabel && (
            <text x={MARGIN.left} y={height - 6} textAnchor="start" fill={CHART_VARS.axis} fontSize={CHART_FONT.tick}>
              {firstLabel}
            </text>
          )}
          {lastLabel && (
            <text x={MARGIN.left + plotW} y={height - 6} textAnchor="end" fill={CHART_VARS.axis} fontSize={CHART_FONT.tick}>
              {lastLabel}
            </text>
          )}
        </svg>
        {hoveredPoint && (
          <div
            data-duration-history-tip=""
            className="pointer-events-none absolute z-10"
            // Beside the hovered run, on the side away from the plot's nearer edge, so it never covers that run's point.
            style={
              x(hovered as number) > width / 2
                ? { right: Math.max(0, width - x(hovered as number) + TIP_GAP), top: MARGIN.top, maxWidth: TIP_MAX_WIDTH }
                : { left: x(hovered as number) + TIP_GAP, top: MARGIN.top, maxWidth: TIP_MAX_WIDTH }
            }
          >
            <ChartTooltipBody marked content={durationHistoryTipContent(model, hovered as number)} style={TIP_BOX_STYLE} />
          </div>
        )}
      </div>
      <ChartLegend entries={legend} />
      <figcaption data-chart-axis-caption="" className="text-xs text-[var(--color-text-secondary)]">
        {DURATION_HISTORY_CAPTION}
      </figcaption>
    </figure>
  )
}
