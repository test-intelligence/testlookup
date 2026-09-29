/**
 * The KEYBOARD CURSOR every Wave-2 chart shares (VIZ-401 / VIZ-402 fix round A).
 *
 * Recharts gives a chart two accessibility affordances and each Wave-2 chart
 * takes both away again:
 *
 *   - its default tooltip is a `role="status" aria-live` region, so a point
 *     under the pointer is spoken. A custom `content` replaces that element
 *     entirely, and the live region goes with it.
 *   - `accessibilityLayer` puts `role="application"` on the drawing surface.
 *     That suppresses the screen reader's virtual cursor — the only way the
 *     reader had to explore the SVG — and Recharts gives the surface no
 *     accessible name, so the reader lands on an unnamed application and the
 *     arrow keys do nothing they can hear.
 *
 * So the chart gets a cursor of its own instead:
 *
 *   surface   ONE focusable element around the plot, `role="group"` with a
 *             name built from the frame's title and the chart type, and a
 *             hint that the arrow keys read the values. No `role="application"`:
 *             nothing here needs the reader's arrow keys taken away from them,
 *             and an unnamed application role is strictly worse than a named
 *             group.
 *   keys      ←/→ (and ↑/↓) move a point, Home/End jump to the ends, Escape
 *             dismisses. Every one of them is `preventDefault`ed only when it
 *             is handled, so Tab still leaves.
 *   speech    the focused point's text goes to the PAGE's one
 *             `ChartAnnouncer` — never a live region per chart (VIZ-105).
 *             It is the result of the reader's own keypress, so it is
 *             announced at once, assertively, like Retry's result.
 *   sight     the same content is drawn in a readout inside the chart body, so
 *             a sighted keyboard user sees what the reader hears. A point that
 *             carries its tooltip CONTENT (VIZ-601: `cursorPoint`) is drawn by
 *             the tooltip's own `ChartTooltipBody` — the same rows, in the same
 *             order, that the pointer's tooltip shows — and its spoken text is
 *             `tooltipText` of that same content. A tooltip that
 *             follows the pointer cannot serve a keyboard at all. The readout
 *             WRAPS — a day's text can run to hundreds of characters (an
 *             anomaly explained, a local-time equivalent), and one truncated
 *             line showed a third of it — and it is rendered BELOW the plot,
 *             in the flow, never laid over the data it describes.
 *   1.4.13    Escape dismisses the HOVER tooltip too (the chart passes
 *             `tipProps` to Recharts' `<Tooltip>`, which makes `active`
 *             controlled), and pointing at the chart again brings it back.
 *             The wrapper also gets `pointer-events: auto`, so the tooltip is
 *             something the pointer can be over at all — Recharts' default
 *             `none` fails Hoverable before the pointer has moved. That is
 *             half of Hoverable; the other half is PINNING, so the tooltip
 *             does not slide away as the pointer approaches it. A chart pins
 *             its own tooltip (it knows where its days are):
 *             `MultiSeriesChart` placed it a fixed offset from the day's x
 *             and held the day while the pointer was on it; VIZ-601 made
 *             that every chart's (`PinnedTip`, `tipPlacement.ts`).
 *   hover     pointing at the chart re-renders NOTHING here: the pointer
 *             handler only lifts a dismissal, and it reads a ref first, so a
 *             pointer sweeping over the chart sets no state at all.
 *
 * Pure UI: it holds an index and some text, fetches nothing and knows nothing
 * about any chart's model.
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from 'react'
import { useChartAnnouncer } from './ChartAnnouncer'
import { ChartTooltipBody, TIP_BOX_STYLE } from './ChartTooltip'
import { tooltipText, type TooltipContent } from './tooltip'

/** One thing the cursor can stop on: a bar, a slice, a day. */
export interface ChartCursorPoint {
  /** Stable identity, for React keys and for tests. */
  key: string
  /** Everything the reader is told about this point, already formatted. */
  text: string
  /**
   * The point's tooltip content (VIZ-601). When present, the readout draws it
   * with the tooltip's own markup, and `text` must be `tooltipText(content)` —
   * `cursorPoint` builds both from the one content, so they cannot differ.
   */
  content?: TooltipContent
}

/** A cursor stop whose spoken text and readout both come from ONE tooltip content. */
export function cursorPoint(key: string, content: TooltipContent): ChartCursorPoint {
  return { key, text: tooltipText(content), content }
}

export interface ChartCursorOptions {
  /** The frame's title: the surface is named after the chart the reader sees. */
  title: string
  /** "Donut chart", "Stacked bar chart", … */
  chartType: string
  /** The points, in the order the arrow keys walk them. */
  points: readonly ChartCursorPoint[]
  /** What one point is called in the hint. Default "value". */
  noun?: string
}

/** The hint every chart's accessible name ends with. Exported for the specs. */
export const CURSOR_HINT = 'Use the arrow keys to read each value.'

export interface ChartCursorSurfaceProps {
  tabIndex: 0
  role: 'group'
  'aria-label': string
  'data-chart-cursor': 'idle' | 'active'
  'data-chart-cursor-index': number
  onKeyDown: (event: ReactKeyboardEvent<HTMLElement>) => void
  onFocus: () => void
  onBlur: () => void
  onPointerMove: () => void
}

export interface ChartCursorTipProps {
  /**
   * `undefined` leaves Recharts in charge (hover works normally); `false`
   * after Escape, which is what makes the tooltip dismissible (SC 1.4.13).
   */
  active: boolean | undefined
  /** Hoverable (SC 1.4.13): the pointer can move onto the tooltip itself. */
  wrapperStyle: CSSProperties
}

