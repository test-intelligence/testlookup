/**
 * The test scatter (VIZ-506): one point per test, p95 duration (log x) by
 * failure rate (y), sized by executions, split into quadrants by the medians.
 *
 * Canvas, lazy: this module holds no ECharts code; the scatter engine chunk
 * (the shared base + `ScatterChart`, `MarkLineComponent`, `BrushComponent`)
 * is fetched the first time a scatter mounts.
 *
 * THREE WAYS TO PICK TESTS, ONE RULE (spike S2):
 *   - drag a rectangle ("Drag to select" takes ECharts' global brush cursor;
 *     off by default, so a finger scrolling the page is never a selection);
 *   - "Select slow and flaky": a programmatic brush over the salient quadrant,
 *     the keyboard's way to the same selection;
 *   - "Clear selection".
 * Whatever drew the rectangle, the selected tests are OUR filter over its
 * data range (`pointsInRect`), never ECharts' `brushSelected`. The host lists
 * them (`ScatterSelectionList`), which is also the accessible alternative to
 * the brush.
 *
 * KEYBOARD (VIZ-105 model, as the heatmap): the plot is one focus stop; arrow
 * keys walk the tests in duration order, each highlighted through ECharts'
 * own `highlight` / `showTip` (the same tooltip the mouse gets) and announced
 * through the page's ONE announcer. With `onMarkActivate`, Enter opens the
 * focused test (its rows) and the readout under the plot offers the same as
 * buttons Tab reaches (`MarkActions`). No live region of its own.
 *
 * Hostile names: a test name reaches the DOM only as React text (the list,
 * the buttons) or `domTooltipFormatter` text (the tooltip); the canvas draws
 * no name at all.
 */
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import type { PointsChart } from '@/lib/viz/contracts'
import { useChartTokens } from './tokens'
import { tooltipText } from './tooltip'
import { useFramePlotHeight, usePresentationScale } from './framePlotHeight'
import { usePrefersReducedMotion } from './motion'
import { useChartKeyboard, type NavRequest } from './useChartKeyboard'
import { buildScatterOption, QUADRANT_SYMBOLS, quadrantColor } from './engines/echarts/scatterOption'
import { STALE_BUILD_ACTION, STALE_BUILD_MESSAGE } from './engines/lazyChartEngine'
import { useEChart } from './engines/useEChart'
import { CHART_DRAW_ERROR } from './ChartErrorBoundary'
import { useChartAnnouncer } from './ChartAnnouncer'
import { MarkActions } from './MarkActions'
import {
  availableIntents,
  keyboardIntent,
  pointerIntent,
  type MarkActivationProps,
  type PointerModifiers,
} from './marks'
import {
  QUADRANTS,
  QUADRANT_LABELS,
  QUADRANT_RULE,
  SALIENT_QUADRANT,
  keyboardOrder,
  moveInOrder,
  pointsInRect,
  quadrantCounts,
  quadrantRect,
  rectFromBrushEnd,
  sameRect,
  scatterMark,
  scatterTooltipContent,
  selectionSentence,
  type DataRect,
  type Quadrant,
} from './testScatter.model'

/** How a selection was made: the reader's drag, the quadrant button, or a clear. */
export type ScatterSelectionOrigin = 'brush' | 'quadrant' | 'clear'

export interface TestScatterProps extends MarkActivationProps {
  data: PointsChart
  /** One sentence for assistive tech (`scatterDescription`). */
  description: string
  width?: number | string
  height?: number
  animate?: boolean
  /**
   * The selected tests (indices into `data.points`, data order), or `null`
   * when nothing is selected. Called on the reader's own action only; a new
   * `data` clears the selection without a call (the host keys its own copy
   * on `data`).
   */
  onSelectionChange?: (indices: number[] | null, origin: ScatterSelectionOrigin) => void
}

export const SCATTER_KEYBOARD_HINT = 'Arrow keys walk the tests from fastest to slowest, Escape clears, Tab leaves'
export const SCATTER_ACTIVATE_HINT = 'Enter opens the focused test'
export const SELECT_SALIENT_LABEL = 'Select slow and flaky'
export const DRAG_SELECT_LABEL = 'Drag to select'
export const CLEAR_SELECTION_LABEL = 'Clear selection'
export const NO_SALIENT_MESSAGE = 'No test is slow and flaky: none is above both medians.'

