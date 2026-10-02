/**
 * `StackedColumnChart` (VIZ-104, Wave 2.5 K1) — one column per bucket (a day, a
 * month), stacked by series: executions by status per day, hours saved per
 * model leg per month.
 *
 * Everything it draws comes from `stackedColumnModel` (pure, tested on its
 * own); this file is only the drawing. What it is careful about:
 *
 *   - A status series is its status's colour AND decal, from `STATUS_ENCODING`
 *     through `patterns.tsx`; any other series is a series colour plus a
 *     category decal. Two series never differ by hue alone, and the legend's
 *     swatch is drawn with the same fill as the column.
 *   - `null` is not measured: a gap, stated under the plot, "—" in the tooltip
 *     and the table — never a zero. A MEASURED zero is marked on the baseline
 *     with a short tick, so an empty-but-present column is not read as a gap.
 *   - The value axis starts at 0 and ends on a nice tick (`niceScale`): a
 *     column encodes value as height.
 *   - The bucket labels are MEASURED (`textMeasure.ts`) in the font the
 *     reader's browser draws them in. They lie flat when they fit their
 *     column, and slanted with an axis as tall as the longest slanted label
 *     needs when they do not. A reserve sized for Segoe UI cut labels on the
 *     Linux CI runner's DejaVu Sans in Wave 2.4; this one is sized from the
 *     label. A TIME axis may show every Nth day, counted back from the newest
 *     so today is always named; a CATEGORY axis never drops a name — it
 *     slants, then cuts names in the middle, then turns into horizontal bars
 *     (`columnAxisLayout`).
 *   - Labels are ingested text (suite names, run names): they reach the DOM
 *     only as React text, middle-truncated on the axis and whole in the
 *     tooltip and the table.
 *   - The plot sits inside a named, focusable `role="group"` with a keyboard
 *     cursor (`useChartCursor`) that reads each column in the same words as
 *     the pointer's tooltip; Recharts' own `accessibilityLayer` is off.
 */
import { useCallback, useMemo } from 'react'
import { Bar, BarChart, CartesianGrid, Legend, ReferenceDot, Tooltip, XAxis, YAxis, useXAxisScale } from 'recharts'
import { cursorPoint, useChartCursor, type ChartCursorPoint } from './ChartCursor'
import { PinnedTip, sweepOf, useColumnMark } from './ChartTooltip'
import ChartResponsive from './ChartResponsive'
import { middleTruncate } from './labelTruncate'
import { useContainerWidth } from './chartLayout'
import { useFramePlotLayoutHeight, usePresentationScale } from './framePlotHeight'
import { useChartAnimation } from './motion'
import {
  ChartLegend,
  patternFill,
  renderPatterns,
  statusPatternId,
  useChartPatternPrefix,
  type LegendEntry,
  type PatternSpec,
} from './patterns'
import {
  gapNote,
  invalidNote,
  stackedColumnTipContent,
  type StackedColumnModel,
  type StackedColumnSeries,
} from './stackedColumnModel'
import { useTextMeasure, type TextMeasure } from './textMeasure'
import { COLUMN_SIDES, type TipRect } from './tipPlacement'
import { CHART_VARS, RECHARTS_AXIS_TICK } from './tokens'

export interface StackedColumnChartProps {
  model: StackedColumnModel
  /** Names the focusable drawing surface (see `useChartCursor`); the frame passes its title. */
  title?: string
  height?: number
  /** Forwarded to Recharts' `isAnimationActive`; off under `prefers-reduced-motion`. */
  animate?: boolean
  /** What one column is called in the gap note and the keyboard hint ("day", "month"). Default "column". */
  bucketNoun?: string
}

