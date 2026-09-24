/**
 * `ChartTooltip` (VIZ-601) — the ONE tooltip markup every Recharts chart
 * renders through its `<Tooltip content>`, and the keyboard readout draws too.
 *
 * It renders a `TooltipContent` (see `tooltip.ts`) and nothing else: no chart
 * builds tooltip JSX of its own any more, so a tooltip cannot say something the
 * keyboard does not, and the Recharts and ECharts tooltips are the same
 * elements with the same classes and inline styles (`buildTooltipNode` is the
 * DOM twin of `ChartTooltipBody`). The look is the one `OverviewPage`'s trend
 * tooltip and `BarChart`'s `TooltipShell` had: a card-coloured box with a
 * hairline border, a bold title, then label / value lines with the values
 * right-aligned in tabular figures.
 *
 * Every string is a React text child: a test named `<img src=x onerror=…>`
 * from an ingested CI file is shown as those characters and never parsed.
 *
 * `PinnedTip` is the box a chart hands Recharts. It places ITSELF, beside the
 * mark it describes (`tipPlacement.ts`), because Recharts places a tooltip
 * relative to the pointer — over the mark, and sliding as the pointer moves,
 * including towards it, so nobody could get onto it:
 *
 *   beside     never over its mark; flipped at an edge; clamped inside the
 *              part of the chart the reader can see (viewport, scroll boxes);
 *              and, for a day or a row, OFF the line the pointer sweeps along
 *              (`TipSweep`), so sweeping on to the next day never runs into it.
 *   below      where it cannot sit beside its mark without covering it (a
 *              narrow window: 320 px), it is not drawn over the plot at all:
 *              the same box goes in the readout slot BELOW the plot, in the
 *              flow, where the keyboard cursor draws its readout.
 *   hoverable  (SC 1.4.13) it LINGERS briefly after the chart lets go, so a
 *              pointer crossing the gap from its mark finds it still there;
 *              and a pointer that comes onto it FROM ITS MARK (through the
 *              corridor to its facing edge: `approachStep`) is held — its
 *              moves on the box are stopped before they reach the chart, so
 *              the mark it names does not change under the pointer, for as
 *              long as the pointer stays. A pointer that reaches the box any
 *              other way (a sweep down the rows running into it) is NOT held:
 *              its moves reach the chart, which picks the mark under it.
 *   dismissible  Escape is `useChartCursor`'s: it sets `active: false` and
 *              remounts the Recharts tooltip (`tipKey`), which drops anything
 *              lingering here with it.
 *   in place   it renders INSIDE the chart's own DOM (Recharts portals its
 *              tooltip into the chart wrapper, never into `document.body`), so
 *              it stays visible when the chart's frame goes full screen.
 *
 * Nothing here re-renders the CHART: the box's own state (its position, what
 * lingers) lives in this component, which Recharts renders on its own.
 */
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react'
import { createPortal } from 'react-dom'
import { usePlotArea } from 'recharts'
import {
  TIP_CLASS,
  TIP_LINE_SWATCH,
  TIP_STYLE,
  TOOLTIP_NODE_ATTRIBUTE,
  rowLabelText,
  safeDataAttributes,
  tooltipText,
  type TooltipContent,
  type TooltipRow,
} from './tooltip'
import {
  ANY_SIDE,
  TIP_GAP,
  approachStep,
  bandGap,
  chartScaleOf,
  columnMark,
  onSweepLine,
  placeTip,
  tipMaxWidth,
  unscaled,
  visibleBoundsOf,
  type ApproachGeometry,
  type TipPoint,
  type TipRect,
  type TipSide,
  type TipSweep,
} from './tipPlacement'
import { RECHARTS_TOOLTIP_STYLE } from './tokens'

/** The box around the content: the card, its border and its padding (Overview's tooltip, BarChart's shell). */
export const TIP_BOX_STYLE: CSSProperties = {
  ...RECHARTS_TOOLTIP_STYLE,
  padding: '4px 8px',
  boxSizing: 'border-box',
}

