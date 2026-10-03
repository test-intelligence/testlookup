/**
 * The drawing every circle view of VIZ-504 shares: failure-group bubbles,
 * related groups and systemic clusters. A layout (pack or force) is handed in
 * as a function of the plot box; this draws it as React SVG — `<circle>` and
 * `<text>`, text from React children only, never markup built from a name —
 * and gives it ONE keyboard path:
 *
 *   one tab stop   the plot is a single focus stop, not 200 (plan 3.3.3)
 *   arrows         move through the circles in RANK order (`useChartKeyboard`);
 *                  each move is said through the page's one announcer
 *   Enter          the first intent on offer for the focused circle
 *                  (Shift+Enter: `filter` where offered), as every Wave-3 chart
 *   Escape         clears the highlight; Tab leaves (to the readout's buttons)
 *
 * What a circle IS appears in the READOUT beside the plot (below it on a narrow
 * frame) — the same `TooltipContent` the announcer speaks — for the hovered
 * circle, else the keyboard's, else the one a touch selected. It sits in the
 * flow, never over the plot, so it covers nothing, stays while the reader reads
 * it, and goes when they move on (WCAG 1.4.13). Its buttons ("Drill into …",
 * "View rows") make every intent reachable without a pointer or a shortcut.
 *
 * A circle's category is its pattern AND its colour (`categoryPatternSpecs`),
 * and the readout names it in words. Every circle is at least 24 x 24 px.
 */
import { useCallback, useId, useMemo, useState, type MouseEvent } from 'react'
import { useChartAnnouncer } from '../ChartAnnouncer'
import { ChartTooltipBody } from '../ChartTooltipBody'
import { useContainerWidth } from '../chartLayout'
import { usePresentationScale } from '../framePlotHeight'
import MarkActions, { MARK_BUTTON_LABEL_MAX } from '../MarkActions'
import {
  availableIntents,
  keyboardIntent,
  pointerIntent,
  type ChartMark,
  type MarkActivationProps,
  type MarkIntent,
} from '../marks'
import { patternFill, renderPatterns, useChartPatternPrefix } from '../patterns'
import { useTextMeasure } from '../textMeasure'
import { CHART_VARS } from '../tokens'
import { tooltipText } from '../tooltip'
import { useChartKeyboard } from '../useChartKeyboard'
import { categoryPatternId, categoryPatternSpecs, type FailureCategoryKey } from './categoryPatterns'
import {
  fitLabel,
  plotBox,
  rankWalk,
  READOUT_WIDTH,
  wordTruncate,
  type PlotBox,
  type PlotItem,
  type PlotLayout,
  type PlotLink,
  type PlotNode,
} from './plot.model'

export type { PlotBox, PlotItem, PlotLayout, PlotLink, PlotNode } from './plot.model'

export interface GroupPlotProps extends MarkActivationProps {
  /** `data-group-plot`, for specs and styles. */
  kind: 'bubbles' | 'relations' | 'clusters'
  /** In keyboard (rank) order. Items without a node are not drawn and not walked. */
  items: readonly PlotItem[]
  /** The layout for a plot box. Called again only when the box or this function changes. */
  layout: (box: PlotBox) => PlotLayout
  /** `square`: the plot is as wide as it is high (a pack); `wide`: it takes the width it is given. */
  shape: 'square' | 'wide'
  /** The plot's height, px. */
  height: number
  /** The plot's accessible name: what it shows, and how many. */
  description: string
  /** How many of the first items get their label drawn on the circle. */
  labelled?: number
  /** The item whose details are open (drawn with a dashed ring). */
  selectedId?: string | null
  /** The container's width, px, when the caller knows it (tests, the gallery); measured otherwise. */
  width?: number
  /** Said in the readout while nothing is focused or hovered. */
  idleText?: string
}

const SURFACE =
  'group relative flex min-w-0 gap-4 rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]'
const BESIDE = 'flex-row items-start'
const UNDER = 'flex-col'

