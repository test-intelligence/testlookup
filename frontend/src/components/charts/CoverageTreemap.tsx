/**
 * The coverage treemap renderer (VIZ-502): one level of the coverage map,
 * drawn by ECharts on a canvas, lazily loaded (this module holds no engine
 * code; the treemap chunk is fetched the first time one mounts).
 *
 * ACCESSIBLE (VIZ-105). ECharts has no keyboard model for a treemap, so the
 * wrapper is one focus stop and the kit's `useChartKeyboard` walks the
 * children in the order they are drawn (test count, largest first): the
 * arrow keys move, Home / End jump, Escape clears. The focused node is shown
 * with ECharts' own `highlight` + `showTip` (spike S1: child k is `dataIndex`
 * k + 1, never addressed by its untrusted, possibly repeated name), and its
 * tooltip text is spoken through the page's one announcer. Enter opens it (a
 * suite's classes, a class's tests) or, for a test, its executions;
 * Backspace goes up a level.
 *
 * Nothing is shortcut-only or hover-only (WCAG 2.1.1, 1.4.13): the row under
 * the plot names the focused node and offers the same actions as buttons, and
 * otherwise says which keys do what. The frame's table view is the full
 * equivalent of the canvas.
 *
 * A pointer click activates the node it lands on (`pointerIntent`: a plain
 * click is the node's first action; a touch tap only selects it, and the row
 * below offers the actions). Without `onMarkActivate` nothing listens.
 *
 * Colour is never alone: a gap (not run in the window, last run unknown,
 * never run) is a pattern, named in the legend, the tooltip and the table.
 */
import { forwardRef, useCallback, useId, useMemo, useRef, useState, type KeyboardEvent, type Ref } from 'react'
import { STALE_BUILD_ACTION, STALE_BUILD_MESSAGE } from './engines/lazyChartEngine'
import { useEChart } from './engines/useEChart'
import { buildTreemapOption, nodeFill, treemapTarget } from './engines/echarts/treemapOption'
import { CHART_DRAW_ERROR } from './ChartErrorBoundary'
import { useChartAnnouncer } from './ChartAnnouncer'
import { CHART_VARS, DECAL_TILE, SERIES_COUNT, useChartTokens, type DecalKind } from './tokens'
import { tooltipText } from './tooltip'
import { useFramePlotHeight, usePresentationScale } from './framePlotHeight'
import { usePrefersReducedMotion } from './motion'
import { useChartKeyboard, type NavRequest } from './useChartKeyboard'
import {
  availableIntents,
  intentLabel,
  keyboardIntent,
  pointerIntent,
  type MarkActivationProps,
  type MarkIntent,
} from './marks'
import {
  BIN_RAMP_STEPS,
  childIntents,
  childMark,
  COLOR_BINS,
  colorByLabel,
  GAP_DECAL,
  GAP_WORDS,
  moveInOrder,
  nodeTooltip,
  tileValueNote,
  treemapClick,
  treemapKeyboardHint,
  type CoverageChild,
  type CoverageColorBy,
  type CoverageGap,
  type CoverageLevel,
} from './coverageMap.model'

export interface CoverageTreemapProps extends MarkActivationProps {
  /** The level's children, in the server's order. */
  items: readonly CoverageChild[]
  level: CoverageLevel
  colorBy: CoverageColorBy
  /** One sentence for assistive tech; ECharts' generated one is off. */
  description: string
  /** Canvas height, px (grows to the frame's body in full screen). */
  height?: number
  animate?: boolean
  /** Backspace: up one level. Absent at the top. */
  onLevelUp?: () => void
}

const BUTTON =
  'inline-flex min-h-6 items-center rounded border border-[var(--color-border-light)] px-2 py-0.5 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]'

/** What the action row and the legend keep back from the canvas in full screen, px. */
const TREEMAP_NOTES_RESERVE = 108 // + the legend's tile-value line (F-03)

function assign<T>(ref: Ref<T> | undefined, value: T | null) {
  if (typeof ref === 'function') ref(value)
  else if (ref) (ref as { current: T | null }).current = value
}

/** A legend swatch's pattern: the card colour with the gap's marks in the axis colour, as the canvas draws it. */
function GapPattern({ id, kind }: { id: string; kind: DecalKind }) {
  const t = DECAL_TILE
  return (
    <pattern
      id={id}
      width={t}
      height={t}
      patternUnits="userSpaceOnUse"
      patternTransform={kind === 'dots' ? undefined : 'rotate(45)'}
      data-coverage-gap-pattern={kind}
    >
      <rect width={t} height={t} fill={CHART_VARS.card} />
      {kind === 'dots' ? <circle cx={t / 2} cy={t / 2} r={1.5} fill={CHART_VARS.axis} /> : null}
      {kind === 'diagonal' || kind === 'crosshatch' ? <rect width={2} height={t} fill={CHART_VARS.axis} /> : null}
      {kind === 'crosshatch' ? <rect width={t} height={2} fill={CHART_VARS.axis} /> : null}
    </pattern>
  )
}

