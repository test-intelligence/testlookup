/**
 * `ChartResponsive` — Recharts' `ResponsiveContainer` for every Wave-2 chart,
 * which in full screen shows the drawing at `PRESENTATION_SCALE` (VIZ-608,
 * Wave 2.4 review A5).
 *
 * Outside full screen it IS `<ResponsiveContainer width="100%" height>`: not
 * one element more, so no page and no committed screenshot changes.
 *
 * In full screen the chart is laid out exactly as it would be on a page
 * `1/scale` as wide, at `height` (the LAYOUT height the chart asks for), and
 * that drawing is scaled up by `scale` from its top-left corner. The outer
 * box takes the scaled size in the flow, so nothing below the plot moves
 * under it. What reads the drawing's geometry copes with the scale:
 *
 *   - Recharts turns a pointer into chart coordinates relative to its own
 *     wrapper, dividing by the wrapper's CSS transform scale
 *     (`getRelativeCoordinate`);
 *   - `PinnedTip` and the donut's ring divide what they measure on screen by
 *     `chartScaleOf(chart)` (`tipPlacement.ts`), so a tooltip is placed in the same chart
 *     coordinates it is drawn in;
 *   - the tooltip is drawn INSIDE the scaled wrapper, so it is scaled with the
 *     chart: its 12 px text reads at 16 px in full screen too.
 *
 * Canvas (ECharts) charts are not wrapped: CSS cannot reach canvas text, and
 * they size theirs from `useChartFullscreen()` themselves.
 */
import type { ReactElement } from 'react'
import { ResponsiveContainer } from 'recharts'
import { usePresentationScale } from './framePlotHeight'

export interface ChartResponsiveProps {
  /** The LAYOUT height, in chart pixels: what Recharts draws in before any scaling. */
  height: number
  children: ReactElement
}

export default function ChartResponsive({ height, children }: ChartResponsiveProps) {
  const scale = usePresentationScale()
  if (scale === 1) {
    return (
      <ResponsiveContainer width="100%" height={height}>
        {children}
      </ResponsiveContainer>
    )
  }
  return (
    <div data-chart-presentation={scale} style={{ height: Math.round(height * scale) }}>
      <div style={{ width: `${100 / scale}%`, height, transform: `scale(${scale})`, transformOrigin: '0 0' }}>
        <ResponsiveContainer width="100%" height={height}>
          {children}
        </ResponsiveContainer>
      </div>
    </div>
  )
}
