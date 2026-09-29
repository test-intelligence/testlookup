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
 *     column, every Nth label flat when they nearly do, and slanted with an
 *     axis as tall as the longest slanted label needs when they do not. A
 *     reserve sized for Segoe UI cut labels on the Linux CI runner's DejaVu
 *     Sans in Wave 2.4; this one is sized from the label.
 *   - Labels are ingested text (suite names, run names): they reach the DOM
 *     only as React text, middle-truncated on the axis and whole in the
 *     tooltip and the table.
 *   - The plot sits inside a named, focusable `role="group"` with a keyboard
 *     cursor (`useChartCursor`) that reads each column in the same words as
 *     the pointer's tooltip; Recharts' own `accessibilityLayer` is off.
 */
import { useCallback, useMemo } from 'react'
import { Bar, BarChart, CartesianGrid, Legend, ReferenceDot, Tooltip, XAxis, YAxis } from 'recharts'
import { cursorPoint, useChartCursor, type ChartCursorPoint } from './ChartCursor'
import { PinnedTip, useColumnMark } from './ChartTooltip'
import ChartResponsive from './ChartResponsive'
import { middleTruncate } from './BarChart.model'
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
import { COLUMN_SIDES } from './tipPlacement'
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
/**
 * Where nothing is laid out (jsdom, a tab never shown) text cannot be
 * measured: 0.62 em a character is wider than the average glyph of Segoe UI,
 * San Francisco and DejaVu Sans alike, so an estimate errs toward room.
 */
const ESTIMATE_EM_PER_CHAR = 0.62

export const estimateTextWidth: TextMeasure = (text, fontSize) => [...text].length * fontSize * ESTIMATE_EM_PER_CHAR

export interface ColumnAxisLayout {
  /** 0 (flat) or `-SLANT_ANGLE`. */
  angle: number
  /** The x axis's height, px. */
  height: number
  /** Recharts' `interval`: labels shown are every `interval + 1`th, from the first. */
  interval: number
  /** The longest drawn label, px. */
  longest: number
}

/**
 * How the bucket labels are laid out under columns `band` px apart.
 *
 * Flat when the longest label fits its column; flat and thinned to every Nth
 * label while N stays small (a month of days reads "Sep 1, Sep 4, …"); else
 * slanted by `SLANT_ANGLE`, thinned only so far that two slanted labels do not
 * touch, with an axis as tall as the longest slanted label reaches. `band` 0
 * means no width is known yet: flat, all labels, Recharts' own height.
 */
export function columnAxisLayout(labels: readonly string[], band: number, measure: TextMeasure): ColumnAxisLayout {
  let longest = 0
  for (const label of labels) longest = Math.max(longest, measure(label, LABEL_SIZE))
  if (!(band > 0) || labels.length === 0) return { angle: 0, height: FLAT_AXIS_HEIGHT, interval: 0, longest }
  const stride = Math.max(1, Math.ceil((longest + FLAT_LABEL_GAP) / band))
  if (stride <= MAX_FLAT_STRIDE) return { angle: 0, height: FLAT_AXIS_HEIGHT, interval: stride - 1, longest }
  const angle = (SLANT_ANGLE * Math.PI) / 180
  // Slanted labels are parallel lines `band · sin(angle)` apart: they need a line's height between them.
  const slantStride = Math.max(1, Math.ceil((LINE_EM * LABEL_SIZE) / (band * Math.sin(angle))))
  const needed = TICK_ANCHOR + longest * Math.sin(angle) + TICK_LINE_DEPTH_EM * LABEL_SIZE * Math.cos(angle)
  return { angle: -SLANT_ANGLE, height: Math.max(FLAT_AXIS_HEIGHT, Math.ceil(needed) + 1), interval: slantStride - 1, longest }
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

/** The short tick a MEASURED zero column gets on the baseline: present, and empty. */
function ZeroTick({ cx, cy, bucket }: { cx?: number; cy?: number; bucket: string }) {
  if (typeof cx !== 'number' || typeof cy !== 'number') return null
  return (
    <line
      data-stacked-zero={bucket}
      x1={cx - 6}
      x2={cx + 6}
      y1={cy}
      y2={cy}
      stroke={CHART_VARS.axis}
      strokeWidth={2}
      strokeLinecap="round"
    />
  )
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
  const shortLabels = useMemo(
    () => new Map(model.buckets.map((bucket) => [bucket.key, middleTruncate(bucket.label, AXIS_LABEL_MAX_CHARS)])),
    [model],
  )

  const tickLabels = useMemo(() => model.axis.ticks.map((tick) => model.format(tick)), [model])
  const yWidth = valueAxisWidth(tickLabels, measure)
  // The band a column has, in LAYOUT px (full screen scales the drawing, not the layout).
  const plotWidth = width / scale - yWidth - PLOT_MARGIN.left - PLOT_MARGIN.right
  const band = width > 0 && model.buckets.length > 0 ? plotWidth / model.buckets.length : 0
  const axis = useMemo(
    () => columnAxisLayout([...shortLabels.values()], band, measure ?? estimateTextWidth),
    [shortLabels, band, measure],
  )

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
        data-stacked-axis-angle={axis.angle}
        data-stacked-axis-height={axis.height}
        data-stacked-axis-interval={axis.interval}
        className="w-full focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
        {...cursor.surfaceProps}
      >
        <ChartResponsive height={height}>
          {/* `accessibilityLayer={false}` — explicitly; see `ChartCursor`. */}
          <BarChart data={rows} margin={PLOT_MARGIN} accessibilityLayer={false}>
            <defs>{renderPatterns(patterns)}</defs>
            <CartesianGrid strokeDasharray="3 3" stroke={CHART_VARS.grid} vertical={false} />
            <XAxis
              dataKey="key"
              axisLine={false}
              tickLine={false}
              tick={RECHARTS_AXIS_TICK}
              interval={axis.interval}
              angle={axis.angle}
              textAnchor={axis.angle === 0 ? 'middle' : 'end'}
              height={axis.height}
              // The key is unique; the label need not be. The axis draws the label, cut in the middle.
              tickFormatter={(key: string) => shortLabels.get(String(key)) ?? String(key)}
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
            {/* Pinned beside its column (`PinnedTip`): a fixed origin, no slide. */}
            <Tooltip
              key={cursor.tipKey}
              cursor={false}
              content={<StackedColumnTooltip model={model} />}
              position={{ x: 0, y: 0 }}
              isAnimationActive={false}
              {...cursor.tipProps}
            />
            <Legend content={() => <ChartLegend entries={legend} />} />
            {model.series.map((entry, s) => (
              <Bar
                key={entry.key}
                dataKey={entry.field}
                name={entry.label}
                stackId="stack"
                fill={patternFill(seriesPatternId(prefix, entry, s))}
                maxBarSize={COLUMN_MAX_WIDTH}
                isAnimationActive={animate}
              />
            ))}
            {zeros.map((bucket) => (
              <ReferenceDot
                key={`zero-${bucket.key}`}
                x={bucket.key}
                y={0}
                r={0}
                ifOverflow="visible"
                shape={(props: { cx?: number; cy?: number }) => <ZeroTick cx={props.cx} cy={props.cy} bucket={bucket.key} />}
              />
            ))}
          </BarChart>
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