const NOTE = 'text-xs text-[var(--color-text-secondary)]'
/** What the notes under the plot keep back in full screen, px. */
const STACKED_NOTES_RESERVE = 48
/** The widest a column is drawn, px: a window of three days is not three slabs. */
export const COLUMN_MAX_WIDTH = 32
/** The axis labels' size, px (`RECHARTS_AXIS_TICK`). */
const LABEL_SIZE = RECHARTS_AXIS_TICK.fontSize
/** The longest a bucket label is drawn on the axis, in characters; the tooltip and the table carry it whole. */
export const AXIS_LABEL_MAX_CHARS = 24
/** Space kept between two flat labels, px. */
const FLAT_LABEL_GAP = 8
/** Recharts' own x axis height: a flat label's line box fits it in any font (8 px anchor + about 1 em). */
export const FLAT_AXIS_HEIGHT = 30
/** The labels' slant when they do not lie flat, degrees. */
export const SLANT_ANGLE = 30
/** Where Recharts anchors a bottom tick label: `tickSize` (6) + `tickMargin` (2) below the axis. */
const TICK_ANCHOR = 8
/** From the anchor to the bottom of a label's line, in em (`DurationHistogram`'s measure): cap height plus the deepest descent. */
const TICK_LINE_DEPTH_EM = 0.71 + 0.3
/** A line of axis text, in em, for spacing slanted labels apart. */
const LINE_EM = 1.25
/** Flat labels thinned to every Nth at most this far before they slant instead. */
const MAX_FLAT_STRIDE = 3
/** The value axis's width where nothing can be measured (Recharts' own default). */
export const VALUE_AXIS_MIN_WIDTH = 60
/** The value axis keeps this beside its longest tick label: the 8 px tick anchor and the rotated title's line (offset 14). */
const VALUE_AXIS_TITLE_ROOM = 32
const PLOT_MARGIN = { top: 16, right: 8, left: 0, bottom: 0 } as const
/** The bar form's margins: room on the right for the last value tick's label, and under the value axis's title. */
const BARS_MARGIN = { top: 8, right: 16, left: 4, bottom: 8 } as const
/** The bar form's value axis, px: its ticks and its title under them (`BarChart.tsx`'s). */
const BARS_VALUE_AXIS_HEIGHT = 32
/**
 * Where nothing is laid out (jsdom, a tab never shown) text cannot be
 * measured: 0.62 em a character is wider than the average glyph of Segoe UI,
 * San Francisco and DejaVu Sans alike, so an estimate errs toward room.
 */
const ESTIMATE_EM_PER_CHAR = 0.62

export const estimateTextWidth: TextMeasure = (text, fontSize) => [...text].length * fontSize * ESTIMATE_EM_PER_CHAR

/** The steeper slant a category axis tries when its columns are too narrow for `SLANT_ANGLE`, degrees. */
export const STEEP_SLANT_ANGLE = 45
/**
 * More categories than this and the chart draws horizontal bars instead of
 * columns (R2's accepted design call): past about a dozen, even slanted names
 * crowd, and a category axis never drops a name to make room.
 */
export const MAX_CATEGORY_COLUMNS = 12
/** The shortest a category name is cut to on a slanted axis, in characters, before the chart turns to bars. */
export const MIN_CATEGORY_LABEL_CHARS = 10
/** The tallest a slanted category axis may grow, as a share of the chart's height, before names are cut. */
export const SLANT_AXIS_MAX_SHARE = 0.4
/** Horizontal bars: the height one category's row takes, px (a label's line plus the gap between bars). */
export const BAR_ROW_HEIGHT = 24
/** Horizontal bars: the thickest a bar is drawn, px. */
const BAR_MAX_THICKNESS = 16
/** Horizontal bars: what the value axis, its title and the legend take under the rows, px. */
const BARS_CHROME = 72
/** Horizontal bars: the widest the category axis may be, as a share of the chart's width. */
const BAR_LABEL_MAX_SHARE = 0.4
/** Horizontal bars: kept between a category name and its bar, px. */
const BAR_LABEL_GAP = 8

