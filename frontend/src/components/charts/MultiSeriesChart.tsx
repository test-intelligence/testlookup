/**
 * `MultiSeriesChart` (VIZ-404) — suites, releases or branches overlaid on one
 * chart, so they can be compared directly.
 *
 * Everything it draws comes from `multiSeriesModel` (pure, tested on its own);
 * this file is the drawing and the interaction. What it is careful about:
 *
 *   - One line per series, told apart by DASH as well as colour, and named
 *     twice: by a direct label at the line's end and by the legend. A direct
 *     label is a swatch of the line's own colour AND dash, then its name; a
 *     thin axis-coloured connector — never the line's colour or dash, and
 *     clear of the line's end — leads to it from the height the line ends at
 *     (`LABEL_ROW`). Direct labels never overlap — they are nudged apart, in
 *     centred clusters (`placeDirectLabels`) — and when they cannot fit, in
 *     height or because their fixed gutter would leave the plot too narrow,
 *     they are dropped and a note says the legend names every series.
 *   - ONE shared tooltip per day, every shown series in it, SORTED DESCENDING;
 *     an unmeasured value is "—" with its reason, last. It is PINNED per day —
 *     a fixed offset from the day's x, at the top of the plot, never following
 *     the pointer — and while the pointer is on it the day it names holds, so
 *     it can be hovered (SC 1.4.13). The keyboard reaches exactly the same
 *     content through `useChartCursor` (a named, focusable group; never
 *     Recharts' unnamed `role="application"`), drawn in a readout BELOW the
 *     plot, where it hides none of the lines it describes.
 *   - A day a series did not measure is a gap: `connectNulls` is off, and a
 *     measured point with no measured neighbour is drawn as a dot, because a
 *     line renderer draws nothing at all for it. The still-filling day
 *     (`meta.partial_day`) is drawn HOLLOW, and says so under the plot.
 *   - The legend is a row of toggle buttons: select one to hide or show that
 *     line, Shift+select to show it alone. A hidden line's direct label goes
 *     with it. The frame (`MultiSeriesChartFrame`) owns the hidden set, so the
 *     table and the page announcer hear about it too. "Show all series" hands
 *     focus to the first toggle as it goes, so focus never falls to <body>.
 *   - `comparable: false` is a note (`role="note"`) over the plot, and the
 *     comparison is still drawn. The plot's group is described by it, so a
 *     reader who tabs straight to the plot hears the caveat.
 *
 * The React tooltip, not a string formatter: series names are ingested CI
 * text, and React escapes them by construction.
 */
import {
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
} from 'react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  useChartWidth,
  usePlotArea,
  useXAxisScale,
  useYAxisScale,
} from 'recharts'
import { formatNumber } from '@/utils/formatters'
import { useChartCursor, type ChartCursorPoint } from './ChartCursor'
import { CHART_VARS, RECHARTS_AXIS_TICK } from './tokens'
import { useChartAnimation } from './motion'
import {
  DIRECT_LABEL_GUTTER,
  LABEL_ROW,
  LABEL_SWATCH_LENGTH,
  LEADER_DASH,
  PARTIAL_MARK,
  directLabelText,
  placeDirectLabels,
  tipContentAt,
  tipRowName,
  tipText,
  type MultiSeriesLine,
  type MultiSeriesModel,
  type TipContent,
  type ValueFormat,
} from './multiSeriesModel'

export interface MultiSeriesChartProps {
  model: MultiSeriesModel
  /** The frame's title: it names the keyboard cursor's surface and the legend. */
  title: string
  /** Series keys the reader has hidden. */
  hidden: ReadonlySet<string>
  onToggle: (key: string) => void
  onIsolate: (key: string) => void
  onShowAll: () => void
  /** How a value is printed in the tooltip, the readout and the direct labels' table twin. */
  format: ValueFormat
  /** Whether the line-end labels fit; reported by the plot, drawn as a note here. */
  labelsFit: boolean
  onLabelsFit: (fits: boolean) => void
  height?: number
  animate?: boolean
}

const NOTE = 'text-xs text-[var(--color-text-secondary)]'
/** Room to the right of the plot for the direct labels (laid out by `LABEL_ROW`). */
export { DIRECT_LABEL_GUTTER }
/** The right margin when there are no direct labels. */
export const NO_GUTTER = 16
/** A legend swatch's length: longer than every dash period (at most 32 px), so each pattern shows whole. */
export const SWATCH_LENGTH = 36
/** How far the pinned tooltip sits from its day's x — at most half a day's step, so it starts inside that day. */
export const TIP_GAP = 12

