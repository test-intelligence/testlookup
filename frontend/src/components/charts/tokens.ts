/**
 * Chart design tokens (VIZ-102) — the ONE place a chart gets a colour from.
 *
 * The values live in `src/index.css`, per theme (`--chart-series-*`,
 * `--chart-seq-*`, `--chart-div-*`, `--chart-grid`, `--chart-axis` and the
 * five `--status-*` hues). This module hands them to the two engines:
 *
 *   - Recharts draws SVG, and SVG presentation attributes resolve `var()`, so
 *     it takes `CHART_VARS` — `var(--…)` strings that follow a theme switch with
 *     no re-render at all.
 *   - ECharts draws on a canvas, which cannot resolve `var()`. It takes
 *     `readChartTokens()` / `useChartTokens()`: the computed values, read ONCE
 *     per theme (memoised on the `data-theme` attribute of `<html>`, re-read
 *     when that attribute changes) — never a `getComputedStyle` per render.
 *
 * `npm run check:theme` fails on any hex / rgb / hsl literal anywhere under
 * `components/charts/`, so a colour cannot bypass this module.
 *
 * Status is never colour-only: each of the five statuses has a fixed icon, a
 * fixed decal (canvas) / pattern id (SVG) and a text label. "Flaky" is an
 * attribute drawn ON TOP of a status (`FLAKY_MARKER`), never a sixth status.
 */
import { useSyncExternalStore } from 'react'
import { VIZ_STATUSES, type VizStatus } from '@/lib/viz/contracts'

export const SERIES_COUNT = 8
export const SEQ_COUNT = 7
export const DIV_COUNT = 7
/** The attribute `themeStore` sets on `<html>`; the memo key. */
export const THEME_ATTRIBUTE = 'data-theme'

const range = (n: number) => Array.from({ length: n }, (_, i) => i + 1)

export const SERIES_VARS = range(SERIES_COUNT).map((i) => `--chart-series-${i}`)
export const SEQ_VARS = range(SEQ_COUNT).map((i) => `--chart-seq-${i}`)
export const DIV_VARS = range(DIV_COUNT).map((i) => `--chart-div-${i}`)
export const STATUS_VARS: Record<VizStatus, string> = {
  passed: '--status-passed',
  failed: '--status-failed',
  broken: '--status-broken',
  skipped: '--status-skipped',
  unknown: '--status-unknown',
}

const cssVar = (name: string) => `var(${name})`

/** `var()` references, for SVG (Recharts). They follow the theme on their own. */
export const CHART_VARS = {
  series: SERIES_VARS.map(cssVar),
  seq: SEQ_VARS.map(cssVar),
  div: DIV_VARS.map(cssVar),
  status: Object.fromEntries(VIZ_STATUSES.map((s) => [s, cssVar(STATUS_VARS[s])])) as Record<
    VizStatus,
    string
  >,
  flaky: cssVar('--status-flaky'),
  grid: cssVar('--chart-grid'),
  axis: cssVar('--chart-axis'),
  accent: cssVar('--color-accent'),
  neutral: cssVar('--color-text-muted'),
  card: cssVar('--color-bg-card'),
  border: cssVar('--color-border-light'),
  text: cssVar('--color-text'),
} as const

/** A theme-following tooltip surface for Recharts' `contentStyle`. */
export const RECHARTS_TOOLTIP_STYLE = {
  backgroundColor: CHART_VARS.card,
  border: `1px solid ${CHART_VARS.border}`,
  borderRadius: '8px',
  color: CHART_VARS.text,
  fontSize: '12px',
} as const

export const RECHARTS_AXIS_TICK = { fill: CHART_VARS.axis, fontSize: 11 } as const

/** Resolved values, for canvas (ECharts). */
export interface ChartTokens {
  /** The `data-theme` value these were read under (`''` when none is set). */
  theme: string
  series: string[]
  seq: string[]
  div: string[]
  status: Record<VizStatus, string>
  flaky: string
  grid: string
  axis: string
  card: string
  border: string
  text: string
  textMuted: string
}

let cache: { root: Element; theme: string; tokens: ChartTokens } | null = null

/**
 * The computed chart tokens for the theme currently on `root`. Memoised per
 * theme: calling it again under the same `data-theme` returns the SAME object
 * without touching `getComputedStyle`.
 */
export function readChartTokens(root: Element = document.documentElement): ChartTokens {
  const theme = root.getAttribute(THEME_ATTRIBUTE) ?? ''
  if (cache && cache.root === root && cache.theme === theme) return cache.tokens
  const style = getComputedStyle(root)
  const read = (name: string) => style.getPropertyValue(name).trim()
  const tokens: ChartTokens = {
    theme,
    series: SERIES_VARS.map(read),
    seq: SEQ_VARS.map(read),
    div: DIV_VARS.map(read),
    status: Object.fromEntries(VIZ_STATUSES.map((s) => [s, read(STATUS_VARS[s])])) as Record<
      VizStatus,
      string
    >,
    flaky: read('--status-flaky'),
    grid: read('--chart-grid'),
    axis: read('--chart-axis'),
    card: read('--color-bg-card'),
    border: read('--color-border-light'),
    text: read('--color-text'),
    textMuted: read('--color-text-muted'),
  }
  cache = { root, theme, tokens }
  return tokens
}

/** Test seam: forget the memo (the next read hits `getComputedStyle`). */
export function resetChartTokenCache(): void {
  cache = null
}

/**
 * Series colour `index` (0-based). Past the eighth series the answer is the
 * "Other" colour, never a recycled hue — callers show top 7 + Other.
 */
export function seriesColor(tokens: Pick<ChartTokens, 'series'>, index: number): string {
  return tokens.series[Math.min(Math.max(index, 0), SERIES_COUNT - 1)]
}