export interface ColumnAxisLayout {
  /**
   * `columns` (the bucket axis under the plot) or `bars`: a category chart of
   * more than `MAX_CATEGORY_COLUMNS` buckets, or of names too long for any
   * slant, drawn as horizontal bars with the names beside them.
   */
  orientation: 'columns' | 'bars'
  /** 0 (flat), `-SLANT_ANGLE` or `-STEEP_SLANT_ANGLE`. */
  angle: number
  /** The x axis's height, px. */
  height: number
  /**
   * Labels drawn are every `interval + 1`th, counted BACK from the last (the
   * newest) bucket, so the right end — where a reader looks for "now" — is
   * always labelled (R2 G2, R1 F16). Always 0 on a category axis: an
   * unlabelled column there has no identity, so a category axis never thins.
   */
  interval: number
  /** The longest drawn label, px. */
  longest: number
  /** The longest a label is drawn, in characters: cut in the middle past it (`middleTruncate`). */
  maxChars: number
}

export interface ColumnAxisOptions {
  /** `time` buckets may thin their labels; `category` buckets never do. Default `category`, as the model's. */
  xType?: 'time' | 'category'
  /** The tallest a slanted category axis may grow, px. Default `SLANT_AXIS_MAX_SHARE` of a 280 px chart. */
  maxHeight?: number
}

/** Whether the label of bucket `index` of `count` is drawn under a layout thinned to `interval`. */
export function drawsLabel(index: number, count: number, interval: number): boolean {
  return (count - 1 - index) % (interval + 1) === 0
}

const radians = (degrees: number) => (degrees * Math.PI) / 180
/** How tall an axis a label `length` px long needs, slanted by `angle` radians. */
const slantedHeight = (length: number, angle: number) =>
  TICK_ANCHOR + length * Math.sin(angle) + TICK_LINE_DEPTH_EM * LABEL_SIZE * Math.cos(angle)
const longestOf = (labels: readonly string[], chars: number, measure: TextMeasure) => {
  let longest = 0
  for (const label of labels) longest = Math.max(longest, measure(middleTruncate(label, chars), LABEL_SIZE))
  return longest
}

/**
 * How the bucket labels are laid out under columns `band` px apart.
 *
 * A TIME axis: flat when the longest label fits its column; flat and thinned
 * to every Nth label while N stays small (a month of days reads "Sep 1, Sep 4,
 * …"); else slanted by `SLANT_ANGLE`, thinned only so far that two slanted
 * labels do not touch, with an axis as tall as the longest slanted label
 * reaches. Thinning counts back from the newest bucket, so it is always named.
 *
 * A CATEGORY axis never thins (R2's accepted design call): an unlabelled
 * column there has no identity, and hovering every column is not a glance.
 * Flat when every name fits; else slanted (`SLANT_ANGLE`, then the steeper
 * `STEEP_SLANT_ANGLE` when the columns are too narrow for two slanted names
 * not to touch), cut in the middle to what an axis of at most `maxHeight`
 * holds, down to `MIN_CATEGORY_LABEL_CHARS`; the full name stays in the
 * tooltip, the table and the keyboard cursor. When even that fails, or there
 * are more than `MAX_CATEGORY_COLUMNS` buckets, the chart draws horizontal
 * bars. `band` 0 means no width is known yet: flat, all labels.
 */