/** How long a tooltip stays after the chart lets go of it, so the pointer can cross onto it. */
export const TIP_LINGER_MS = 300

function Swatch({ row }: { row: TooltipRow }) {
  if (!row.color) return null
  if (row.mark === 'line') {
    return (
      <svg
        aria-hidden="true"
        focusable="false"
        width={TIP_LINE_SWATCH}
        height={10}
        className={TIP_CLASS.swatch}
        style={TIP_STYLE.line}
      >
        <line
          x1={1}
          y1={5}
          x2={TIP_LINE_SWATCH - 1}
          y2={5}
          stroke={row.color}
          strokeWidth={2}
          strokeDasharray={row.dash}
        />
      </svg>
    )
  }
  return <span aria-hidden="true" className={TIP_CLASS.swatch} style={{ ...TIP_STYLE.swatch, backgroundColor: row.color }} />
}

function Row({ row }: { row: TooltipRow }) {
  const note = row.kind === 'note'
  const data = Object.fromEntries(safeDataAttributes(row.data))
  const label = rowLabelText(row)
  return (
    <div className={note ? TIP_CLASS.note : TIP_CLASS.row} data-tip-kind={row.kind ?? 'value'} style={note ? TIP_STYLE.note : TIP_STYLE.row} {...data}>
      {!note && <Swatch row={row} />}
      {label && (
        <span className={TIP_CLASS.label} style={TIP_STYLE.label}>
          {label}
        </span>
      )}
      <span className={TIP_CLASS.value} style={note ? TIP_STYLE.noteValue : TIP_STYLE.value} data-tip-value="">
        {row.value}
      </span>
      {row.detail && (
        <span className={TIP_CLASS.detail} style={TIP_STYLE.detail}>
          ({row.detail})
        </span>
      )}
    </div>
  )
}

const rowKey = (row: TooltipRow, index: number) => `${row.key ?? `${row.kind ?? 'value'}:${row.label}`}#${index}`

export interface ChartTooltipBodyProps {
  content: TooltipContent
  /** Mark it as THE tooltip (`data-chart-tooltip`). The keyboard readout draws the same body unmarked. */
  marked?: boolean
  style?: CSSProperties
}

/** The content, drawn: the React twin of `buildTooltipNode`. */
export function ChartTooltipBody({ content, marked = false, style }: ChartTooltipBodyProps) {
  const mark = marked ? { [TOOLTIP_NODE_ATTRIBUTE]: '' } : {}
  return (
    <div className={TIP_CLASS.body} style={{ ...TIP_STYLE.body, ...style }} {...mark}>
      <TipRows content={content} />
    </div>
  )
}

/** The content in its box, in the flow: for a tooltip that is not placed by `PinnedTip`. */
export default function ChartTooltip({ content }: { content: TooltipContent | null }) {
  if (!content) return null
  return <ChartTooltipBody content={content} marked style={TIP_BOX_STYLE} />
}

/** A mark: a rectangle in chart coordinates, or read from the chart's DOM once it is drawn. */
export type TipMark = TipRect | ((chart: HTMLElement) => TipRect | null) | null

export interface PinnedTipProps {
  /** What to show; `null` when the chart has nothing active. */
  content: TooltipContent | null
  /** The mark the content describes, which the box must stay clear of. */
  mark: TipMark
  /** Sides to try, in order (`COLUMN_SIDES` for a day or a bucket). */
  sides?: readonly TipSide[]
  align?: 'start' | 'center'
  gap?: number
  /**
   * The chart's box in chart coordinates, from Recharts' own geometry. Used
   * when the DOM cannot be measured (no layout: jsdom, a hidden tab); in a
   * real layout the visible part of the chart's element is used instead.
   */
  chartBox?: TipRect
  /**
   * The pointer's line along the category axis, when the mark is one of a
   * row of days or bars (`TipSweep`). The tooltip is kept off it — as it was
   * when the mark became active, and again whenever the pointer comes onto
   * the tooltip's line without approaching it — never re-placed while the
   * pointer is on its way to the tooltip.
   */
  sweep?: TipSweep
}