const LABEL_FONT = 11
/** A circle narrower than this gets no label (the readout and the table name it). */
const MIN_LABELLED_RADIUS = 18
/** A label may use this share of its circle's diameter; a whole label the cut would barely shorten may use the circle's width. */
const LABEL_BAND = 0.8
const LABEL_WHOLE = 0.95

export const GROUP_PLOT_HINT = 'Arrow keys move, Enter opens, Escape clears, Tab leaves'
/**
 * Space kept under the plot for the keyboard hint chip, px. The chip is drawn
 * under the plot only while it has keyboard focus (`absolute top-full`); with
 * the gap below the plot this keeps the chip's line clear, so it never covers
 * what comes next — the table's caption, a legend (R2-B F-21).
 */
export const GROUP_PLOT_HINT_RESERVE = 20

/** A rough width when nothing can be measured (jsdom, a hidden tab): wide enough for DejaVu. */
const roughMeasure = (text: string, fontSize: number) => [...text].length * fontSize * 0.62

export default function GroupPlot({
  kind,
  items,
  layout,
  shape,
  height,
  description,
  labelled = 5,
  selectedId = null,
  width: fixedWidth,
  idleText = 'Point at a circle, or focus the chart and use the arrow keys, to read a group here.',
  onMarkActivate,
  markIntents,
}: GroupPlotProps) {
  const hintId = useId()
  const prefix = useChartPatternPrefix()
  const [measureWidth, measuredWidth] = useContainerWidth<HTMLDivElement>()
  const [measureText, measure] = useTextMeasure<HTMLDivElement>()
  const attach = useCallback(
    (node: HTMLDivElement | null) => {
      measureWidth(node)
      measureText(node)
    },
    [measureWidth, measureText],
  )
  const width = fixedWidth ?? measuredWidth
  // Full screen and presentation mode draw the circle labels larger, as every other chart's text (R2-B F-22).
  const textScale = usePresentationScale()
  const labelFont = LABEL_FONT * textScale
  const box = useMemo(() => plotBox(width, height, shape), [width, height, shape])
  const drawn = useMemo(
    () => (box.width > 0 && box.height > 0 ? layout({ width: box.width, height: box.height }) : { nodes: [] }),
    [layout, box.width, box.height],
  )
  const nodeOf = useMemo(() => new Map(drawn.nodes.map((node) => [node.id, node])), [drawn])
  // Only what is drawn is walked, in the caller's (rank) order.
  const walk = useMemo(() => items.filter((item) => nodeOf.has(item.id)), [items, nodeOf])
  const categories = useMemo(() => {
    const present = new Set<FailureCategoryKey>()
    for (const item of walk) if (item.category) present.add(item.category)
    return [...present].sort()
  }, [walk])

  const activation: MarkActivationProps = useMemo(() => ({ onMarkActivate, markIntents }), [onMarkActivate, markIntents])
  const intentsOf = useCallback(
    (item: PlotItem): MarkIntent[] => (item.mark ? availableIntents(item.mark, activation) : []),
    [activation],
  )
  const act = useCallback(
    (item: PlotItem, intent: MarkIntent | null) => {
      if (intent && item.mark && onMarkActivate) onMarkActivate(item.mark, intent)
    },
    [onMarkActivate],
  )

  const announcer = useChartAnnouncer()
  const describe = useCallback((index: number) => tooltipText(walk[index].tip), [walk])
  const move = useMemo(() => rankWalk(walk.length), [walk.length])
  const onActivate = useCallback((index: number) => announcer?.assertive(describe(index)), [announcer, describe])
  const onClear = useCallback(() => {}, [])
  // Enter acts only when the host can be handed something: without a handler
  // the keyboard is exactly the canvas charts' (`useChartKeyboard` without `onEnter`).
  const onEnter = useCallback(
    (index: number, { shiftKey }: { shiftKey: boolean }) => {
      const item = walk[index]
      if (item) act(item, keyboardIntent(intentsOf(item), { shiftKey }))
    },
    [walk, act, intentsOf],
  )
  const { containerRef, containerProps, activeIndex } = useChartKeyboard({
    count: walk.length,
    move,
    onActivate,
    onClear,
    describe,
    onEnter: onMarkActivate ? onEnter : undefined,
  })

  const [hoverId, setHoverId] = useState<string | null>(null)
  const [tappedId, setTappedId] = useState<string | null>(null)
  const active = activeIndex !== null ? walk[activeIndex] : null
  const shown = walk.find((item) => item.id === hoverId) ?? active ?? walk.find((item) => item.id === tappedId) ?? null

  const onCircleClick = (item: PlotItem, event: MouseEvent<SVGElement>) => {
    // A click is a PointerEvent in every current browser: its `pointerType` tells a touch apart.
    const pointerType = (event.nativeEvent as { pointerType?: unknown }).pointerType
    const intent = pointerIntent(intentsOf(item), {
      shiftKey: event.shiftKey,
      ctrlKey: event.ctrlKey,
      metaKey: event.metaKey,
      pointerType: typeof pointerType === 'string' ? pointerType : undefined,
    })
    // A touch (no modifier to choose with) selects; the readout's buttons act.
    if (intent === null) setTappedId(item.id)
    else act(item, intent)
  }

  const fit = useCallback(
    (text: string, radius: number) =>
      fitLabel(text, radius * 2 * LABEL_BAND, (t) => (measure ?? roughMeasure)(t, labelFont), radius * 2 * LABEL_WHOLE),
    [measure, labelFont],
  )
  const linkOpacity = (link: PlotLink) =>
    shown && (link.source === shown.id || link.target === shown.id) ? 0.9 : 0.25 + link.weight * 0.35

  // The buttons are for the keyboard's and a touch's mark; a pointer acts on the circle itself
  // (and a hover's readout would vanish on the way to its buttons).
  const actionTarget = shown !== null && (shown === active || shown.id === tappedId) ? shown : null
  const readoutIntents = actionTarget ? intentsOf(actionTarget) : []
  // The buttons name the group cut on WORD boundaries (R2-B F-23); an activation hands the WHOLE mark.
  const buttonMark = useMemo<ChartMark | null>(
    () =>
      actionTarget?.mark ? { ...actionTarget.mark, label: wordTruncate(actionTarget.mark.label, MARK_BUTTON_LABEL_MAX) } : null,
    [actionTarget],
  )
  const onButton = useCallback(
    (_shown: ChartMark, intent: MarkIntent) => {
      if (actionTarget?.mark && onMarkActivate) onMarkActivate(actionTarget.mark, intent)
    },
    [actionTarget, onMarkActivate],
  )
  // A readout button's action may open a panel that takes focus; when it closes, focus returns to the plot
  // (the one tab stop), not to a button that is gone (X4 request 1, as TestScatter and the cursor charts do).
  const plotOf = useCallback(() => containerRef.current, [containerRef])
  return (
    // The outer box is measured (the width the plot may take) and keeps the hint's line; the inner one is the focus stop.
    <div ref={attach} className="w-full min-w-0" style={{ paddingBottom: GROUP_PLOT_HINT_RESERVE }}>
      <div
        ref={containerRef}
        {...containerProps}
        role="group"
        aria-label={description}
        aria-describedby={hintId}
        data-group-plot={kind}
        data-active-index={activeIndex ?? ''}
        className={`${SURFACE} ${box.beside ? BESIDE : UNDER}`}
      >
        {/* The plot is centred in the room the readout leaves (R2-B F-02: a square plot sat at the left of a
            940 px frame); a wide plot fills that room itself. */}
        <div
          data-group-plot-area=""
          style={{ ...(box.beside ? { flex: '1 1 0%' } : {}), minWidth: 0, display: 'flex', justifyContent: 'center' }}
        >
          <div
            className="relative shrink-0"
            data-chart-presentation={textScale}
            style={{ width: box.width, height: box.height }}
          >
            {box.width > 0 && (
              <svg
                width={box.width}
                height={box.height}
                viewBox={`0 0 ${box.width} ${box.height}`}
                aria-hidden="true"
                focusable="false"
                style={{ display: 'block', overflow: 'visible' }}
              >
                <defs>{renderPatterns(categoryPatternSpecs(prefix, categories))}</defs>
                {drawn.links?.map((link) => {
                  const a = nodeOf.get(link.source)
                  const b = nodeOf.get(link.target)
                  if (!a || !b) return null
                  return (
                    <line
                      key={`${link.source.length}:${link.source}${link.target}`}
                      data-group-link=""
                      x1={a.x}
                      y1={a.y}
                      x2={b.x}
                      y2={b.y}
                      stroke={CHART_VARS.axis}
                      strokeOpacity={linkOpacity(link)}
                      strokeWidth={1 + link.weight * 3}
                    />
                  )
                })}
                {walk.map((item, index) => {
                  const node = nodeOf.get(item.id) as PlotNode
                  const isActive = index === activeIndex
                  const label = index < labelled && node.r >= MIN_LABELLED_RADIUS ? fit(item.label, node.r) : null
                  return (
                    <g
                      key={item.id}
                      data-group-id={item.id}
                      data-group-rank={item.rank}
                      data-active={isActive ? 'true' : undefined}
                      onPointerEnter={() => setHoverId(item.id)}
                      onPointerLeave={() => setHoverId((current) => (current === item.id ? null : current))}
                      onClick={(event) => onCircleClick(item, event)}
                      style={{
                        cursor: item.mark && onMarkActivate ? 'pointer' : undefined,
                      }}
                    >
                      <circle
                        cx={node.x}
                        cy={node.y}
                        r={node.r}
                        fill={item.category ? patternFill(categoryPatternId(prefix, item.category)) : CHART_VARS.series[0]}
                        fillOpacity={item.category ? 1 : 0.55}
                        stroke={CHART_VARS.card}
                        strokeWidth={1}
                      />
                      {item.id === selectedId && (
                        <circle
                          data-group-selected=""
                          cx={node.x}
                          cy={node.y}
                          r={node.r + 3}
                          fill="none"
                          stroke={CHART_VARS.text}
                          strokeWidth={1.5}
                          strokeDasharray="4 3"
                        />
                      )}
                      {isActive && (
                        <circle
                          data-group-focus=""
                          cx={node.x}
                          cy={node.y}
                          r={node.r + 2}
                          fill="none"
                          stroke={CHART_VARS.text}
                          strokeWidth={3}
                        />
                      )}
                      {label && (
                        <text
                          x={node.x}
                          y={node.y}
                          textAnchor="middle"
                          dominantBaseline="central"
                          fontSize={labelFont}
                          fill={CHART_VARS.text}
                          stroke={CHART_VARS.card}
                          strokeWidth={3}
                          paintOrder="stroke"
                          pointerEvents="none"
                          data-group-label=""
                        >
                          {label}
                        </text>
                      )}
                    </g>
                  )
                })}
              </svg>
            )}
          </div>
        </div>
        <div
          data-group-readout=""
          className="min-w-0 text-xs text-[var(--color-text-secondary)]"
          style={box.beside ? { flex: `0 0 ${READOUT_WIDTH}px` } : undefined}
        >
          {shown ? (
            <>
              <ChartTooltipBody content={shown.tip} />
              {buttonMark && onMarkActivate && (
                <MarkActions mark={buttonMark} intents={readoutIntents} onActivate={onButton} returnFocus={plotOf} />
              )}
            </>
          ) : (
            <p className="m-0">{idleText}</p>
          )}
          {drawn.note && (
            <p data-group-layout-note="" className="m-0 mt-3">
              {drawn.note}
            </p>
          )}
        </div>
        <p
          id={hintId}
          data-chart-keyboard-hint=""
          className="pointer-events-none absolute left-0 top-full z-10 mt-1 hidden rounded border border-[var(--color-border-light)] bg-[var(--color-bg-card)] px-2 py-1 text-xs text-[var(--color-text)] group-focus-visible:block"
        >
          {GROUP_PLOT_HINT}
        </p>
      </div>
    </div>
  )
}