export function columnAxisLayout(
  labels: readonly string[],
  band: number,
  measure: TextMeasure,
  { xType = 'category', maxHeight = SLANT_AXIS_MAX_SHARE * 280 }: ColumnAxisOptions = {},
): ColumnAxisLayout {
  const longest = longestOf(labels, AXIS_LABEL_MAX_CHARS, measure)
  const flat = { orientation: 'columns', angle: 0, height: FLAT_AXIS_HEIGHT, interval: 0, longest, maxChars: AXIS_LABEL_MAX_CHARS } as const
  const bars = { ...flat, orientation: 'bars' } as const
  if (xType !== 'time' && labels.length > MAX_CATEGORY_COLUMNS) return bars
  if (!(band > 0) || labels.length === 0) return flat
  const line = LINE_EM * LABEL_SIZE

  if (xType === 'time') {
    const stride = Math.max(1, Math.ceil((longest + FLAT_LABEL_GAP) / band))
    if (stride <= MAX_FLAT_STRIDE) return { ...flat, interval: stride - 1 }
    const angle = radians(SLANT_ANGLE)
    // Slanted labels are parallel lines `band · sin(angle)` apart: they need a line's height between them.
    const slantStride = Math.max(1, Math.ceil(line / (band * Math.sin(angle))))
    const height = Math.max(FLAT_AXIS_HEIGHT, Math.ceil(slantedHeight(longest, angle)) + 1)
    return { ...flat, angle: -SLANT_ANGLE, height, interval: slantStride - 1 }
  }

  if (longest + FLAT_LABEL_GAP <= band) return flat
  for (const degrees of [SLANT_ANGLE, STEEP_SLANT_ANGLE]) {
    const angle = radians(degrees)
    // Two slanted names closer than a line apart overlap: try the steeper slant.
    if (band * Math.sin(angle) < line) continue
    for (let chars = AXIS_LABEL_MAX_CHARS; chars >= MIN_CATEGORY_LABEL_CHARS; chars--) {
      const length = chars === AXIS_LABEL_MAX_CHARS ? longest : longestOf(labels, chars, measure)
      const needed = slantedHeight(length, angle)
      if (needed <= maxHeight) {
        return { orientation: 'columns', angle: -degrees, height: Math.max(FLAT_AXIS_HEIGHT, Math.ceil(needed) + 1), interval: 0, longest: length, maxChars: chars }
      }
    }
    // Too long even cut short: a steeper slant only needs a taller axis, so it cannot help.
    break
  }
  return bars
}

export interface ColumnRightMarginInput {
  /** The newest label's MEASURED width, px (the one a thinned axis always draws). */
  lastLabel: number
  /** The plot's width at the `base` right margin, px. */
  plotWidth: number
  /** Columns. */
  count: number
  /** The margin the chart has anyway, px. */
  base: number
}

/**
 * The plot's right margin under a FLAT column axis (Wave 2.6, B0 finding 5).
 *
 * A flat label is centred on its column, so once labels are thinned (wider
 * than a column) the newest one — always drawn — reaches past the plot's right
 * edge by half its width less half a column. A fixed margin sized for one font
 * cuts it in a wider one: "Sep 18" lost its last glyph in DejaVu Sans on the
 * CI runner at 640 and 768 px. So the margin is sized from the MEASURED label,
 * and only grows where it has to (every chart whose label already fits keeps
 * `base`, pixel for pixel). A wider margin narrows every column, which moves
 * the last centre right, so the margin is solved for that, not added once:
 *
 *   m >= w/2 - band(m)/2 + 1, band(m) = (plotWidth + base - m) / count
 *
 * (1 px for the glyph's antialiased edge).
 */
export function columnRightMargin({ lastLabel, plotWidth, count, base }: ColumnRightMarginInput): number {
  if (!(count > 0) || !(lastLabel > 0)) return base
  const share = 1 / (2 * count)
  const needed = (lastLabel / 2 - (plotWidth + base) * share + 1) / (1 - share)
  return Math.max(base, Math.ceil(needed))
}

export interface BarAxisLayout {
  /** The category axis's width, px. */
  width: number
  /** The longest a category name is drawn beside its bar, in characters. */
  maxChars: number
}

/**
 * The category axis of the horizontal-bar form: as wide as the longest name
 * needs, at most `BAR_LABEL_MAX_SHARE` of the chart; names past it are cut in
 * the middle (never under `MIN_CATEGORY_LABEL_CHARS` characters), whole in the
 * tooltip, the table and the cursor. `width` 0 (no layout): the widest cut.
 */