/** The legend's standing instruction, visible and referenced by every toggle. */
export const LEGEND_HINT = 'Select a series to hide or show it; Shift+select shows it alone.'
export const SHOW_ALL_LABEL = 'Show all series'
export const LABELS_DROPPED_NOTE = 'The line-end labels do not fit at this size; the legend names every series.'
export const ALL_HIDDEN_NOTE = 'Every series is hidden. Select one in the legend to show it.'

/** The legend group's accessible name: which chart's series these are. */
const legendName = (title: string) => `Series shown on ${title}`

/** The note under a chart whose newest day is still filling. */
function partialNote(day: string, inProgress: number): string {
  const progress = inProgress > 0 ? ` (${formatNumber(inProgress)} in progress)` : ''
  return `${day} is ${PARTIAL_MARK}${progress}: its points are drawn hollow, and the line-end labels name the last complete day.`
}

const colourOf = (line: Pick<MultiSeriesLine, 'styleIndex'>) => CHART_VARS.series[line.styleIndex]
/** A Recharts dataKey per line, by position: a series key may contain dots, which Recharts reads as a path. */
const dataKeyOf = (index: number) => `s${index}`

// ── The shared tooltip ───────────────────────────────────────────────────────

function Swatch({ stroke, dash }: { stroke: string; dash: string | undefined }) {
  return (
    <svg width={SWATCH_LENGTH} height={10} aria-hidden="true" focusable="false" className="shrink-0">
      <line
        data-series-swatch=""
        x1={1}
        y1={5}
        x2={SWATCH_LENGTH - 1}
        y2={5}
        stroke={stroke}
        strokeWidth={2}
        strokeDasharray={dash}
      />
    </svg>
  )
}

/** The body the pointer tooltip AND the keyboard readout both draw. */
export function MultiSeriesTipBody({ model, content }: { model: MultiSeriesModel; content: TipContent }) {
  return (
    <div className="flex flex-col gap-0.5">
      <div className="font-semibold">{content.title}</div>
      <ol data-multi-series-tip-rows="" className="flex flex-col gap-0.5">
        {content.rows.map((row) => (
          <li key={row.key} data-tip-series={row.key} data-tip-measured={row.y === null ? 'false' : 'true'} className="flex items-baseline gap-2">
            <Swatch stroke={CHART_VARS.series[row.styleIndex]} dash={row.dash} />
            <span className="min-w-0 break-words text-[var(--color-text-secondary)]">{tipRowName(model, row)}</span>
            <span className="ml-auto whitespace-nowrap pl-3 font-medium tabular-nums" data-tip-value="">
              {row.value}
            </span>
            {row.partial && row.y !== null && (
              <span data-tip-partial="" className="whitespace-nowrap text-[var(--color-text-secondary)]">
                ({PARTIAL_MARK})
              </span>
            )}
          </li>
        ))}
      </ol>
      {/* The reasons, visible, after the values: a "—" nobody explains reads as broken. */}
      {content.rows.some((row) => row.reason) && (
        <ul data-tip-reasons="" className="mt-0.5 flex max-w-xs flex-col gap-0.5 text-[var(--color-text-secondary)]">
          {content.rows
            .filter((row) => row.reason)
            .map((row) => (
              <li key={row.key} data-tip-reason-visible={row.key} className="whitespace-normal break-words">
                {row.label}: {row.reason}
              </li>
            ))}
        </ul>
      )}
      {content.hiddenCount > 0 && (
        <div data-tip-hidden="" className="text-[var(--color-text-secondary)]">
          {formatNumber(content.hiddenCount)} hidden {content.hiddenCount === 1 ? 'series is' : 'series are'} not listed.
        </div>
      )}
    </div>
  )
}

const TIP_BOX =
  'rounded border border-[var(--color-border-light)] bg-[var(--color-bg-card)] px-2 py-1 text-xs text-[var(--color-text)]'

interface MultiSeriesTipProps {
  /** Recharts passes these; nothing is drawn unless `active`. */
  active?: boolean
  label?: string | number
  /**
   * The pointer's position, which Recharts also passes. Deliberately NOT what
   * places the tooltip — the day's own x is — and used only if the chart has
   * no x scale to ask.
   */
  coordinate?: { x?: number; y?: number }
  model: MultiSeriesModel
  hidden: ReadonlySet<string>
  format: ValueFormat
}