export interface ChartCursor {
  /** The focused point, or -1 when the cursor is not on one. */
  index: number
  point: ChartCursorPoint | null
  /** Spread onto the element that WRAPS the plot. */
  surfaceProps: ChartCursorSurfaceProps
  /** Spread onto Recharts' `<Tooltip>`. */
  tipProps: ChartCursorTipProps
  /**
   * `key` for that same `<Tooltip>`. Recharts treats a tooltip that has ever
   * been given an `active` prop as CONTROLLED, and handing it back `undefined`
   * does not hand control back: the tooltip stays hidden however the pointer
   * moves, so one Escape would silently kill hovering for the rest of the
   * page's life. Changing the key remounts it, uncontrolled, the moment the
   * dismissal is lifted.
   */
  tipKey: string
  /** Render inside the chart body: the focused point, for a sighted keyboard user. */
  readout: ReactNode
}

const NEXT = new Set(['ArrowRight', 'ArrowDown'])
const PREVIOUS = new Set(['ArrowLeft', 'ArrowUp'])

/**
 * The cursor for one chart. Call it with the points the chart draws, spread
 * `surfaceProps` on the plot's wrapper, `tipProps` on the `<Tooltip>`, and
 * render `readout` somewhere inside the body.
 */
export function useChartCursor({ title, chartType, points, noun = 'value' }: ChartCursorOptions): ChartCursor {
  const announcer = useChartAnnouncer()
  const [index, setIndex] = useState(-1)
  const [dismissed, setDismissed] = useState(false)
  // Read by the pointer handler, so a pointer sweeping over the chart sets no
  // state (and re-renders nothing) unless there is a dismissal to lift.
  const dismissedRef = useRef(false)
  useEffect(() => {
    dismissedRef.current = dismissed
  }, [dismissed])
  const count = points.length

  // The points changed under the cursor (a page turn, a filter): start over
  // rather than read out whatever now happens to sit at that index.
  const previousCount = useRef(count)
  useEffect(() => {
    if (previousCount.current !== count) {
      previousCount.current = count
      setIndex(-1)
    }
  }, [count])

  const speak = useCallback(
    (next: number) => {
      const point = points[next]
      if (!point) return
      // The reader's OWN action, so it is announced at once — the page's one
      // announcer, never a live region of this chart's own.
      announcer?.assertive(`${title}: ${point.text}`)
    },
    [announcer, points, title],
  )

  const move = useCallback(
    (next: number) => {
      const clamped = Math.min(Math.max(next, 0), count - 1)
      setDismissed(false)
      setIndex(clamped)
      speak(clamped)
    },
    [count, speak],
  )

  const onKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLElement>) => {
      if (count === 0) return
      // Never swallow a modified key: Ctrl+Home is the reader's, not ours.
      if (event.altKey || event.ctrlKey || event.metaKey) return
      const { key } = event
      if (NEXT.has(key)) move(index < 0 ? 0 : index + 1)
      else if (PREVIOUS.has(key)) move(index < 0 ? count - 1 : index - 1)
      else if (key === 'Home') move(0)
      else if (key === 'End') move(count - 1)
      else if (key === 'Escape') {
        // Dismissible (SC 1.4.13) — and focus STAYS, so Escape never costs the
        // reader their place in the page.
        setIndex(-1)
        setDismissed(true)
      } else return
      event.preventDefault()
      event.stopPropagation()
    },
    [count, index, move],
  )

  // Escape must dismiss a tooltip the POINTER opened too, and then focus is
  // somewhere else entirely — so this one listens on the document.
  useEffect(() => {
    const onDocumentKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setDismissed(true)
    }
    document.addEventListener('keydown', onDocumentKeyDown)
    return () => document.removeEventListener('keydown', onDocumentKeyDown)
  }, [])

  const onFocus = useCallback(() => setDismissed(false), [])
  const onBlur = useCallback(() => setIndex(-1), [])
  // Moving the pointer again asks for the tooltip back — and does nothing else.
  const onPointerMove = useCallback(() => {
    if (dismissedRef.current) setDismissed(false)
  }, [])

  const point = index >= 0 ? (points[index] ?? null) : null

  const label = `${title}, ${chartType}, ${count} ${count === 1 ? noun : `${noun}s`}. ${CURSOR_HINT}`

  const surfaceProps = useMemo<ChartCursorSurfaceProps>(
    () => ({
      tabIndex: 0,
      role: 'group',
      'aria-label': label,
      'data-chart-cursor': point ? 'active' : 'idle',
      'data-chart-cursor-index': index,
      onKeyDown,
      onFocus,
      onBlur,
      onPointerMove,
    }),
    [index, label, onBlur, onFocus, onKeyDown, onPointerMove, point],
  )

  const tipProps = useMemo<ChartCursorTipProps>(
    () => ({ active: dismissed ? false : undefined, wrapperStyle: { pointerEvents: 'auto' } }),
    [dismissed],
  )

  const readout = point?.content ? (
    <div
      data-chart-readout=""
      // The tooltip's own box and rows. Wraps; placed by the chart, below its
      // plot, in the flow — never laid over the data it describes.
      className="mt-1 whitespace-normal break-words text-xs text-[var(--color-text)]"
      style={{ ...TIP_BOX_STYLE, width: 'fit-content', maxWidth: '100%' }}
      // Not a live region: the page's one announcer has already said it.
      aria-hidden="true"
    >
      <ChartTooltipBody content={point.content} />
    </div>
  ) : point ? (
    <p
      data-chart-readout=""
      // Wraps: never one truncated line. Placed by the chart, below its plot.
      className="mt-1 whitespace-normal break-words text-xs text-[var(--color-text)]"
      // Not a live region: the page's one announcer has already said it.
      aria-hidden="true"
    >
      {point.text}
    </p>
  ) : null

  return { index, point, surfaceProps, tipProps, tipKey: dismissed ? 'dismissed' : 'live', readout }
}