function Swatch({ fill }: { fill: string }) {
  return (
    <svg width={14} height={14} aria-hidden="true" focusable="false">
      <rect x={0.5} y={0.5} width={13} height={13} rx={2} fill={fill} stroke={CHART_VARS.border} />
    </svg>
  )
}

const GAP_ORDER: readonly CoverageGap[] = ['not_run', 'unknown', 'never']

/**
 * The legend: the measure's five bins with their edges (a fixed scale, so
 * every bin is listed whether or not a node falls in it), then each gap
 * pattern the level actually draws, then the combined "Other" grey.
 */
export function CoverageLegend({ items, colorBy }: { items: readonly CoverageChild[]; colorBy: CoverageColorBy }) {
  const prefix = `cg${useId().replace(/[^A-Za-z0-9_-]/g, '')}`
  const tokens = useChartTokens()
  const { gaps, other } = useMemo(() => {
    const found = new Set<CoverageGap>()
    let grey = false
    for (const item of items) {
      const fill = nodeFill(item, colorBy, tokens)
      if (fill.gap) found.add(fill.gap)
      else if (fill.bin === null) grey = true
    }
    return { gaps: GAP_ORDER.filter((gap) => found.has(gap)), other: grey }
  }, [items, colorBy, tokens])
  return (
    <div data-coverage-legend="" className="pt-2 text-xs text-[var(--color-text-secondary)]">
      {gaps.length > 0 ? (
        <svg width={0} height={0} aria-hidden="true" focusable="false" className="absolute">
          <defs>
            {gaps.map((gap) => (
              <GapPattern key={gap} id={`${prefix}-${gap}`} kind={GAP_DECAL[gap]} />
            ))}
          </defs>
        </svg>
      ) : null}
      <ul aria-label={`${colorByLabel(colorBy)}: colour key`} className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1">
        {COLOR_BINS[colorBy].map((bin, i) => (
          <li key={bin.label} data-coverage-bin={i} className="flex items-center gap-1.5">
            <Swatch fill={CHART_VARS.seq[BIN_RAMP_STEPS[i]]} />
            <span>{bin.label}</span>
          </li>
        ))}
        {gaps.map((gap) => (
          <li key={gap} data-coverage-gap={gap} className="flex items-center gap-1.5">
            <Swatch fill={`url(#${prefix}-${gap})`} />
            <span>{GAP_WORDS[gap]}</span>
          </li>
        ))}
        {other ? (
          <li data-coverage-other="" className="flex items-center gap-1.5">
            <Swatch fill={CHART_VARS.series[SERIES_COUNT - 1]} />
            <span>Combined (no pass rate)</span>
          </li>
        ) : null}
      </ul>
      {/* F-03: the value is printed on the tile too, so a neighbouring bin is never told apart by colour alone. */}
      <p data-coverage-tile-value="" className="mt-1 text-center">
        {tileValueNote(colorBy)}
      </p>
    </div>
  )
}

