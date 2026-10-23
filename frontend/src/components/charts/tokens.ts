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
 *     per theme and presentation mode (memoised on the `data-theme` and
 *     `data-presentation` attributes of `<html>`, re-read when either
 *     changes) — never a `getComputedStyle` per render.
 *
 * `npm run check:theme` fails on any hex / rgb / hsl literal anywhere under
 * `components/charts/`, so a colour cannot bypass this module.
 *
 * It is also the kit's one text, grid and measure style (W29b UIX-05):
 * `CHART_FONT` (every size a chart draws; rule 6c fails a numeric font size
 * anywhere else in the kit), `RECHARTS_GRID` (solid, value axis only; rule 6d
 * fails a dashed `CartesianGrid`), `CHART_VARS.measure` (the one-measure
 * colour) and the `echarts*` text helpers (the page's font on the canvas).
 *
 * Status is never colour-only: each of the five statuses has a fixed icon, a
 * fixed decal (canvas) / pattern id (SVG) and a text label. "Flaky" is an
 * attribute drawn ON TOP of a status (`FLAKY_MARKER`), never a sixth status.
 */
import { useSyncExternalStore } from 'react'
import { VIZ_STATUSES, type VizStatus } from '@/lib/viz/contracts'
import { STATUS_ENCODING, type DecalKind } from './statusEncoding'

export const SERIES_COUNT = 8
export const SEQ_COUNT = 7
export const DIV_COUNT = 7
/** The attribute `themeStore` sets on `<html>`; part of the memo key. */
export const THEME_ATTRIBUTE = 'data-theme'
/**
 * The attribute presentation mode sets on `<html>` (`store/presentationStore`,
 * VIZ-106); the other part of the memo key. Presentation mode remaps
 * `--chart-axis` and the muted text tokens, so a canvas that kept the tokens it
 * read before the toggle would draw its axis text in the old, low-contrast
 * colour until the next theme switch. Spelt out here rather than imported so
 * the token reader stays free of the store and its React state: it only reads
 * an attribute on `<html>`, whoever sets it (`tokens.test.ts` holds the two
 * names equal). Not a bundle saving: the store is in the eager entry, and
 * `framePlotHeight` imports it for every chart anyway.
 */
export const PRESENTATION_TOKEN_ATTRIBUTE = 'data-presentation'

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
  /**
   * W29b UIX-05: the ONE colour of a mark that shows a single quantity — a
   * ranked bar that is not a change, the duration histogram, the slowest
   * tests, the executions bars, the catalogue bars, an uncategorised cluster
   * bubble. `--chart-measure` is series-2 (already every theme's executions
   * colour), so a mark that was series-2 does not move; one that borrowed a
   * diverging or another series slot now reads the same blue as the rest.
   */
  measure: cssVar('--chart-measure'),
  /** The measure taken toward the card: the fill of a one-measure AREA that carries a label (`--color-text` on it ≥ 6.9:1). */
  measureTint: cssVar('--chart-measure-tint'),
} as const

/**
 * The kit's ONE font table (W29b UIX-05). Every size a chart draws reads it —
 * Recharts ticks and axis titles, the kit's own SVG text, the canvas engines'
 * labels — and check-theme-tokens rule 6c fails a numeric font size anywhere
 * else under `components/charts/**`. These are DESK sizes: presentation mode
 * and full screen scale the drawing (`usePresentationScale`, the ECharts
 * builders' `textScale`), never these numbers. The kit's HTML text (a gauge's
 * caption, a legend row) takes the page's type scale instead (`text-2xs` 11,
 * `text-xs` 12, `text-sm` 13), which presentation mode raises on its own.
 */