const BUTTON =
  'min-h-6 rounded border border-[var(--color-border-light)] px-3 py-1 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]'
const PRESSED = 'bg-[var(--color-bg-hover)] font-medium'

/** What the toolbar, key, hint and mark buttons around the plot keep back in full screen, px. */
const SCATTER_NOTES_RESERVE = 120

/** The brush cursor on (a rectangle) or off. */
const takeCursor = (on: boolean) => ({
  type: 'takeGlobalCursor',
  key: 'brush',
  brushOption: on ? { brushType: 'rect', brushMode: 'single' } : { brushType: false },
})

/** A programmatic brush over `rect` (data space), or none. */
const brushAction = (rect: DataRect | null) => ({
  type: 'brush',
  areas: rect ? [{ brushType: 'rect', xAxisIndex: 0, yAxisIndex: 0, coordRange: [[...rect.x], [...rect.y]] }] : [],
})

/** A click's modifier keys, read from ECharts' params without trusting their shape. */
function modifiersOf(params: unknown): PointerModifiers {
  const native = (params as { event?: { event?: unknown } } | null)?.event?.event as Partial<PointerEvent> | undefined
  if (!native || typeof native !== 'object') return {}
  return {
    shiftKey: Boolean(native.shiftKey),
    ctrlKey: Boolean(native.ctrlKey),
    metaKey: Boolean(native.metaKey),
    pointerType: typeof native.pointerType === 'string' ? native.pointerType : undefined,
  }
}

function dataIndexOf(params: unknown): number | null {
  const index = (params as { dataIndex?: unknown; seriesIndex?: unknown } | null)?.dataIndex
  const series = (params as { seriesIndex?: unknown } | null)?.seriesIndex
  return typeof index === 'number' && Number.isInteger(index) && (series === undefined || series === 0) ? index : null
}

/** The key under the plot: each quadrant's shape, colour, name and count — the colour is never alone. */
function QuadrantKey({ data, colorOf }: { data: PointsChart; colorOf: (q: Quadrant) => string }) {
  const counts = quadrantCounts(data)
  return (
    <div className="pt-2 text-xs text-[var(--color-text-secondary)]">
      <ul data-scatter-key="" className="flex flex-wrap items-center gap-x-4 gap-y-1">
        {QUADRANTS.map((q) => (
          <li key={q} data-quadrant={q} className="flex items-center gap-1.5">
            <svg width={12} height={12} viewBox="0 0 12 12" aria-hidden="true" focusable="false">
              <QuadrantShape quadrant={q} fill={colorOf(q)} />
            </svg>
            <span>
              {QUADRANT_LABELS[q]} ({counts[q]})
            </span>
          </li>
        ))}
        <li className="flex items-center gap-1.5">
          <svg width={18} height={12} aria-hidden="true" focusable="false">
            <line x1={1} y1={6} x2={17} y2={6} stroke="currentColor" strokeDasharray="3 2" />
          </svg>
          <span>Medians</span>
        </li>
      </ul>
      <p className="mt-1">{QUADRANT_RULE}</p>
    </div>
  )
}

/** The same four shapes the canvas draws (`QUADRANT_SYMBOLS`), at 12 px. */
function QuadrantShape({ quadrant, fill }: { quadrant: Quadrant; fill: string }) {
  switch (QUADRANT_SYMBOLS[quadrant]) {
    case 'triangle':
      return <polygon points="6,1 11,11 1,11" fill={fill} />
    case 'diamond':
      return <polygon points="6,0 12,6 6,12 0,6" fill={fill} />
    case 'rect':
      return <rect x={1} y={1} width={10} height={10} fill={fill} />
    case 'circle':
      return <circle cx={6} cy={6} r={5} fill={fill} />
  }
}