/**
 * The pointer's shared tooltip: one day, every shown series, sorted
 * descending — PINNED to that day.
 *
 * Recharts places a tooltip relative to the pointer, so it slides as the
 * pointer moves, including towards it: nobody could get onto it (SC 1.4.13,
 * Hoverable). Here Recharts' wrapper is fixed at the chart's origin
 * (`position={{ x: 0, y: 0 }}`, no slide animation) and this box places
 * itself: `TIP_GAP` right of the day's x — or left of it, when it would run
 * past the chart's edge — at the top of the plot. The gap is at most half a
 * day's step, so the box starts INSIDE the day's band: a pointer moving
 * straight towards it never crosses into the next day first. Once the pointer
 * is on it, its moves stop here and never reach the chart, so the day it
 * names holds for as long as the pointer stays.
 */
export function MultiSeriesTip({ active, label, coordinate, model, hidden, format }: MultiSeriesTipProps) {
  const plot = usePlotArea()
  const chartWidth = useChartWidth()
  const xScale = useXAxisScale()
  const box = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  const index = model.xs.indexOf(String(label))
  const open = Boolean(active) && index >= 0

  // Its own width decides which side of the day it goes: measured before
  // paint, so it never draws on the wrong side first.
  useLayoutEffect(() => {
    if (!open) return
    const measured = box.current?.offsetWidth ?? 0
    if (measured !== width) setWidth(measured)
    // Re-measured whenever what it shows can change: the day, the hidden set, the model.
  }, [open, index, hidden, model, width])

  if (!open) return null

  const plotX = plot?.x ?? 0
  const plotWidth = plot?.width ?? 0
  const dayX = xScale?.(model.xs[index]) ?? coordinate?.x ?? plotX
  const days = model.xs.length
  const step = days > 1 ? plotWidth / (days - 1) : plotWidth
  const gap = Math.min(TIP_GAP, step / 2)
  const edge = chartWidth ?? plotX + plotWidth
  let left = dayX + gap
  if (left + width > edge) left = dayX - gap - width
  left = Math.max(0, Math.min(left, Math.max(0, edge - width)))

  const style: CSSProperties = {
    position: 'absolute',
    left,
    top: plot?.y ?? 0,
    width: 'max-content',
    maxWidth: edge,
    // Recharts' wrapper is `pointer-events: none` by default; the box itself
    // must be something the pointer can be over.
    pointerEvents: 'auto',
  }

  return (
    <div
      ref={box}
      data-chart-tooltip=""
      className={TIP_BOX}
      style={style}
      // On the tooltip, the pointer is reading it: do not let Recharts pick a
      // new day from where it is.
      onMouseMove={(event) => event.stopPropagation()}
    >
      <MultiSeriesTipBody model={model} content={tipContentAt(model, index, { hidden, format })} />
    </div>
  )
}

// ── Direct labels (rendered INSIDE the Recharts svg, so they share its scale) ─

function DirectLabels({
  lines,
  onFit,
  gutterOn,
}: {
  lines: readonly MultiSeriesLine[]
  onFit: (fits: boolean) => void
  /** Whether the plot is currently drawn WITH the labels' gutter. */
  gutterOn: boolean
}) {
  const plot = usePlotArea()
  const yScale = useYAxisScale()
  // The plot's numbers, not the object: `usePlotArea` hands back a fresh one each render.
  const top = plot?.y
  const bottom = plot ? plot.y + plot.height : undefined
  const edge = plot ? plot.x + plot.width : undefined
  // The width the plot has — or would have — WITH the gutter. Judged the same
  // way whether the gutter is on or off, so dropping it cannot bring it back.
  const plotWidth = plot ? plot.width - (gutterOn ? 0 : DIRECT_LABEL_GUTTER - NO_GUTTER) : undefined
  const placement = useMemo(() => {
    if (top === undefined || bottom === undefined || !yScale) return null
    const requests = lines
      .filter((line) => line.last !== null)
      .map((line) => ({ key: line.key, y: yScale((line.last as { y: number }).y) ?? Number.NaN }))
    return placeDirectLabels(requests, { top, bottom, plotWidth })
  }, [top, bottom, yScale, lines, plotWidth])
  const fits = placement?.fits
  useEffect(() => {
    if (fits !== undefined) onFit(fits)
  }, [fits, onFit])
  if (edge === undefined || !placement || !placement.fits) return null
  const byKey = new Map(lines.map((line) => [line.key, line]))
  return (
    // The legend and the table name every series for assistive tech; these
    // are the same names again, drawn where the eye already is.
    <g data-multi-series-direct-labels="" aria-hidden="true">
      {placement.labels.map((placed) => {
        const line = byKey.get(placed.key)
        if (!line) return null
        // Flat from the height the line ends at, then one angled step to the
        // label's row: only in the gutter, and clear of the line's end.
        const leader = [
          [edge + LABEL_ROW.leaderStart, placed.target],
          [edge + LABEL_ROW.leaderTurn, placed.target],
          [edge + LABEL_ROW.leaderEnd, placed.y],
        ]
          .map(([x, y]) => `${x},${y}`)
          .join(' ')
        return (
          <g key={placed.key} data-direct-label={placed.key}>
            <polyline
              data-direct-label-leader=""
              points={leader}
              fill="none"
              stroke={CHART_VARS.axis}
              strokeWidth={1}
              strokeDasharray={LEADER_DASH}
              strokeLinejoin="round"
            />
            <line
              data-direct-label-swatch=""
              x1={edge + LABEL_ROW.swatchStart}
              y1={placed.y}
              x2={edge + LABEL_ROW.swatchStart + LABEL_SWATCH_LENGTH}
              y2={placed.y}
              stroke={colourOf(line)}
              strokeWidth={2}
              strokeDasharray={line.dash}
            />
            <text x={edge + LABEL_ROW.textStart} y={placed.y} dominantBaseline="central" fontSize={11} fill={CHART_VARS.text}>
              {directLabelText(line.label)}
            </text>
          </g>
        )
      })}
    </g>
  )
}