export const CHART_FONT = {
  /** Axis ticks, category labels, the heatmap / scatter / treemap / sankey labels, gauge ticks. */
  tick: 11,
  /** A value or category axis's title (a Recharts `<Label>`, an ECharts axis `name`). */
  axisTitle: 11,
  /** A value printed on or beside a mark (ranked-bar values, donut slice labels, bubble labels). */
  dataLabel: 11,
  /** Release-marker labels, timeline-note labels, a lane's count. */
  annotation: 11,
  /** A legend row; a chart's own key under the plot. */
  legend: 12,
  /**
   * The tooltip box (`RECHARTS_TOOLTIP_STYLE`, `TIP_STYLE`). Named `tip`, not
   * `tooltip`: the chart guard (`scripts/chart-guard.mjs`) reads any `tooltip`
   * key in the kit as an ECharts option and refuses a value it cannot see into.
   */
  tip: 12,
  /** The donut's centre value: the one large in-chart number. */
  centre: 20,
} as const

/** A theme-following tooltip surface for Recharts' `contentStyle`. */
export const RECHARTS_TOOLTIP_STYLE = {
  backgroundColor: CHART_VARS.card,
  border: `1px solid ${CHART_VARS.border}`,
  borderRadius: '8px',
  color: CHART_VARS.text,
  fontSize: `${CHART_FONT.tip}px`,
} as const

/** A Recharts axis's tick text: the axis colour at the kit's tick size. */
export const RECHARTS_AXIS_TICK = { fill: CHART_VARS.axis, fontSize: CHART_FONT.tick } as const

/** A Recharts axis's title: spread into every `label={{ value, angle, position, ...RECHARTS_AXIS_TITLE }}`. */
export const RECHARTS_AXIS_TITLE = { fill: CHART_VARS.axis, fontSize: CHART_FONT.axisTitle } as const

/**
 * The ONE gridline (W29b UIX-05): a SOLID 1 px hairline in the grid colour, on
 * the value axis only — `<CartesianGrid {...RECHARTS_GRID} vertical={false} />`
 * (or `horizontal={false}` on a horizontal-bar layout). No `strokeDasharray`:
 * check-theme-tokens rule 6d fails a dashed grid. ECharts' twin is `echartsSplitLine`.
 */
export const RECHARTS_GRID = { stroke: CHART_VARS.grid, strokeWidth: 1 } as const

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
  /** `--chart-measure`: the one-measure colour (`CHART_VARS.measure`, resolved). */
  measure: string
  /**
   * `--font-sans`: the page's own font stack, so a canvas draws its text in the
   * font the HTML around it uses (ECharts' default is "Microsoft YaHei" on
   * Windows and `sans-serif` elsewhere).
   */
  fontSans: string
}

let cache: { root: Element; theme: string; presentation: string; tokens: ChartTokens } | null = null

/**
 * The computed chart tokens for the theme currently on `root`. Memoised per
 * theme AND presentation mode: calling it again under the same `data-theme`
 * and `data-presentation` returns the SAME object without touching
 * `getComputedStyle`.
 */
export function readChartTokens(root: Element = document.documentElement): ChartTokens {
  const theme = root.getAttribute(THEME_ATTRIBUTE) ?? ''
  const presentation = root.getAttribute(PRESENTATION_TOKEN_ATTRIBUTE) ?? ''
  if (cache && cache.root === root && cache.theme === theme && cache.presentation === presentation) {
    return cache.tokens
  }
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
    measure: read('--chart-measure'),
    fontSans: read('--font-sans'),
  }
  cache = { root, theme, presentation, tokens }
  return tokens
}

// ── The ECharts text style (W29b UIX-05) ──────────────────────────────────────

/** A desk size at `textScale` (presentation mode, full screen): never smaller than the desk size, never NaN. */
function scaledFont(px: number, textScale: number): number {
  return Number.isFinite(textScale) && textScale > 1 ? Math.round(px * textScale) : px
}

/** An ECharts text style: a font family, a size and a colour, every key spelled out. */
export interface EchartsText {
  fontFamily: string
  fontSize: number
  color: string
}

/**
 * The ECharts text style: the page's font, the kit's tick size, the axis
 * colour. Every option builder sets `textStyle: echartsBaseText(tokens)` at the
 * option ROOT — ECharts' global default for every text it draws (a label with
 * no size of its own, the tooltip box's family) — AND builds each axisLabel /
 * nameTextStyle / label / legend.textStyle / visualMap.textStyle through these
 * helpers, so a label that sets its own size keeps the family (ECharts' axis
 * and legend defaults carry their own 12 px). `textScale` is the builder's
 * presentation / full-screen scale (1 on the page).
 */