export function barAxisLayout(labels: readonly string[], width: number, measure: TextMeasure): BarAxisLayout {
  const room = width > 0 ? width * BAR_LABEL_MAX_SHARE - BAR_LABEL_GAP : Infinity
  for (let chars = AXIS_LABEL_MAX_CHARS; chars > MIN_CATEGORY_LABEL_CHARS; chars--) {
    const longest = longestOf(labels, chars, measure)
    if (longest <= room) return { width: Math.ceil(longest + BAR_LABEL_GAP), maxChars: chars }
  }
  return { width: Math.ceil(longestOf(labels, MIN_CATEGORY_LABEL_CHARS, measure) + BAR_LABEL_GAP), maxChars: MIN_CATEGORY_LABEL_CHARS }
}

/** The value axis's width: its longest tick label plus the room for the rotated title. */
export function valueAxisWidth(ticks: readonly string[], measure: TextMeasure | null): number {
  if (!measure) return VALUE_AXIS_MIN_WIDTH
  let longest = 0
  for (const tick of ticks) longest = Math.max(longest, measure(tick, LABEL_SIZE))
  return Math.max(VALUE_AXIS_MIN_WIDTH, Math.ceil(longest + VALUE_AXIS_TITLE_ROOM))
}

/** The pattern each series is filled with, under this chart's own id prefix. */
function seriesPatternId(prefix: string, entry: StackedColumnSeries, index: number): string {
  return entry.status ? statusPatternId(prefix, entry.status) : `${prefix}-chart-pattern-series-${index}`
}

/** The short tick a MEASURED zero column gets on the baseline: present, and empty. Across the bar's row in the bar form. */
function ZeroTick({ cx, cy, bucket, across = false }: { cx?: number; cy?: number; bucket: string; across?: boolean }) {
  if (typeof cx !== 'number' || typeof cy !== 'number') return null
  return (
    <line
      data-stacked-zero={bucket}
      x1={across ? cx : cx - 6}
      x2={across ? cx : cx + 6}
      y1={across ? cy - 6 : cy}
      y2={across ? cy + 6 : cy}
      stroke={CHART_VARS.axis}
      strokeWidth={2}
      strokeLinecap="round"
    />
  )
}

/**
 * The drawn extent of one horizontal bar, in chart coordinates: from the value
 * axis's zero to the end of the stack, over the bar's thickness. The tooltip is
 * placed beside THIS, so it never covers the bar it describes (`barMark` in
 * `BarChart.tsx`, which this chart does not import: it would pull that chart in).
 */
export function stackRowMark(
  total: number,
  thickness: number,
  centre: number | undefined,
  xScale: ((value: number) => number | undefined) | undefined,
): TipRect | null {
  if (centre === undefined || !xScale) return null
  const xs = [0, total].map((value) => xScale(value)).filter((x): x is number => typeof x === 'number' && Number.isFinite(x))
  if (xs.length === 0) return null
  const left = Math.min(...xs)
  return { left, top: centre - thickness / 2, width: Math.max(...xs) - left, height: thickness }
}

interface StackedColumnTooltipProps {
  active?: boolean
  label?: string | number
  coordinate?: { x?: number; y?: number }
  model: StackedColumnModel
}

/** Recharts' tooltip: the shared content model, pinned beside the column (`PinnedTip`). */
export function StackedColumnTooltip({ active, label, coordinate, model }: StackedColumnTooltipProps) {
  const index = model.buckets.findIndex((bucket) => bucket.key === String(label))
  const { mark, gap, chartBox, sweep } = useColumnMark(coordinate, model.buckets.length, COLUMN_MAX_WIDTH / 2)
  const content = active && index >= 0 ? stackedColumnTipContent(model, index) : null
  return <PinnedTip content={content} mark={mark} sides={COLUMN_SIDES} align="start" gap={gap} chartBox={chartBox} sweep={sweep} />
}