// ── The legend: toggle buttons ───────────────────────────────────────────────

function SeriesLegend({
  title,
  lines,
  hidden,
  onToggle,
  onIsolate,
  onShowAll,
}: Pick<MultiSeriesChartProps, 'title' | 'hidden' | 'onToggle' | 'onIsolate' | 'onShowAll'> & { lines: readonly MultiSeriesLine[] }) {
  const hintId = useId()
  const firstToggle = useRef<HTMLButtonElement>(null)
  const onClick = (key: string) => (event: ReactMouseEvent<HTMLButtonElement>) => {
    if (event.shiftKey) onIsolate(key)
    else onToggle(key)
  }
  // Shift+Enter / Shift+Space from the keyboard: the same isolate the mouse gets.
  const onKeyDown = (key: string) => (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (!event.shiftKey || (event.key !== 'Enter' && event.key !== ' ')) return
    event.preventDefault()
    onIsolate(key)
  }
  // "Show all series" leaves the page once nothing is hidden: hand focus to
  // the first toggle as it goes, or it falls to <body>.
  const showAll = () => {
    onShowAll()
    firstToggle.current?.focus()
  }
  return (
    <div data-multi-series-legend="" role="group" aria-label={legendName(title)} className="flex flex-col items-center gap-1 pt-3">
      <ul className="flex flex-wrap items-center justify-center gap-x-1 gap-y-1 text-xs">
        {lines.map((line, index) => {
          const off = hidden.has(line.key)
          return (
            <li key={line.key}>
              <button
                ref={index === 0 ? firstToggle : undefined}
                type="button"
                aria-pressed={!off}
                aria-describedby={hintId}
                data-legend-series={line.key}
                data-legend-hidden={off ? 'true' : 'false'}
                onClick={onClick(line.key)}
                onKeyDown={onKeyDown(line.key)}
                className="flex min-h-6 items-center gap-1.5 rounded px-1.5 py-0.5 text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
              >
                {/* A hidden series keeps its dash, drawn in the neutral colour, and its name is struck through: not a colour-only difference. */}
                <Swatch stroke={off ? CHART_VARS.neutral : colourOf(line)} dash={line.dash} />
                <span className={off ? 'line-through' : undefined}>{line.label}</span>
              </button>
            </li>
          )
        })}
      </ul>
      <p className={`${NOTE} flex flex-wrap items-center justify-center gap-x-2`}>
        <span id={hintId}>{LEGEND_HINT}</span>
        {hidden.size > 0 && (
          <button
            type="button"
            data-legend-show-all=""
            onClick={showAll}
            className="min-h-6 rounded border border-[var(--color-border-light)] px-2 py-0.5 text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
          >
            {SHOW_ALL_LABEL}
          </button>
        )}
      </p>
    </div>
  )
}

// ── The not-comparable note ──────────────────────────────────────────────────