/**
 * The pointer's line in a Recharts chart, from the `coordinate` Recharts hands
 * its tooltip content: in a horizontal layout (days across) `coordinate.y` IS
 * the pointer's y (`getActiveCartesianCoordinate`), in a vertical one (rows
 * down) `coordinate.x` is the pointer's x.
 */
export function sweepOf(coordinate: { x?: number; y?: number } | undefined, axis: TipSweep['axis']): TipSweep | undefined {
  const at = axis === 'x' ? coordinate?.y : coordinate?.x
  return typeof at === 'number' && Number.isFinite(at) ? { axis, at } : undefined
}

/**
 * The mark of a category COLUMN (a day, a histogram bucket) for a Recharts
 * tooltip content component, from Recharts' own geometry: `coordinate.x` is
 * the active column's centre, the plot area gives its band and its height.
 * Returns the gap that keeps the tooltip inside the column's band (hoverable
 * without crossing into the next column), the chart box `PinnedTip` falls
 * back to where there is no layout to measure, and the pointer's line the
 * tooltip keeps off (`sweepOf`).
 */
export function useColumnMark(coordinate: { x?: number; y?: number } | undefined, columns: number, markHalf: number) {
  const plot = usePlotArea()
  const x = coordinate?.x
  if (x === undefined || !plot) return { mark: null, gap: TIP_GAP, chartBox: undefined, sweep: undefined }
  const band = columns > 0 ? plot.width / columns : plot.width
  const half = Math.min(markHalf, band / 2)
  return {
    mark: columnMark(x, half, { top: plot.y, height: plot.height }),
    gap: bandGap(band, half),
    chartBox: { left: 0, top: 0, width: plot.x + plot.width, height: plot.y + plot.height },
    sweep: sweepOf(coordinate, 'x'),
  }
}

/** The readout slot a tooltip that fits nowhere beside its mark goes into: the chart's cursor surface, below the plot. */
function slotOf(chart: HTMLElement | null): HTMLElement | null {
  return chart?.closest<HTMLElement>('[data-chart-cursor]') ?? null
}

/** The element a Recharts tooltip is placed in: the chart wrapper Recharts portals it into. */
function chartOf(box: HTMLElement): HTMLElement | null {
  return box.closest<HTMLElement>('.recharts-wrapper') ?? box.parentElement?.parentElement ?? null
}

function resolveMark(mark: TipMark, chart: HTMLElement | null): TipRect | null {
  if (typeof mark === 'function') return chart ? mark(chart) : null
  return mark
}

/**
 * The tooltip box a Recharts chart hands its `<Tooltip content>`: the
 * content, pinned beside its mark. Pass `position={{ x: 0, y: 0 }}` and
 * `isAnimationActive={false}` on the `<Tooltip>` so Recharts' wrapper stays
 * at the chart's origin and this box's `left` / `top` are chart coordinates.
 *
 * Holding the pointer (SC 1.4.13) is decided per pointer move by
 * `approachStep`, fed from a `mousemove` listener on the chart element in the
 * CAPTURE phase — so it has judged each move before the move reaches the box
 * or the chart:
 *   - on its way from the mark to the box, a move is kept from the chart
 *     (`suppress`), so the mark cannot change under a pointer heading for
 *     its tooltip;
 *   - on the box, having come that way (`hold`), the box stops the move, as
 *     every move on it did before;
 *   - on the box any other way, the move goes on to the chart. Recharts works
 *     the mark out from the pointer's position relative to ITS wrapper
 *     (`getRelativeCoordinate(event.currentTarget)`), so a move over the box
 *     picks the mark under the box — and the box moves beside that mark.
 * The box itself keeps `pointer-events: auto` throughout: only what the
 * chart is TOLD changes, so nothing flickers between hit-testable and not
 * under the pointer, and the box is still what the reader points at.
 */
