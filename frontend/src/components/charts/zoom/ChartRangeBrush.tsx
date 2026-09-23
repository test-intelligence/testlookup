/**
 * The range brush under a time chart (VIZ-407): a strip spanning the chart's
 * days, where the reader zooms by dragging across it or by moving its two
 * handles — with a pointer or from the keyboard.
 *
 *   - Each handle is a real `role="slider"`: `aria-valuemin/max/now` are day
 *     positions and `aria-valuetext` is the day in words, so a screen reader
 *     says "September 3, 2026", not "12". Arrows move a day, Page Up / Page
 *     Down a week, Home / End to the ends. The two handles cannot cross — each
 *     one's min or max IS the other's value (the APG multi-thumb pattern) —
 *     but they may meet: one day is a valid zoom.
 *   - Dragging across the strip selects a range (mouse and pen). On TOUCH, a
 *     drag across the strip would fight the page's own scroll, so the strip
 *     keeps `touch-action: pan-y` and a finger sliding over it scrolls; the
 *     handles take `touch-action: none` and are what a finger drags.
 *   - Dragging is never the ONLY pointer way (SC 2.5.7): a click — or a tap
 *     — on the strip picks a day, and a second click picks the other end, so
 *     any range is two single presses. The first day is marked on the strip
 *     and the label says the range is waiting for its other end; nothing
 *     zooms until then. A drag, a handle, a key or Reset drops the pending
 *     day.
 *   - The handles sit OUTSIDE the range they bound — the start handle to the
 *     left of its day edge, the end handle to the right of its own — so two
 *     handles one day apart abut instead of overlapping, and each keeps its
 *     whole 24 × 32 px target (SC 2.5.8) however short the range or narrow
 *     the strip.
 *   - A drag is PREVIEWED on the strip and committed on release, so the chart
 *     redraws once per gesture rather than once per pointer event. A key press
 *     commits at once.
 *   - "Reset zoom" exists only while zoomed. It removes itself, so focus moves
 *     to the start handle — still there, still where the reader was working —
 *     rather than falling to the page.
 *   - "Apply as time filter" is offered only where the page asked for it. When
 *     the range cannot become the page window it stays in the tab order,
 *     `aria-disabled`, described by its VISIBLE reason — never applied as some
 *     other window than the one on the strip.
 *
 * No live region here: the frame announces a zoom through the page's one
 * `ChartAnnouncer`. Every colour is a token.
 */
import { useCallback, useId, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { CALENDAR_WORDS, clampRange, isFullRange, type AxisWords, type PromoteDecision, type ZoomRange } from './zoomModel'
import { applyAsWindowLabel } from './windowWords'

export const RESET_ZOOM_LABEL = 'Reset zoom'
export const APPLY_AS_FILTER_LABEL = 'Apply as time filter'
export const BRUSH_HINT =
  'Drag across the strip, click a first and a last day on it, or move its handles, to zoom. The page filters do not change.'

/** Keys that move a handle, and by how many days. */
const STEPS: Record<string, number> = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1, PageDown: -7, PageUp: 7 }

/** Pointer travel, in px, before a press on the strip counts as a drag (and a tap as a swipe). */
const DRAG_THRESHOLD_PX = 3

type Handle = 'start' | 'end'

type Drag =
  | { kind: 'select'; anchor: number; startX: number; moved: boolean; pointerId: number }
  /** A finger on the strip: a tap picks a day; anything that travels is the page's scroll. */
  | { kind: 'tap'; startX: number; startY: number; moved: boolean; pointerId: number }
  | { kind: Handle; pointerId: number }

const BUTTON =
  'inline-flex min-h-6 items-center rounded border border-[var(--color-border-light)] px-2 py-0.5 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)] aria-disabled:cursor-not-allowed aria-disabled:opacity-60'