/** The same tooltip for the bar form: pinned beside the hovered row's bar. */
export function StackedBarTooltip({ active, label, coordinate, model }: StackedColumnTooltipProps) {
  const index = model.buckets.findIndex((bucket) => bucket.key === String(label))
  const xScale = useXAxisScale()
  const bucket = active && index >= 0 ? model.buckets[index] : undefined
  const mark = bucket
    ? stackRowMark(bucket.total ?? 0, BAR_MAX_THICKNESS, coordinate?.y, xScale ? (value: number) => xScale(value) : undefined)
    : null
  return <PinnedTip content={bucket ? stackedColumnTipContent(model, index) : null} mark={mark} sweep={sweepOf(coordinate, 'y')} />
}

type Row = Record<string, string | number | null>

export default function StackedColumnChart({
  model,
  title = 'Stacked columns',
  height: requestedHeight = 280,
  animate: requested,
  bucketNoun = 'column',
}: StackedColumnChartProps) {
  // Full screen (VIZ-608): laid out at page text size, then scaled (`ChartResponsive`).
  const scale = usePresentationScale()
  const height = useFramePlotLayoutHeight(requestedHeight, STACKED_NOTES_RESERVE)
  const animate = useChartAnimation(requested)
  const prefix = useChartPatternPrefix()
  const [widthRef, width] = useContainerWidth<HTMLDivElement>()
  const [measureRef, measure] = useTextMeasure<HTMLDivElement>()
  const plotRef = useCallback(
    (node: HTMLDivElement | null) => {
      widthRef(node)
      measureRef(node)
    },
    [widthRef, measureRef],
  )

  const rows = useMemo<Row[]>(
    () =>
      model.buckets.map((bucket) => {
        const row: Row = { key: bucket.key }
        model.series.forEach((entry, s) => {
          // `null` stays `null`: Recharts stacks it as nothing and draws no segment.
          row[entry.field] = bucket.values[s]
        })
        return row
      }),
    [model],
  )
  const labels = useMemo(() => model.buckets.map((bucket) => bucket.label), [model])
  const textMeasure = measure ?? estimateTextWidth

  const tickLabels = useMemo(() => model.axis.ticks.map((tick) => model.format(tick)), [model])
  const yWidth = valueAxisWidth(tickLabels, measure)
  // The band a column has, in LAYOUT px (full screen scales the drawing, not the layout).
  const plotWidth = width / scale - yWidth - PLOT_MARGIN.left - PLOT_MARGIN.right
  const count = model.buckets.length
  const band = width > 0 && count > 0 ? plotWidth / count : 0
  const axisOptions = useMemo(() => ({ xType: model.xType, maxHeight: SLANT_AXIS_MAX_SHARE * height }), [model.xType, height])
  const firstAxis = useMemo(() => columnAxisLayout(labels, band, textMeasure, axisOptions), [labels, band, textMeasure, axisOptions])
  // A flat axis centres the newest label on the last column: give it the room
  // it measures (B0 finding 5), then lay the axis out again in the narrower
  // columns that leaves. A slanted label ends at its column and needs none.
  const plotRight =
    firstAxis.orientation === 'columns' && firstAxis.angle === 0 && band > 0
      ? columnRightMargin({
          lastLabel: textMeasure(middleTruncate(labels[count - 1] ?? '', firstAxis.maxChars), LABEL_SIZE),
          plotWidth,
          count,
          base: PLOT_MARGIN.right,
        })
      : PLOT_MARGIN.right
  const axis = useMemo(
    () =>
      plotRight === PLOT_MARGIN.right
        ? firstAxis
        : columnAxisLayout(labels, (plotWidth + PLOT_MARGIN.right - plotRight) / count, textMeasure, axisOptions),
    [plotRight, firstAxis, labels, plotWidth, count, textMeasure, axisOptions],
  )
  const plotMargin = useMemo(() => ({ ...PLOT_MARGIN, right: plotRight }), [plotRight])
  const asBars = axis.orientation === 'bars'
  const barAxis = useMemo(
    () => (asBars ? barAxisLayout(labels, width / scale, textMeasure) : null),
    [asBars, labels, width, scale, textMeasure],
  )
  const maxChars = barAxis?.maxChars ?? axis.maxChars
  const shortLabels = useMemo(
    () => new Map(model.buckets.map((bucket) => [bucket.key, middleTruncate(bucket.label, maxChars)])),
    [model, maxChars],
  )
  // The labels drawn under a thinned time axis: counted back from the newest, so it is always one of them.
  const labelled = useMemo(
    () => new Set(model.buckets.filter((_, i) => drawsLabel(i, model.buckets.length, axis.interval)).map((bucket) => bucket.key)),
    [model, axis.interval],
  )
  const axisLabel = (key: string) => (labelled.has(String(key)) ? (shortLabels.get(String(key)) ?? String(key)) : '')
  // The bar form grows with its rows instead of squeezing them into the card's height.
  const plotHeight = asBars ? Math.max(height, model.buckets.length * BAR_ROW_HEIGHT + BARS_CHROME) : height

  const patterns = useMemo<PatternSpec[]>(
    () => model.series.map((entry, s) => ({ id: seriesPatternId(prefix, entry, s), color: entry.color, decal: entry.decal })),
    [model, prefix],
  )
  const legend: LegendEntry[] = model.series.map((entry, s) => ({
    key: entry.key,
    label: entry.label,
    status: entry.status ?? undefined,
    fill: patternFill(seriesPatternId(prefix, entry, s)),
  }))

  // One stop per COLUMN, reading every segment, in the pointer tooltip's own words (VIZ-601).
  const cursorPoints = useMemo<ChartCursorPoint[]>(
    () => model.buckets.map((bucket, index) => cursorPoint(bucket.key, stackedColumnTipContent(model, index))),
    [model],
  )
  const cursor = useChartCursor({ title, chartType: 'stacked column chart', points: cursorPoints, noun: bucketNoun })

  const gaps = gapNote(model, bucketNoun)
  const invalid = invalidNote(model)
  const zeros = model.buckets.filter((bucket) => bucket.zero)

  const bars = model.series.map((entry, s) => (
    <Bar
      key={entry.key}
      dataKey={entry.field}
      name={entry.label}
      stackId="stack"
      fill={patternFill(seriesPatternId(prefix, entry, s))}
      maxBarSize={asBars ? BAR_MAX_THICKNESS : COLUMN_MAX_WIDTH}
      isAnimationActive={animate}
    />
  ))
  const zeroTicks = zeros.map((bucket) => (
    <ReferenceDot
      key={`zero-${bucket.key}`}
      x={asBars ? 0 : bucket.key}
      y={asBars ? bucket.key : 0}
      r={0}
      ifOverflow="visible"
      shape={(props: { cx?: number; cy?: number }) => <ZeroTick cx={props.cx} cy={props.cy} bucket={bucket.key} across={asBars} />}
    />
  ))
  const tooltip = (
    // Pinned beside its column or bar (`PinnedTip`): a fixed origin, no slide.
    <Tooltip
      key={cursor.tipKey}
      cursor={false}
      content={asBars ? <StackedBarTooltip model={model} /> : <StackedColumnTooltip model={model} />}
      position={{ x: 0, y: 0 }}
      isAnimationActive={false}
      {...cursor.tipProps}
    />
  )
  const legendNode = <Legend content={() => <ChartLegend entries={legend} />} />

  return (
    <figure
      data-chart="stacked-column"
      data-stacked-series={model.series.map((entry) => entry.key).join(',')}
      data-stacked-domain={model.axis.domain.join(',')}
      data-stacked-gaps={model.gaps}
      className="m-0 flex flex-col gap-1"
    >
      <div
        ref={plotRef}
        data-stacked-column-plot=""
        data-stacked-orientation={axis.orientation}
        data-stacked-axis-angle={axis.angle}
        data-stacked-axis-height={axis.height}
        data-stacked-axis-interval={axis.interval}
        data-stacked-axis-max-chars={maxChars}
        // Only when it grew (a DOM snapshot of an unchanged chart stays unchanged).
        data-stacked-plot-right={plotRight === PLOT_MARGIN.right ? undefined : plotRight}
        className="w-full focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
        {...cursor.surfaceProps}
      >
        <ChartResponsive height={plotHeight}>
          {asBars ? (
            // `accessibilityLayer={false}` — explicitly; see `ChartCursor`.
            <BarChart data={rows} layout="vertical" margin={BARS_MARGIN} accessibilityLayer={false}>
              <defs>{renderPatterns(patterns)}</defs>
              <CartesianGrid strokeDasharray="3 3" stroke={CHART_VARS.grid} horizontal={false} />
              <XAxis
                type="number"
                domain={model.axis.domain}
                ticks={model.axis.ticks}
                interval={0}
                allowDataOverflow={false}
                axisLine={false}
                tickLine={false}
                tick={RECHARTS_AXIS_TICK}
                height={BARS_VALUE_AXIS_HEIGHT}
                tickFormatter={(value: number) => model.format(value)}
                label={{ value: model.valueTitle, position: 'insideBottom', offset: -4, fill: CHART_VARS.axis, fontSize: 11 }}
              />
              {/* Every name, beside its bar: a category is never left unlabelled. */}
              <YAxis
                type="category"
                dataKey="key"
                width={barAxis?.width}
                interval={0}
                axisLine={false}
                tickLine={false}
                tick={RECHARTS_AXIS_TICK}
                tickFormatter={axisLabel}
              />
              {tooltip}
              {legendNode}
              {bars}
              {zeroTicks}
            </BarChart>
          ) : (
            // `accessibilityLayer={false}` — explicitly; see `ChartCursor`.
            <BarChart data={rows} margin={plotMargin} accessibilityLayer={false}>
              <defs>{renderPatterns(patterns)}</defs>
              <CartesianGrid strokeDasharray="3 3" stroke={CHART_VARS.grid} vertical={false} />
              <XAxis
                dataKey="key"
                axisLine={false}
                tickLine={false}
                tick={RECHARTS_AXIS_TICK}
                // Every tick is placed; a thinned time axis blanks the labels it
                // skips (`axisLabel`), counted from the newest, because Recharts'
                // own `interval` always counts from the oldest and so dropped today.
                interval={0}
                angle={axis.angle}
                textAnchor={axis.angle === 0 ? 'middle' : 'end'}
                height={axis.height}
                // The key is unique; the label need not be. The axis draws the label, cut in the middle.
                tickFormatter={axisLabel}
              />
              {/*
                Domain AND ticks from the model: zero-based, ending on a tick, so
                the last interval is as wide as the others.
              */}
              <YAxis
                domain={model.axis.domain}
                ticks={model.axis.ticks}
                interval={0}
                allowDataOverflow={false}
                axisLine={false}
                tickLine={false}
                tick={RECHARTS_AXIS_TICK}
                width={yWidth}
                tickFormatter={(value: number) => model.format(value)}
                // `offset: 14`, as the time series' rate title: at the default 5
                // the rotated title's line box overhung the svg's left edge.
                label={{ value: model.valueTitle, angle: -90, position: 'insideLeft', offset: 14, fill: CHART_VARS.axis, fontSize: 11 }}
              />
              {tooltip}
              {legendNode}
              {bars}
              {zeroTicks}
            </BarChart>
          )}
        </ChartResponsive>
        {cursor.readout}
      </div>
      {gaps && (
        <p data-chart-gap-note="" className={NOTE}>
          {gaps}
        </p>
      )}
      {invalid && (
        <p data-chart-invalid-note="" className={NOTE}>
          {invalid}
        </p>
      )}
    </figure>
  )
}