export function echartsBaseText(tokens: Pick<ChartTokens, 'fontSans' | 'axis'>, textScale = 1): EchartsText {
  return { fontFamily: tokens.fontSans, fontSize: scaledFont(CHART_FONT.tick, textScale), color: tokens.axis }
}

/** An axis's tick labels and its name (`axisLabel`, `nameTextStyle`): `echartsBaseText`'s values. */
export function echartsAxisLabel(tokens: Pick<ChartTokens, 'fontSans' | 'axis'>, textScale = 1): EchartsText {
  return { fontFamily: tokens.fontSans, fontSize: scaledFont(CHART_FONT.tick, textScale), color: tokens.axis }
}

/** A legend's text: the page's font at the kit's legend size, in the text colour. */
export function echartsLegendText(tokens: Pick<ChartTokens, 'fontSans' | 'text'>, textScale = 1): EchartsText {
  return { fontFamily: tokens.fontSans, fontSize: scaledFont(CHART_FONT.legend, textScale), color: tokens.text }
}

/** A value axis's gridline: the one solid 1 px hairline (`RECHARTS_GRID`'s twin). */
export function echartsSplitLine(tokens: Pick<ChartTokens, 'grid'>): { lineStyle: { color: string; width: 1; type: 'solid' } } {
  return { lineStyle: { color: tokens.grid, width: 1, type: 'solid' } }
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
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: [THEME_ATTRIBUTE, PRESENTATION_TOKEN_ATTRIBUTE],
  })
  return () => observer.disconnect()
}

/** The resolved tokens, re-rendering the caller when the theme or presentation mode changes. */
export function useChartTokens(): ChartTokens {
  return useSyncExternalStore(subscribe, () => readChartTokens())
}

// ── Status encodings: colour + icon + decal + label, fixed per status ─────────

// The label / icon / decal maps live in `statusEncoding.ts`, so the eager status vocabulary can read them without
// this module; re-exported here for the kit.
export { FLAKY_MARKER, STATUS_ENCODING, type DecalKind, type StatusEncoding } from './statusEncoding'

/**
 * The edge a NON-TEXT mark is drawn with where it must be told from the card
 * (WCAG 2.2 SC 1.4.11, at least 3:1): a gauge track's outline and scale
 * notches, a ring's track outline, an empty day's outline (R2 F9). The theme's
 * own quiet fills — `--color-bg-secondary`, `--chart-grid`, `--color-border` —
 * are 1.0-1.8:1 against the card in every theme, so a track or an empty cell
 * drawn only in them has no visible extent. `--chart-axis` taken 80 % toward
 * the card is 3.7-4.2:1 against the card and at least 3:1 against
 * `--color-bg-secondary` in all six themes: an edge that reads as a boundary,
 * not as a fill. `tokens.contrast.test.ts` recomputes both from `index.css`.
 */
export const NON_TEXT_EDGE = 'color-mix(in srgb, var(--chart-axis) 80%, var(--color-bg-card))'

/**
 * The ONE order statuses stack in (bottom of a stack first) and list in a legend
 * (first entry first), in every chart of the kit: Passed, Failed, Broken,
 * Skipped, then Unknown.
 *
 * It is the contract's own vocabulary order (`VIZ_STATUSES`, `status_vocab`),
 * which the status bars (`BarChart.model`), the donut and the heatmap legend
 * already followed; only the stacked charts had their own lists, and three
 * pages drew the same four statuses in two orders (R2 F3/F4, R1 F9). Passed
 * sits on the baseline, where the largest share is read against zero; the two
 * failure kinds sit together directly on it, where the eye compares them; the
 * statuses that did not run come last. A page passes statuses in any order and
 * the kit applies this one (`buildStackedColumnModel`, `TrendChart`).
 */
export const STATUS_STACK_ORDER: readonly VizStatus[] = VIZ_STATUSES

/** A status's place in `STATUS_STACK_ORDER`: the sort key every stacked chart uses. */
export function statusStackRank(status: VizStatus): number {
  return STATUS_STACK_ORDER.indexOf(status)
}

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