/** A caution mark, drawn in the text colour: the note is never told by colour alone. */
function CaveatIcon() {
  return (
    <svg width={14} height={14} viewBox="0 0 16 16" aria-hidden="true" focusable="false" className="mt-px shrink-0">
      <path d="M8 1.5 15 14.5H1Z" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinejoin="round" />
      <path d="M8 6v4" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" />
      <circle cx={8} cy={12.25} r={0.9} fill="currentColor" />
    </svg>
  )
}

// ── The chart ────────────────────────────────────────────────────────────────

/**
 * Recharts' `dot` for one line: a filled dot on every measured point when the
 * line has an isolated one (a line renderer draws nothing for it), and a
 * HOLLOW one on the still-filling day — a shape, not a colour, says "partial".
 */
function dotFor(line: MultiSeriesLine) {
  const colour = colourOf(line)
  const partial = line.points.some((point) => point.partial && point.y !== null)
  if (!line.isolated && !partial) return false
  return function MultiSeriesDot(props: { index?: number; cx?: number; cy?: number }) {
    const point = props.index === undefined ? undefined : line.points[props.index]
    if (!point || point.y === null || props.cx === undefined || props.cy === undefined) return <g />
    if (point.partial) {
      return (
        <circle
          data-partial-dot=""
          className="recharts-dot"
          cx={props.cx}
          cy={props.cy}
          r={4}
          fill={CHART_VARS.card}
          stroke={colour}
          strokeWidth={2}
        />
      )
    }
    return line.isolated ? <circle className="recharts-dot" cx={props.cx} cy={props.cy} r={3} fill={colour} strokeWidth={0} /> : <g />
  }
}