/** Each handle is placed OUTSIDE its edge (`HANDLE_SIDE`), so two handles never overlap. */
const HANDLE =
  'absolute top-0 z-10 flex h-8 w-6 cursor-ew-resize items-center justify-center gap-0.5 rounded border-2 border-[var(--color-accent-ink)] bg-[var(--color-bg-card)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)] focus-visible:ring-offset-1'
const HANDLE_SIDE: Record<Handle, string> = { start: '-translate-x-full', end: 'translate-x-0' }

const percent = (value: number) => `${value * 100}%`

export interface ChartRangeBrushProps {
  /** The chart's x keys, in axis order. */
  xs: readonly string[]
  /** The zoom, or `null` for the whole axis. */
  range: ZoomRange | null
  /** A new zoom; a whole-axis range means "reset". */
  onRangeChange: (range: ZoomRange | null) => void
  /** The chart's title, which names the control. */
  title: string
  /** How positions are named. Calendar days by default. */
  words?: AxisWords
  /** Offer "Apply as time filter", enabled or with its reason. Omitted → not offered. */
  promote?: PromoteDecision | null
  onPromote?: (days: number) => void
}

export default function ChartRangeBrush({
  xs,
  range,
  onRangeChange,
  title,
  words = CALENDAR_WORDS,
  promote = null,
  onPromote,
}: ChartRangeBrushProps) {
  const hintId = useId()
  const reasonId = useId()
  const count = xs.length
  const last = Math.max(0, count - 1)
  const committed = range ?? { start: 0, end: last }
  const [preview, setPreview] = useState<ZoomRange | null>(null)
  // The day a first click picked, waiting for the click that picks the other end.
  const [picked, setPicked] = useState<number | null>(null)
  const drag = useRef<Drag | null>(null)
  const trackRef = useRef<HTMLDivElement>(null)
  const startHandleRef = useRef<HTMLDivElement>(null)

  const shown = preview ?? committed
  const zoomed = range !== null && !isFullRange(range, count)
  // A picked day the axis no longer has (new data) is simply forgotten.
  const pending = picked !== null && picked <= last ? picked : null

  const commit = useCallback(
    (next: ZoomRange) => {
      const clamped = clampRange(next, count)
      if (clamped === null) return
      onRangeChange(isFullRange(clamped, count) ? null : clamped)
    },
    [count, onRangeChange],
  )

  /** Where on the strip a pointer is, as a fraction 0..1 of its width. */
  const fractionAt = (clientX: number): number => {
    const box = trackRef.current?.getBoundingClientRect()
    if (!box || box.width <= 0) return 0
    return Math.min(1, Math.max(0, (clientX - box.left) / box.width))
  }
  /** The day under the pointer: each day owns an equal slot of the strip. */
  const dayAt = (clientX: number) => Math.min(last, Math.floor(fractionAt(clientX) * count))
  /** The day EDGE nearest the pointer (0..count): where a handle lands. */
  const edgeAt = (clientX: number) => Math.round(fractionAt(clientX) * count)

  const capture = (pointerId: number) => {
    try {
      trackRef.current?.setPointerCapture?.(pointerId)
    } catch {
      // A pointer that is already gone cannot be captured; the drag still works while it is over the strip.
    }
  }

  const onTrackPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || count < 2) return
    if (event.pointerType === 'touch') {
      // Not captured and not prevented: a finger sliding over the strip is
      // the page's scroll (the browser cancels this pointer when it scrolls).
      drag.current = { kind: 'tap', startX: event.clientX, startY: event.clientY, moved: false, pointerId: event.pointerId }
      return
    }
    const anchor = dayAt(event.clientX)
    drag.current = { kind: 'select', anchor, startX: event.clientX, moved: false, pointerId: event.pointerId }
    capture(event.pointerId)
  }

  const onHandlePointerDown = (handle: Handle) => (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return
    // The strip under the handle must not start a selection of its own.
    event.stopPropagation()
    event.preventDefault()
    setPicked(null)
    drag.current = { kind: handle, pointerId: event.pointerId }
    capture(event.pointerId)
    event.currentTarget.focus()
  }

  /**
   * A single press on the strip (SC 2.5.7): the first picks a day, the second
   * picks the other end and zooms to the two (in either order; the same day
   * twice is a one-day zoom).
   */
  const pick = (day: number) => {
    if (pending === null) {
      setPicked(day)
      return
    }
    setPicked(null)
    commit({ start: Math.min(pending, day), end: Math.max(pending, day) })
  }

  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const current = drag.current
    if (!current || current.pointerId !== event.pointerId) return
    if (current.kind === 'tap') {
      if (Math.hypot(event.clientX - current.startX, event.clientY - current.startY) >= DRAG_THRESHOLD_PX) current.moved = true
      return
    }
    if (current.kind === 'select') {
      if (!current.moved && Math.abs(event.clientX - current.startX) < DRAG_THRESHOLD_PX) return
      current.moved = true
      setPicked(null)
      const day = dayAt(event.clientX)
      setPreview({ start: Math.min(current.anchor, day), end: Math.max(current.anchor, day) })
      return
    }
    const edge = edgeAt(event.clientX)
    const base = preview ?? committed
    setPreview(
      current.kind === 'start'
        ? { start: Math.min(Math.max(0, edge), base.end), end: base.end }
        : { start: base.start, end: Math.max(base.start, Math.min(last, edge - 1)) },
    )
  }

  const endDrag = (event: PointerEvent<HTMLDivElement>, cancelled: boolean) => {
    const current = drag.current
    if (!current || current.pointerId !== event.pointerId) return
    drag.current = null
    const next = preview
    setPreview(null)
    if (cancelled) return
    // A press on the strip that never moved (or a tap) is a click: it picks a day.
    if ((current.kind === 'select' || current.kind === 'tap') && !current.moved) {
      pick(dayAt(event.clientX))
      return
    }
    if (next === null) return
    commit(next)
  }

  const onHandleKeyDown = (handle: Handle) => (event: KeyboardEvent<HTMLDivElement>) => {
    setPicked(null)
    const { start, end } = committed
    let next: ZoomRange | null = null
    if (event.key in STEPS) {
      const step = STEPS[event.key]
      next =
        handle === 'start'
          ? { start: Math.min(end, Math.max(0, start + step)), end }
          : { start, end: Math.max(start, Math.min(last, end + step)) }
    } else if (event.key === 'Home') {
      next = handle === 'start' ? { start: 0, end } : { start, end: start }
    } else if (event.key === 'End') {
      next = handle === 'start' ? { start: end, end } : { start, end: last }
    }
    if (next === null) return
    event.preventDefault()
    if (next.start !== start || next.end !== end) commit(next)
  }

  const reset = () => {
    setPicked(null)
    onRangeChange(null)
    // The button removes itself; the start handle is where the reader was.
    startHandleRef.current?.focus()
  }

  const apply = () => {
    if (!promote?.enabled) return
    setPicked(null)
    // The zoom ends with it and these controls may go while the new window
    // loads; the chart body stays, so focus waits there.
    const body = trackRef.current?.closest<HTMLElement>('[data-chart-body]')
    onPromote?.(promote.days)
    body?.focus()
  }

  if (count < 2) return null

  const first = xs[0]
  const lastX = xs[last]
  const selection =
    pending !== null && !preview
      ? `From ${words.full(xs[pending])}: click the day the range ends`
      : zoomed || preview
        ? `Showing ${words.range(xs[shown.start], xs[shown.end])} (${(shown.end - shown.start + 1).toLocaleString('en-US')} of ${count.toLocaleString('en-US')} days)`
        : `Showing all ${count.toLocaleString('en-US')} days`

  const handle = (which: Handle) => {
    const value = which === 'start' ? shown.start : shown.end
    const edge = which === 'start' ? shown.start / count : (shown.end + 1) / count
    return (
      <div
        ref={which === 'start' ? startHandleRef : undefined}
        role="slider"
        tabIndex={0}
        data-chart-brush-handle={which}
        aria-label={which === 'start' ? 'Start of zoom range' : 'End of zoom range'}
        aria-orientation="horizontal"
        // Now, min and max all from the range SHOWN, so a drag preview never
        // reports a value outside its own bounds.
        aria-valuemin={which === 'start' ? 0 : shown.start}
        aria-valuemax={which === 'start' ? shown.end : last}
        aria-valuenow={value}
        aria-valuetext={words.full(xs[value])}
        aria-describedby={hintId}
        onKeyDown={onHandleKeyDown(which)}
        onPointerDown={onHandlePointerDown(which)}
        style={{ left: percent(edge), touchAction: 'none' }}
        className={`${HANDLE} ${HANDLE_SIDE[which]}`}
      >
        <span aria-hidden="true" className="h-3 w-px bg-[var(--color-text-secondary)]" />
        <span aria-hidden="true" className="h-3 w-px bg-[var(--color-text-secondary)]" />
      </div>
    )
  }

  return (
    <div role="group" aria-label={`Zoom ${title}`} data-chart-brush="" className="mt-2 flex flex-col gap-1">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--color-text-secondary)]">
        <p id={hintId} className="m-0">
          {BRUSH_HINT}
        </p>
        {zoomed && (
          <div data-chart-brush-actions="" className="flex flex-wrap items-center gap-2">
            <button type="button" data-chart-zoom-reset="" onClick={reset} className={BUTTON}>
              {RESET_ZOOM_LABEL}
            </button>
            {promote &&
              (promote.enabled ? (
                <button type="button" data-chart-zoom-apply="" onClick={apply} className={BUTTON}>
                  {applyAsWindowLabel(promote.days)}
                </button>
              ) : (
                <>
                  <button
                    type="button"
                    data-chart-zoom-apply=""
                    aria-disabled="true"
                    aria-describedby={reasonId}
                    onClick={apply}
                    className={BUTTON}
                  >
                    {APPLY_AS_FILTER_LABEL}
                  </button>
                  <span id={reasonId} data-chart-zoom-apply-reason="">
                    {promote.reason}
                  </span>
                </>
              ))}
          </div>
        )}
      </div>
      {/* The side padding is a whole handle: each handle sits outside its
          edge, so one at either end of the axis still lies on the strip's row. */}
      <div className="px-6">
        <div
          ref={trackRef}
          data-chart-brush-track=""
          onPointerDown={onTrackPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={(event) => endDrag(event, false)}
          onPointerCancel={(event) => endDrag(event, true)}
          style={{ touchAction: 'pan-y' }}
          className="relative h-8 cursor-crosshair select-none rounded border border-[var(--color-border)] bg-[var(--color-bg-hover)]"
        >
          <div
            aria-hidden="true"
            data-chart-brush-selection=""
            style={{ left: percent(shown.start / count), width: percent((shown.end - shown.start + 1) / count) }}
            className="absolute inset-y-0 border-y-2 border-[var(--color-accent-ink)] bg-[var(--color-bg-card)]"
          />
          {pending !== null && !preview && (
            // The first picked day, waiting for its other end: outlined, not
            // filled, so it does not read as the zoom itself.
            <div
              aria-hidden="true"
              data-chart-brush-picked=""
              style={{ left: percent(pending / count), width: percent(1 / count) }}
              className="absolute inset-y-0 border-2 border-dashed border-[var(--color-accent-ink)]"
            />
          )}
          {handle('start')}
          {handle('end')}
        </div>
      </div>
      <div className="flex items-center justify-between gap-2 px-6 text-xs text-[var(--color-text-secondary)]">
        <span aria-hidden="true">{words.short(first)}</span>
        <span data-chart-brush-selection-label="" className="text-center text-[var(--color-text)]">
          {selection}
        </span>
        <span aria-hidden="true">{words.short(lastX)}</span>
      </div>
    </div>
  )
}
