/**
 * How the VIZ-405 trend overlays are DRAWN — the one place the style lives, so
 * the plot, the legend and the toggles' own swatches cannot drift apart.
 *
 * Each overlay is a dash AND a token colour, never a colour alone; an anomaly
 * is a SHAPE (a downward triangle), not a recoloured dot. Kept out of the
 * component module so it holds only components (fast refresh).
 */
import { MOVING_AVERAGE_LABEL, TREND_LINE_LABEL, type TrendOverlayState } from '@/lib/trendStats'
import { CHART_VARS } from './tokens'

export const TREND_OVERLAY_STYLE = {
  movingAverage: { stroke: CHART_VARS.series[2], dash: '8 4', curve: 'monotone', label: MOVING_AVERAGE_LABEL },
  trendLine: { stroke: CHART_VARS.series[3], dash: '2 4', curve: 'linear', label: TREND_LINE_LABEL },
} as const

export type TrendOverlayKey = keyof typeof TREND_OVERLAY_STYLE
/** In drawing order. */
export const TREND_OVERLAY_KEYS: readonly TrendOverlayKey[] = ['movingAverage', 'trendLine']

export const TREND_OVERLAY_WIDTH = 2
/**
 * The card-coloured halo under each overlay: 2 px of card on either side of
 * the 2 px line, so the line's nearest neighbour is always the card — never a
 * bar it would otherwise sit on at 1.02-1.75:1.
 */
export const TREND_OVERLAY_HALO_WIDTH = TREND_OVERLAY_WIDTH + 4
/**
 * The halos' Recharts layer: above the bars (`DefaultZIndexes.bar`, 300) and
 * below every line (`DefaultZIndexes.line`, 400). Recharts 3 paints each
 * z-index layer through a portal, and a Line that mounts later — an overlay
 * toggled back on — is APPENDED to its layer, so JSX order alone put a
 * re-shown halo on top of the rate line and the other overlay. A layer of
 * their own keeps them under every line however often they are toggled.
 * (A literal, not an import: every test that mocks `recharts` would lose it.)
 */
export const TREND_OVERLAY_HALO_Z_INDEX = 350

export const TREND_OVERLAYS_OFF: TrendOverlayState = { movingAverage: false, trendLine: false }

export const ANOMALY_FILL = CHART_VARS.status.failed
export const ANOMALY_STROKE = CHART_VARS.text

/** A downward triangle centred on (cx, cy): "below the usual", as a shape. */
export function anomalyTrianglePath(cx: number, cy: number, size = 6): string {
  return `M${cx - size} ${cy - size * 0.8}L${cx + size} ${cy - size * 0.8}L${cx} ${cy + size}Z`
}