export default function MultiSeriesChart({
  model,
  title,
  hidden,
  onToggle,
  onIsolate,
  onShowAll,
  format,
  labelsFit,
  onLabelsFit,
  height = 280,
  animate: requestedAnimate,
}: MultiSeriesChartProps) {
  const animate = useChartAnimation(requestedAnimate)
  const bannerId = useId()
  const shown = useMemo(() => model.lines.filter((line) => !hidden.has(line.key)), [model, hidden])
  const dots = useMemo(() => new Map(model.lines.map((line) => [line.key, dotFor(line)])), [model])

  const rows = useMemo(
    () =>
      model.xs.map((x, i) => {
        const row: Record<string, string | number | null> = { x }
        model.lines.forEach((line, index) => {
          row[dataKeyOf(index)] = line.points[i]?.y ?? null
        })
        return row
      }),
    [model],
  )

  /** One cursor stop per day, worded by the SAME builder as the pointer's tooltip. */
  const contents = useMemo(
    () => model.xs.map((_, index) => tipContentAt(model, index, { hidden, format })),
    [model, hidden, format],
  )
  const cursorPoints = useMemo<ChartCursorPoint[]>(
    () => model.xs.map((x, index) => ({ key: x, text: tipText(model, contents[index]) })),
    [model, contents],
  )
  const cursor = useChartCursor({ title, chartType: 'multi-series line chart', points: cursorPoints, noun: 'day' })
  const focused = cursor.index >= 0 ? model.xs[cursor.index] : null

  return (
    <figure data-chart="multi-series" data-alignment={model.alignment} data-partial-day={model.partialDay ?? undefined} className="m-0 flex flex-col gap-1">
      {model.banner && (
        <div
          id={bannerId}
          role="note"
          data-chart-comparable-banner=""
          // A 3:1 boundary against both its fill and the card (text-muted is
          // at least 3.4:1 against either, in every theme), and a heavier left
          // edge: a caveat, not a footnote.
          className="flex items-start gap-2 rounded border border-l-4 border-[var(--color-text-muted)] bg-[var(--color-bg-hover)] px-2 py-1.5 text-xs font-medium text-[var(--color-text)]"
        >
          <CaveatIcon />
          <span>{model.banner}</span>
        </div>
      )}

      <div
        data-multi-series-plot=""
        data-series-shown={shown.length}
        className="relative w-full focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
        {...cursor.surfaceProps}
        // Tabbing straight to the plot, a reader hears the caveat with its name.
        aria-describedby={model.banner ? bannerId : undefined}
      >
        <ResponsiveContainer width="100%" height={height}>
          {/* `accessibilityLayer={false}` — explicitly; see `ChartCursor`. */}
          <LineChart
            data={rows}
            margin={{ top: 16, right: labelsFit && shown.length > 0 ? DIRECT_LABEL_GUTTER : NO_GUTTER, left: 0, bottom: 0 }}
            accessibilityLayer={false}
          >
            <CartesianGrid strokeDasharray="3 3" stroke={CHART_VARS.grid} vertical={false} />
            <XAxis
              dataKey="x"
              axisLine={false}
              tickLine={false}
              tick={RECHARTS_AXIS_TICK}
              height={44}
              label={{ value: model.xTitle, position: 'insideBottom', fill: CHART_VARS.axis, fontSize: 11 }}
            />
            <YAxis
              domain={model.yAxis.domain}
              ticks={model.yAxis.ticks}
              interval={0}
              allowDataOverflow
              axisLine={false}
              tickLine={false}
              tick={RECHARTS_AXIS_TICK}
              tickFormatter={(value: number) => formatNumber(value, { maximumFractionDigits: 2 })}
              // `offset` keeps the rotated title inside the svg: at Recharts' default 5 its
              // first pixels sit left of x = 0 and are clipped.
              label={{ value: model.metric.title, angle: -90, position: 'insideLeft', offset: 14, fill: CHART_VARS.axis, fontSize: 11 }}
            />
            <Tooltip
              key={cursor.tipKey}
              content={<MultiSeriesTip model={model} hidden={hidden} format={format} />}
              cursor={{ stroke: CHART_VARS.neutral, strokeDasharray: '3 3' }}
              // Pinned: the box places itself by the day (see `MultiSeriesTip`).
              position={{ x: 0, y: 0 }}
              isAnimationActive={false}
              {...cursor.tipProps}
            />
            {focused !== null && <ReferenceLine x={focused} stroke={CHART_VARS.neutral} strokeDasharray="3 3" />}
            {model.lines.map((line, index) =>
              hidden.has(line.key) ? null : (
                <Line
                  key={line.key}
                  type="linear"
                  dataKey={dataKeyOf(index)}
                  name={line.label}
                  stroke={colourOf(line)}
                  strokeWidth={2}
                  strokeDasharray={line.dash}
                  // A gap is a gap: bridging it would invent a day that was never measured.
                  connectNulls={false}
                  dot={dots.get(line.key) ?? false}
                  activeDot={{ r: 4 }}
                  isAnimationActive={animate}
                />
              ),
            )}
            {shown.length > 0 && <DirectLabels lines={shown} onFit={onLabelsFit} gutterOn={labelsFit} />}
          </LineChart>
        </ResponsiveContainer>
        {cursor.point && (
          // What the reader just heard, for a sighted keyboard user: the same
          // sorted rows the pointer tooltip shows, BELOW the plot — laid over
          // it, it hid a third of the lines it was describing. Not a live
          // region: the page's one announcer has already said it.
          <div data-chart-readout="" aria-hidden="true" className={`mt-1 ${TIP_BOX}`}>
            <MultiSeriesTipBody model={model} content={contents[cursor.index]} />
          </div>
        )}
      </div>

      <SeriesLegend title={title} lines={model.lines} hidden={hidden} onToggle={onToggle} onIsolate={onIsolate} onShowAll={onShowAll} />

      <figcaption data-chart-axis-caption="" className={NOTE}>
        {model.caption}
      </figcaption>
      {model.partialDay && (
        <p data-chart-partial-note="" className={NOTE}>
          {partialNote(model.partialDay, model.inProgressCount)}
        </p>
      )}
      {model.foldNotice && (
        <p data-chart-fold-notice="" className={NOTE}>
          {model.foldNotice}
        </p>
      )}
      {model.gaps > 0 && (
        <p data-chart-gap-note="" className={NOTE}>
          {formatNumber(model.gaps)} {model.gaps === 1 ? 'value is' : 'values are'} not measured and drawn as{' '}
          {model.gaps === 1 ? 'a gap' : 'gaps'}, never as 0.
        </p>
      )}
      {/* A release with fewer days than the axis: not a missed measurement, and said apart. */}
      {model.rangeNote && (
        <p data-chart-range-note="" className={NOTE}>
          {model.rangeNote}
        </p>
      )}
      {!labelsFit && shown.length > 0 && (
        <p data-chart-labels-note="" className={NOTE}>
          {LABELS_DROPPED_NOTE}
        </p>
      )}
      {shown.length === 0 && model.lines.length > 0 && (
        <p data-chart-all-hidden="" className={NOTE}>
          {ALL_HIDDEN_NOTE}
        </p>
      )}
      {model.cappedNote && (
        <p data-chart-capped-note="" className={NOTE}>
          {model.cappedNote}
        </p>
      )}
    </figure>
  )
}