export default function TestScatter({
  data,
  description,
  width = '100%',
  height: requestedHeight = 320,
  animate = false,
  onSelectionChange,
  onMarkActivate,
  markIntents,
}: TestScatterProps) {
  const height = useFramePlotHeight(requestedHeight, SCATTER_NOTES_RESERVE)
  const tokens = useChartTokens()
  const reducedMotion = usePrefersReducedMotion()
  const textScale = usePresentationScale()
  const hintId = useId()
  // The selection's rectangle and the drag mode outlive a re-applied option (a
  // theme switch replaces it with `notMerge`, which drops the brush); a new
  // `data` clears both (adjusted during render, not in an effect).
  const [rect, setRect] = useState<DataRect | null>(null)
  const [dragging, setDragging] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [seenData, setSeenData] = useState(data)
  if (seenData !== data) {
    setSeenData(data)
    setRect(null)
    setNotice(null)
  }
  // Drag mode makes the tooltip non-enterable (a tooltip that takes the pointer stalls a drag across it);
  // the re-applied option gets the brush cursor and the rectangle back (the effect below).
  const option = useMemo(
    () => buildScatterOption({ data, tokens, animate: animate && !reducedMotion, textScale, brushing: dragging }),
    [data, tokens, animate, reducedMotion, textScale, dragging],
  )

  const announcer = useChartAnnouncer()
  // The rectangle as last chosen, read by the event handlers and the
  // re-apply effect; written at once (not after a render), so a `brushEnd`
  // that a programmatic brush might raise sees the rectangle it came from.
  const rectRef = useRef<DataRect | null>(rect)
  const select = useCallback(
    (next: DataRect | null, origin: ScatterSelectionOrigin) => {
      rectRef.current = next
      setRect(next)
      setNotice(null)
      const indices = next ? pointsInRect(data.points, next) : null
      onSelectionChange?.(indices, origin)
      // The reader's own action: said at once, through the page's announcer.
      announcer?.assertive(selectionSentence(indices?.length ?? 0))
    },
    [data, onSelectionChange, announcer],
  )

  const markProps = useMemo(() => ({ onMarkActivate, markIntents }), [onMarkActivate, markIntents])
  const events = useMemo(
    () => ({
      // A drag ended: OUR filter over the rectangle's data range (spike S2).
      // The rectangle already chosen (our own programmatic brush, re-drawn)
      // is not a new selection: nothing is reported or announced twice.
      brushEnd: (params: unknown) => {
        const next = rectFromBrushEnd(params)
        if (!sameRect(next, rectRef.current)) select(next, 'brush')
      },
      click: (params: unknown) => {
        const index = dataIndexOf(params)
        const mark = index === null ? null : scatterMark(data, index)
        if (!mark || !markProps.onMarkActivate) return
        const intent = pointerIntent(availableIntents(mark, markProps), modifiersOf(params))
        if (intent) markProps.onMarkActivate(mark, intent)
      },
    }),
    [select, data, markProps],
  )
  const { containerRef, status, retry, getInstance } = useEChart('scatter', option, events)
  const dispatch = useCallback(
    (payload: { type: string; [key: string]: unknown }) => getInstance()?.dispatchAction?.(payload),
    [getInstance],
  )

  // After every (re)applied option: the drag mode and the drawn rectangle
  // back on the new one. Runs after `useEChart`'s own option effect.
  const draggingRef = useRef(dragging)
  useEffect(() => {
    rectRef.current = rect
    draggingRef.current = dragging
  })
  useEffect(() => {
    if (status !== 'ready') return
    if (draggingRef.current) dispatch(takeCursor(true))
    if (rectRef.current) dispatch(brushAction(rectRef.current))
  }, [option, status, dispatch])

  const toggleDrag = useCallback(() => {
    const next = !dragging
    setDragging(next)
    dispatch(takeCursor(next))
  }, [dragging, dispatch])

  const selectSalient = useCallback(() => {
    const target = quadrantRect(data, SALIENT_QUADRANT)
    if (!target) {
      select(null, 'quadrant')
      dispatch(brushAction(null))
      setNotice(NO_SALIENT_MESSAGE)
      announcer?.assertive(NO_SALIENT_MESSAGE)
      return
    }
    select(target, 'quadrant')
    dispatch(brushAction(target))
  }, [data, dispatch, select, announcer])

  const clear = useCallback(() => {
    select(null, 'clear')
    dispatch(brushAction(null))
  }, [dispatch, select])

  // The keyboard walk.
  const order = useMemo(() => keyboardOrder(data.points), [data])
  const move = useCallback((current: number | null, request: NavRequest) => moveInOrder(order, current, request), [order])
  const describe = useCallback((index: number) => tooltipText(scatterTooltipContent(data, index)), [data])
  const onActivate = useCallback(
    (index: number) => {
      dispatch({ type: 'highlight', seriesIndex: 0, dataIndex: index })
      dispatch({ type: 'showTip', seriesIndex: 0, dataIndex: index })
      announcer?.assertive(describe(index))
    },
    [dispatch, announcer, describe],
  )
  const onClear = useCallback(
    (index: number) => {
      dispatch({ type: 'downplay', seriesIndex: 0, dataIndex: index })
      dispatch({ type: 'hideTip' })
    },
    [dispatch],
  )
  const activatable = Boolean(onMarkActivate)
  const onEnter = useCallback(
    (index: number, { shiftKey }: { shiftKey: boolean }) => {
      const mark = scatterMark(data, index)
      if (!mark || !markProps.onMarkActivate) return
      const intent = keyboardIntent(availableIntents(mark, markProps), { shiftKey })
      if (intent) markProps.onMarkActivate(mark, intent)
    },
    [data, markProps],
  )
  const {
    containerRef: keyboardRef,
    containerProps,
    activeIndex,
  } = useChartKeyboard({ count: data.points.length, move, onActivate, onClear, describe, onEnter: activatable ? onEnter : undefined })

  const plotOf = useCallback(() => keyboardRef.current, [keyboardRef])
  const activeMark = activeIndex === null ? null : scatterMark(data, activeIndex)
  const activeIntents = activeMark ? availableIntents(activeMark, markProps) : []
  const colorOf = useCallback((q: Quadrant) => quadrantColor(q, tokens), [tokens])

  if (status === 'stale-build' || status === 'error') {
    // Static text: the frame, not a live region, owns how a state change is announced.
    return (
      <div
        data-chart-draw-error={status}
        style={{ width, height }}
        className="flex flex-col items-center justify-center gap-2 text-sm text-[var(--color-text-secondary)]"
      >
        {status === 'stale-build' ? (
          <>
            <span>{STALE_BUILD_MESSAGE}</span>
            <button type="button" onClick={() => window.location.reload()} className={BUTTON}>
              {STALE_BUILD_ACTION}
            </button>
          </>
        ) : (
          <>
            <span>{CHART_DRAW_ERROR}</span>
            <button type="button" onClick={retry} className={BUTTON}>
              Try again
            </button>
          </>
        )}
      </div>
    )
  }

  const hint = activatable ? `${SCATTER_KEYBOARD_HINT}. ${SCATTER_ACTIVATE_HINT}.` : `${SCATTER_KEYBOARD_HINT}.`
  return (
    <div data-test-scatter="" style={{ width }}>
      <div role="toolbar" aria-label="Select tests" className="mb-2 flex flex-wrap items-center gap-2">
        <button type="button" data-scatter-action="salient" onClick={selectSalient} className={BUTTON}>
          {SELECT_SALIENT_LABEL}
        </button>
        <button
          type="button"
          data-scatter-action="drag"
          aria-pressed={dragging}
          onClick={toggleDrag}
          className={dragging ? `${BUTTON} ${PRESSED}` : BUTTON}
        >
          {DRAG_SELECT_LABEL}
        </button>
        {rect !== null ? (
          <button type="button" data-scatter-action="clear" onClick={clear} className={BUTTON}>
            {CLEAR_SELECTION_LABEL}
          </button>
        ) : null}
        {notice ? (
          <span data-scatter-notice="" className="text-xs text-[var(--color-text-secondary)]">
            {notice}
          </span>
        ) : null}
      </div>
      <div
        ref={keyboardRef}
        {...containerProps}
        role="group"
        aria-label={description}
        aria-describedby={hintId}
        data-chart-keyboard="scatter"
        data-active-index={activeIndex ?? ''}
        className="rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
      >
        <div
          ref={containerRef}
          data-chart-engine="echarts"
          data-chart-type="scatter"
          data-chart-status={status}
          style={{ width: '100%', height }}
        />
        {activeMark && onMarkActivate ? (
          // The plot takes focus before the action: the rows panel it opens
          // returns focus there on close (this button goes with the point).
          <MarkActions mark={activeMark} intents={activeIntents} onActivate={onMarkActivate} returnFocus={plotOf} />
        ) : null}
      </div>
      <p id={hintId} data-chart-keyboard-hint="" className="pt-1 text-xs text-[var(--color-text-secondary)]">
        {hint}
      </p>
      <QuadrantKey data={data} colorOf={colorOf} />
    </div>
  )
}