export function PinnedTip({ content, mark, sides = ANY_SIDE, align = 'center', gap = TIP_GAP, chartBox, sweep }: PinnedTipProps) {
  const box = useRef<HTMLDivElement>(null)
  // The mark of what is shown, for a box that lingers after its chart let go.
  const lastMark = useRef<TipMark>(null)
  // What was last shown, kept so it can linger. Compared by its WORDS: Recharts
  // hands a fresh content object on every pointer move over the same mark.
  const [last, setLast] = useState<{ text: string; content: TooltipContent } | null>(null)
  const text = content ? tooltipText(content) : null
  if (content && text !== null && text !== last?.text) setLast({ text, content })
  // The pointer's line the box keeps off, taken when a mark becomes active.
  // Recharts hands a new line on every move; it is NOT followed, or a pointer
  // moving towards the box would push it away. Only the approach listener
  // moves it: when the pointer comes onto the box's line without approaching.
  const [line, setLine] = useState<{ text: string; sweep: TipSweep } | null>(null)
  if (text !== null && sweep && line?.text !== text) setLine({ text, sweep })
  const keptSweep = line && (text === null || line.text === text) ? line.sweep : undefined
  const [hovered, setHovered] = useState(false)
  const [position, setPosition] = useState<{ left: number; top: number; maxWidth?: number; side: TipSide; fits: boolean } | null>(null)
  // Set when the box fits nowhere beside its mark: the readout slot it is drawn in instead.
  const [slot, setSlot] = useState<HTMLElement | null>(null)

  // What the approach listener works from (see `approachStep`).
  const geometry = useRef<(ApproachGeometry & { sweep?: TipSweep; key: string }) | null>(null)
  const armed = useRef<TipPoint | null>(null)
  const holding = useRef(false)
  const shownText = useRef<string | null>(null)
  // What the box was last measured and placed for (see the layout effect).
  const measuredFor = useRef<string | null>(null)

  // The readout slot's surface the pointer has LEFT, if it has. A box in the
  // slot is not beside its mark: the pointer reaches it by leaving the plot
  // (the chart lets go at once) and crossing the axis below, which can take
  // longer than a linger. So there it stays while the pointer is anywhere
  // on the chart's surface, and lingers only once the pointer leaves that.
  const [outside, setOutside] = useState<HTMLElement | null>(null)
  useEffect(() => {
    if (!slot) return
    const leave = () => setOutside(slot)
    const enter = () => setOutside(null)
    slot.addEventListener('pointerleave', leave)
    slot.addEventListener('pointerenter', enter)
    return () => {
      slot.removeEventListener('pointerleave', leave)
      slot.removeEventListener('pointerenter', enter)
    }
  }, [slot])

  // The chart let go and the pointer is not on the box: linger, then go.
  const released = content === null
  const onSurface = slot !== null && outside !== slot
  useEffect(() => {
    if (!released || hovered || onSurface) return
    const timer = window.setTimeout(() => setLast(null), TIP_LINGER_MS)
    return () => window.clearTimeout(timer)
  }, [released, hovered, onSurface])

  const shown = content ?? last?.content ?? null

  // Measured before paint, so the box never draws in the wrong place first.
  useLayoutEffect(() => {
    if (content) lastMark.current = mark
    shownText.current = text ?? last?.text ?? null
    const el = box.current
    if (!el) return
    const chart = chartOf(el)
    const rect = chart?.getBoundingClientRect()
    const measurable = Boolean(chart && rect && rect.width > 0 && rect.height > 0)
    // Measured on screen, placed in chart coordinates: a drawing scaled up in
    // full screen (`ChartResponsive`) is divided back down.
    const bounds = measurable && chart ? unscaled(visibleBoundsOf(chart), chartScaleOf(chart)) : chartBox
    const at = resolveMark(content ? mark : lastMark.current, chart)
    if (!bounds || !at) return
    // Judged at its NATURAL size first (unwrapped). Where that fits beside the
    // mark on no side — a narrow window, the right-most day — the box is
    // wrapped to the room on its roomiest side (`tipMaxWidth`) and measured
    // again, so it sits beside the mark instead of being clamped over it. The
    // inline max-width is only borrowed for the measurement: React owns it.
    //
    // Measured again only when something that places the box has changed: its
    // words, the mark's and the visible chart's rectangles — compared by VALUE,
    // since `mark` and `chartBox` are fresh objects on every render — the
    // options and the pointer's line. Recharts re-renders this content on
    // every pointer move over a day (its `coordinate.y` is the pointer's), and
    // each measurement forces two synchronous layouts (review N1).
    const inputs = [shownText.current, at.left, at.top, at.width, at.height, bounds.left, bounds.top, bounds.width, bounds.height, gap, sides.join(), align, keptSweep?.axis, keptSweep?.at].join('|')
    if (position && measuredFor.current === inputs) return
    measuredFor.current = inputs
    const owned = el.style.maxWidth
    el.style.maxWidth = 'none'
    const natural = { width: el.offsetWidth, height: el.offsetHeight }
    const input = { mark: at, bounds, gap, sides, align, sweep: keptSweep }
    const maxWidth = bounds.width > 0 ? tipMaxWidth({ ...input, tip: natural }) : undefined
    el.style.maxWidth = maxWidth !== undefined ? `${maxWidth}px` : ''
    const drawn = { width: el.offsetWidth, height: el.offsetHeight }
    el.style.maxWidth = owned
    const placed = placeTip({ ...input, tip: drawn })
    // Beside the mark on no side, even wrapped: not over the plot at all — in
    // the readout slot below it (when the chart has one).
    const target = placed.fits ? null : slotOf(chart)
    if (target !== slot) setSlot(target)
    const key = `${shownText.current ?? ''}|${keptSweep?.at ?? ''}`
    if (geometry.current?.key !== key) {
      // A new mark, or the box re-placed off a new line: any approach was to the old box.
      armed.current = null
      holding.current = false
    }
    geometry.current = target
      ? null
      : { key, mark: at, tip: { left: placed.left, top: placed.top, ...drawn }, side: placed.side, sweep: keptSweep }
    if (
      !position ||
      placed.left !== position.left ||
      placed.top !== position.top ||
      placed.side !== position.side ||
      placed.fits !== position.fits ||
      maxWidth !== position.maxWidth
    ) {
      setPosition({ left: placed.left, top: placed.top, side: placed.side, fits: placed.fits, maxWidth })
    }
  }, [content, text, last, shown, mark, chartBox, gap, sides, align, keptSweep, position, slot])

  // The approach listener (see above). Attached while there is a box to approach.
  const present = shown !== null
  useEffect(() => {
    const el = box.current
    const chart = present && el ? chartOf(el) : null
    if (!chart) return
    let previous: TipPoint | null = null
    const onMove = (event: MouseEvent) => {
      const origin = chart.getBoundingClientRect()
      const scale = chart.offsetWidth > 0 && origin.width > 0 ? origin.width / chart.offsetWidth : 1
      const point = { x: (event.clientX - origin.left) / scale, y: (event.clientY - origin.top) / scale }
      const from = previous
      previous = point
      const g = geometry.current
      if (!g) {
        armed.current = null
        holding.current = false
        return
      }
      const step = approachStep(armed.current, from, point, g)
      armed.current = step.armed
      holding.current = step.hold
      if (step.suppress) {
        event.stopPropagation()
        return
      }
      const kept = shownText.current
      if (!step.armed && g.sweep && kept !== null && onSweepLine(point, g.tip, g.side, g.sweep)) {
        setLine({ text: kept, sweep: { axis: g.sweep.axis, at: g.sweep.axis === 'x' ? point.y : point.x } })
      }
    }
    const onLeave = () => {
      previous = null
      armed.current = null
      holding.current = false
    }
    chart.addEventListener('mousemove', onMove, true)
    chart.addEventListener('mouseleave', onLeave)
    return () => {
      chart.removeEventListener('mousemove', onMove, true)
      chart.removeEventListener('mouseleave', onLeave)
    }
  }, [present])

  if (!shown) return null

  const inSlot = slot !== null
  const style: CSSProperties = {
    ...TIP_BOX_STYLE,
    position: 'absolute',
    left: position?.left ?? 0,
    top: position?.top ?? 0,
    width: 'max-content',
    maxWidth: position?.maxWidth,
    // Recharts' wrapper is `pointer-events: none` by default; the box itself
    // must be something the pointer can be over (SC 1.4.13, Hoverable).
    pointerEvents: 'auto',
    // Not drawn until it has been placed: never a frame over the mark. In the
    // slot, this one only measures.
    visibility: position && !inSlot ? 'visible' : 'hidden',
  }
  // The attribute NAME comes from `tooltip.ts`: chart-guard reads an object key
  // spelled "tooltip" as an ECharts option, whose value must be an object.
  const marks = inSlot
    ? { 'aria-hidden': true as const }
    : {
        [TOOLTIP_NODE_ATTRIBUTE]: '',
        'data-tip-placement': 'beside',
        'data-tip-side': position?.side,
        'data-tip-fits': position ? String(position.fits) : undefined,
      }

  const beside = (
    <div
      ref={box}
      {...marks}
      className={TIP_CLASS.body}
      style={{ ...TIP_STYLE.body, ...style }}
      // Held (it came from its mark): the pointer is reading it, so the
      // chart must not pick a new mark from where it is. Not held: the move
      // goes on to the chart.
      onMouseMove={(event) => {
        if (!holding.current) return
        event.stopPropagation()
        if (!hovered) setHovered(true)
      }}
      onTouchMove={(event) => {
        if (holding.current) event.stopPropagation()
      }}
      onPointerLeave={() => setHovered(false)}
    >
      <TipRows content={shown} />
    </div>
  )
  if (!inSlot || !slot) return beside

  return (
    <>
      {beside}
      {createPortal(
        <div
          data-chart-tooltip=""
          data-tip-placement="slot"
          data-tip-side={position?.side}
          data-tip-fits="false"
          className={TIP_CLASS.body}
          // The keyboard readout's box, in the same place: in the flow, below
          // the plot, wrapping to the chart's width — and held at the left of
          // whatever scrolls the chart sideways (`sticky`), so a chart wider
          // than a narrow window does not put it out of view.
          style={{
            ...TIP_STYLE.body,
            ...TIP_BOX_STYLE,
            position: 'sticky',
            left: 0,
            width: 'fit-content',
            maxWidth: '100%',
            marginTop: '4px',
            whiteSpace: 'normal',
            overflowWrap: 'anywhere',
          }}
          // Below the plot it covers no mark, so it always holds the pointer.
          // Its events still bubble through the React tree to the chart's
          // own handlers, which would read a pointer down here as a new mark.
          onMouseMove={(event) => {
            event.stopPropagation()
            if (!hovered) setHovered(true)
          }}
          onTouchMove={(event) => event.stopPropagation()}
          onPointerEnter={() => setHovered(true)}
          onPointerLeave={() => setHovered(false)}
        >
          <TipRows content={shown} />
        </div>,
        slot,
      )}
    </>
  )
}

/** The title and rows, for a box that is itself the `[data-chart-tooltip]` element. */
function TipRows({ content }: { content: TooltipContent }) {
  return (
    <>
      {content.title !== undefined && (
        <div className={TIP_CLASS.title} style={TIP_STYLE.title}>
          {content.title}
        </div>
      )}
      {content.rows.map((row, index) => (
        <Row key={rowKey(row, index)} row={row} />
      ))}
    </>
  )
}