function subscribe(onChange: () => void): () => void {
  const observer = new MutationObserver(onChange)
  observer.observe(document.documentElement, { attributes: true, attributeFilter: [THEME_ATTRIBUTE] })
  return () => observer.disconnect()
}

/** The resolved tokens, re-rendering the caller when the theme changes. */
export function useChartTokens(): ChartTokens {
  return useSyncExternalStore(subscribe, () => readChartTokens())
}

// ── Status encodings: colour + icon + decal + label, fixed per status ─────────

export type DecalKind = 'solid' | 'diagonal' | 'crosshatch' | 'dashes' | 'dots'

export interface StatusEncoding {
  label: string
  /** lucide-react icon name. */
  icon: string
  /**
   * `<pattern id>` suffix for SVG fills (Recharts). Each chart prefixes it with
   * its own id (`patterns.tsx`), so two charts on a page never share a pattern.
   */
  patternId: string
  /** Decal id; `echartsDecal()` turns it into an ECharts decal object, `patterns.tsx` into an SVG pattern. */
  decal: DecalKind
  /** `stroke-dasharray` for a line drawn in this status; `undefined` = solid. */
  dash: string | undefined
}

export const STATUS_ENCODING: Record<VizStatus, StatusEncoding> = {
  passed: { label: 'Passed', icon: 'CircleCheck', patternId: 'chart-pattern-passed', decal: 'solid', dash: undefined },
  failed: { label: 'Failed', icon: 'CircleX', patternId: 'chart-pattern-failed', decal: 'diagonal', dash: '7 3' },
  broken: { label: 'Broken', icon: 'CircleAlert', patternId: 'chart-pattern-broken', decal: 'crosshatch', dash: '9 3 2 3' },
  skipped: { label: 'Skipped', icon: 'CircleSlash', patternId: 'chart-pattern-skipped', decal: 'dashes', dash: '2 3' },
  unknown: { label: 'Unknown', icon: 'CircleHelp', patternId: 'chart-pattern-unknown', decal: 'dots', dash: '1 4' },
}

/** Flaky is an attribute marker over a status, not a status. */
export const FLAKY_MARKER = {
  label: 'Flaky',
  icon: 'Shuffle',
  patternId: 'chart-pattern-flaky-marker',
  token: '--status-flaky',
} as const

/** The subset of ECharts' `DecalObject` this kit uses (kept structural: no echarts import here). */
export interface ChartDecal {
  symbol: 'rect' | 'circle'
  symbolSize: number
  color: string
  rotation: number
  dashArrayX: number | number[] | number[][]
  dashArrayY: number | number[]
}

/**
 * The ECharts decal for a status, drawn in the card colour so it reads as a
 * cut-out pattern on any status fill. `null` for the solid (passed) encoding.
 */
export function echartsDecal(status: VizStatus, tokens: Pick<ChartTokens, 'card'>): ChartDecal | null {
  return decalOf(STATUS_ENCODING[status].decal, tokens.card)
}

/** The SVG patterns' tile (`patterns.tsx`); every decal repeats on the same 8 px. */
export const DECAL_TILE = 8

/**
 * The ECharts decal for a decal kind, drawn in `color`. `null` for solid.
 *
 * Each one must DRAW the shape its SVG pattern draws (`patterns.tsx`), which
 * is not the same as having the same name. An ECharts decal is a grid of dash
 * ROWS: `dashArrayY` alternates a drawn row's height with a gap, and each
 * drawn row takes the next entry of `dashArrayX` as its own on/off dashes
 * (values are rounded UP to whole px). So:
 *
 *   - a line is a row whose dash has no gap (`[8, 0]`);
 *   - a crosshatch is two drawn rows per tile — a full line, then 1-in-8
 *     ticks that stack into the crossing line — rotated. Two rows of dashes
 *     (the old `[[1, 5], [5, 1]]`) are just dashes: on the heatmap Broken was
 *     drawn exactly like Skipped, and the hues alone are 1.07-1.38:1 apart;
 *   - `rotation` turns the tile clockwise on screen (as SVG `rotate()` does),
 *     so the SVG's vertical strip at +45 deg ('/') is a horizontal row at
 *     -45 deg here. The old +45 deg drew the mirror image (a backslash) under a '/' legend swatch.
 *
 * `statusDecalAgreement.test.tsx` tiles each of these with ECharts' own decal
 * code and compares the drawn shape with the legend's `<pattern>`.
 */
export function decalOf(kind: DecalKind, color: string): ChartDecal | null {
  const t = DECAL_TILE
  switch (kind) {
    case 'solid':
      return null
    case 'diagonal':
      // '/' stripes, 2 px of every 8.
      return { symbol: 'rect', symbolSize: 1, color, rotation: -Math.PI / 4, dashArrayX: [t, 0], dashArrayY: [2, t - 2] }
    case 'crosshatch':
      // 'X': a 2 px grid of 8 px squares, turned 45 deg.
      return {
        symbol: 'rect',
        symbolSize: 1,
        color,
        rotation: Math.PI / 4,
        dashArrayX: [
          [t, 0],
          [2, t - 2],
        ],
        dashArrayY: [2, 0, t - 2, 0],
      }
    case 'dashes':
      // Horizontal 4 x 2 dashes, one per 8 px tile.
      return { symbol: 'rect', symbolSize: 1, color, rotation: 0, dashArrayX: [4, t - 4], dashArrayY: [2, t - 2] }
    case 'dots':
      // 3 px dots, one per 8 px tile (the SVG's r = 1.5).
      return { symbol: 'circle', symbolSize: 1, color, rotation: 0, dashArrayX: [3, t - 3], dashArrayY: [3, t - 3] }
  }
}
