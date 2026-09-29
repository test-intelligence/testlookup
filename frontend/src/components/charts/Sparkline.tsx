/**
 * `Sparkline` (VIZ-104, kit gap K3) — the small trend line in a KPI cell,
 * drawn from a REAL series or not at all. The geometry and the words are the
 * model's (`Sparkline.model.ts`); this file only draws them.
 *
 * It is deliberately NOT a `ChartFrame` chart: a KPI cell has room for a line,
 * not for a title bar, a toolbar or a table view, and the number the line
 * trends is already printed beside it. What it keeps from the kit is the part
 * that matters in a cell: tokens for every colour, and an accessible name
 * (`role="img"`) that carries the point count, the latest, the lowest and the
 * highest value — the svg itself is hidden from assistive technology.
 *
 * It draws no text, so there is nothing for a wider font (DejaVu Sans on the
 * Linux CI runner) to push out of the cell: the width is the cell's, the
 * height is fixed, and the end dot is inset by its own radius.
 *
 * Fewer than 2 measured points renders `null` — the caller keeps its own
 * caption (Overview's dashed "no data yet" line) rather than this inventing a
 * flat line to fill the space.
 */
import { useId, useMemo, type CSSProperties } from 'react'
import {
  SPARKLINE_HEIGHT,
  SPARKLINE_INSET,
  SPARKLINE_TONE_COLOR,
  buildSparklineModel,
  defaultSparklineFormat,
  sparklineAreaPath,
  sparklineLabel,
  sparklineLinePath,
  type SparklineFormat,
  type SparklineTone,
} from './Sparkline.model'

export type { SparklineTone }

export interface SparklineProps {
  /** OLDEST first. `null` is not measured: the line breaks there. */
  series: readonly (number | null)[]
  /** Required: what the line is a trend OF. The start of the accessible name. */
  label: string
  tone?: SparklineTone
  /** A fixed value range (a pass rate is `[0, 100]`); by default the series' own min and max. */
  domain?: readonly [number, number]
  /** Formats latest / min / max in the accessible name. */
  format?: SparklineFormat
  /** Drawn height in px (default 28). The width is the container's. */
  height?: number
  /** A soft fill under the line. */
  area?: boolean
  className?: string
}

/** The end dot's diameter, px: twice the inset, so the model's inset keeps it inside the cell. */
const DOT = SPARKLINE_INSET * 2

export default function Sparkline({
  series,
  label,
  tone = 'neutral',
  domain,
  format = defaultSparklineFormat,
  height = SPARKLINE_HEIGHT,
  area = false,
  className,
}: SparklineProps) {
  // `useId` yields ":r1:" (React 18) or "«r1»" (React 19); neither is safe
  // inside `url(#…)`, so keep only the characters an id selector allows.
  const gradId = `spark-${useId().replace(/[^A-Za-z0-9_-]/g, '')}`
  const lo = domain?.[0]
  const hi = domain?.[1]
  const model = useMemo(
    () => buildSparklineModel(series, { domain: lo !== undefined && hi !== undefined ? [lo, hi] : undefined, height }),
    [series, lo, hi, height],
  )
  if (!model) return null

  const color = SPARKLINE_TONE_COLOR[tone]
  const dotStyle: CSSProperties = {
    position: 'absolute',
    left: `${model.last.x}%`,
    top: model.last.y,
    width: DOT,
    height: DOT,
    marginLeft: -DOT / 2,
    marginTop: -DOT / 2,
    borderRadius: '50%',
    background: color,
  }

  return (
    <div
      role="img"
      aria-label={sparklineLabel(label, model, format)}
      data-testid="sparkline"
      className={className}
      // The horizontal padding is the dot's radius, so a dot on the newest
      // (or oldest) slot sits inside the cell instead of half outside it.
      style={{ position: 'relative', height, paddingInline: DOT / 2, boxSizing: 'border-box' }}
    >
      <div aria-hidden style={{ position: 'relative', width: '100%', height: '100%' }}>
        <svg
          width="100%"
          height={height}
          viewBox={`0 0 100 ${height}`}
          preserveAspectRatio="none"
          overflow="visible"
          focusable="false"
          style={{ display: 'block' }}
        >
          {area && (
            <>
              <defs>
                <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={color} stopOpacity={0.28} />
                  <stop offset="100%" stopColor={color} stopOpacity={0} />
                </linearGradient>
              </defs>
              <path data-part="area" d={sparklineAreaPath(model)} fill={`url(#${gradId})`} stroke="none" />
            </>
          )}
          <path
            data-part="line"
            d={sparklineLinePath(model)}
            fill="none"
            stroke={color}
            strokeWidth={1.5}
            strokeLinecap="round"
            strokeLinejoin="round"
            vectorEffect="non-scaling-stroke"
          />
        </svg>
        <span data-part="end-dot" style={dotStyle} />
      </div>
    </div>
  )
}