const CoverageTreemap = forwardRef<HTMLDivElement, CoverageTreemapProps>(function CoverageTreemap(
  { items, level, colorBy, description, height: requestedHeight = 360, animate = false, onLevelUp, onMarkActivate, markIntents },
  ref,
) {
  const height = useFramePlotHeight(requestedHeight, TREEMAP_NOTES_RESERVE)
  const tokens = useChartTokens()
  const reducedMotion = usePrefersReducedMotion()
  const textScale = usePresentationScale()
  const hintId = useId()
  const option = useMemo(
    () => buildTreemapOption({ children: items, colorBy, tokens, animate: animate && !reducedMotion, textScale }),
    [items, colorBy, tokens, animate, reducedMotion, textScale],
  )

  const marks = useMemo(() => items.map((item) => childMark(item, level)), [items, level])
  const intentsOf = useCallback(
    (index: number): MarkIntent[] => {
      const item = items[index]
      // The Other node and an id this level did not issue: nothing to open.
      if (!item || !onMarkActivate || item.key === null) return []
      return markIntents ? availableIntents(marks[index], { onMarkActivate, markIntents }) : childIntents(item)
    },
    [items, marks, onMarkActivate, markIntents],
  )
  const activate = useCallback(
    (index: number, intent: MarkIntent | null) => {
      if (intent && onMarkActivate && marks[index]) onMarkActivate(marks[index], intent)
    },
    [marks, onMarkActivate],
  )
  // The action row's buttons: focus back on the chart FIRST (as HeatmapChart's
  // `onButton`). A side panel the action opens returns focus to whatever had
  // it, and the button goes when the chart loses focus to that panel.
  const keyboardNode = useRef<HTMLDivElement | null>(null)
  const activateFromButton = useCallback(
    (index: number, intent: MarkIntent) => {
      keyboardNode.current?.focus()
      activate(index, intent)
    },
    [activate],
  )

  // A touch tap selects a node without acting (a finger has no modifier to
  // choose an action with); the row below then offers its actions. Kept with
  // the items it was made on, so a new level forgets it.
  const [tapped, setTapped] = useState<{ items: readonly CoverageChild[]; index: number } | null>(null)
  const events = useMemo(
    () => ({
      click: (params: unknown) => {
        const hit = treemapClick(params)
        if (!hit || hit.index >= items.length) return
        const intents = intentsOf(hit.index)
        const intent = pointerIntent(intents, hit.modifiers)
        if (intent) activate(hit.index, intent)
        else if (intents.length > 0) setTapped({ items, index: hit.index })
      },
    }),
    [items, intentsOf, activate],
  )
  const { containerRef, instanceRef, status, retry } = useEChart('treemap', option, onMarkActivate ? events : undefined)

  const dispatch = useCallback(
    (payload: { type: string; [key: string]: unknown }) => instanceRef.current?.dispatchAction?.(payload),
    [instanceRef],
  )
  const announcer = useChartAnnouncer()
  const describe = useCallback(
    (index: number) => {
      const item = items[index]
      return item ? `${tooltipText(nodeTooltip(item))}. ${index + 1} of ${items.length}.` : ''
    },
    [items],
  )
  const onActivate = useCallback(
    (index: number) => {
      const target = treemapTarget(index)
      dispatch({ type: 'highlight', ...target })
      dispatch({ type: 'showTip', ...target })
      // The reader's own key press: said at once, through the page's one announcer.
      announcer?.assertive(describe(index))
    },
    [dispatch, announcer, describe],
  )
  const onClear = useCallback(
    (index: number) => {
      dispatch({ type: 'downplay', ...treemapTarget(index) })
      dispatch({ type: 'hideTip' })
    },
    [dispatch],
  )
  const move = useCallback(
    (current: number | null, request: NavRequest) => moveInOrder(items.length, current, request),
    [items.length],
  )
  const onEnter = useCallback(
    (index: number, { shiftKey }: { shiftKey: boolean }) => activate(index, keyboardIntent(intentsOf(index), { shiftKey })),
    [activate, intentsOf],
  )
  // Destructured: react-hooks/refs treats a whole object that holds a ref as a ref.
  // No live region of its own (one page announcer): `onActivate` speaks.
  const {
    containerRef: keyboardRef,
    containerProps,
    activeIndex,
  } = useChartKeyboard({
    count: items.length,
    move,
    onActivate,
    onClear,
    describe,
    onEnter: onMarkActivate ? onEnter : undefined,
  })
  const tappedIndex = tapped && tapped.items === items ? tapped.index : null
  const focused = activeIndex ?? tappedIndex

  const { onKeyDown: keyboardKeyDown, onBlur, tabIndex } = containerProps
  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) => {
      if (event.key === 'Backspace' && onLevelUp && event.target === event.currentTarget) {
        event.preventDefault()
        onLevelUp()
        return
      }
      keyboardKeyDown(event)
    },
    [onLevelUp, keyboardKeyDown],
  )
  const setRefs = useCallback(
    (node: HTMLDivElement | null) => {
      keyboardRef.current = node
      keyboardNode.current = node
      assign(ref, node)
    },
    [keyboardRef, ref],
  )

  if (status === 'stale-build' || status === 'error') {
    // Static text: the frame, not a live region, owns how a state change is announced.
    return (
      <div
        data-chart-draw-error={status}
        style={{ height }}
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

  const focusedItem = focused === null ? undefined : items[focused]
  const focusedIntents = focused === null ? [] : intentsOf(focused)
  const hint = treemapKeyboardHint(level)

  return (
    <div>
      <div
        ref={setRefs}
        tabIndex={tabIndex}
        onBlur={onBlur}
        onKeyDown={onKeyDown}
        role="group"
        aria-label={description}
        aria-describedby={hintId}
        data-chart-keyboard="treemap"
        data-active-index={activeIndex ?? ''}
        className="rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
      >
        <div
          ref={containerRef}
          data-chart-engine="echarts"
          data-chart-type="treemap"
          data-chart-status={status}
          style={{ width: '100%', height }}
        />
        {/* The action row: the focused node and its actions, else the keys in words. */}
        <div data-coverage-readout="" className="flex min-h-8 flex-wrap items-center gap-2 pt-2 text-xs text-[var(--color-text-secondary)]">
          {focusedItem && focused !== null ? (
            <>
              <span data-coverage-focused="" className="min-w-0 max-w-full truncate font-medium text-[var(--color-text)]">
                {focusedItem.node.label}
              </span>
              {focusedIntents.map((intent) => (
                <button
                  key={intent}
                  type="button"
                  data-mark-intent={intent}
                  className={`${BUTTON} max-w-full`}
                  onClick={() => activateFromButton(focused, intent)}
                >
                  <span className="truncate">{intentLabel(intent, marks[focused])}</span>
                </button>
              ))}
              <span id={hintId} className="sr-only">
                {hint}
              </span>
            </>
          ) : (
            <span id={hintId} data-chart-keyboard-hint="">
              {hint}
            </span>
          )}
        </div>
      </div>
      <CoverageLegend items={items} colorBy={colorBy} />
    </div>
  )
})

export default CoverageTreemap
