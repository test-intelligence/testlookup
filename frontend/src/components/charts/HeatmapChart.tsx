/**
 * Heatmap renderer (VIZ-103) — the first chart drawn by the ECharts engine.
 *
 * Canvas, lazy: this component's module holds no ECharts code; the engine
 * chunk (shared base + the heatmap type) is fetched the first time a heatmap
 * mounts. Colours are the resolved chart tokens, re-applied on a theme switch.
 *
 * Accessible (VIZ-105): the wrapper is one focus stop, named once by our
 * description (ECharts' own aria label is off) and described by a keyboard
 * hint that is also SHOWN while the chart has keyboard focus. Arrow keys move
 * a highlighted cell through ECharts' own `highlight` / `showTip` actions (so
 * the tooltip is the same formatter output the mouse gets), and the cell's
 * tooltip text is announced through the page's one chart announcer — a
 * response to the reader's own key press (a polite region of the chart's own
 * only when no announcer is mounted). Escape clears the highlight and keeps focus on the
 * chart; Tab leaves. Animation is off under `prefers-reduced-motion`.
 *
 * No-data cells are hatched, never the lowest ramp colour; a status matrix
 * carries each status decal plus a legend drawn with the same patterns.
 *
 * Mark activation (Wave 3, VIZ-501/602): only with `onMarkActivate` AND
 * `markOf`. A click on a cell acts at once (`pointerIntent`: a plain click is
 * the first intent, Shift/Ctrl/Cmd-click `filter` when offered); a touch tap
 * only selects the cell, and Enter (Shift+Enter) acts on the highlighted one.
 * Either way the selected cell's actions are real buttons in a row under the
 * plot (`MarkActions`), Tab-reachable, so nothing is pointer- or
 * shortcut-only. Without the two props the chart is exactly as before.
 */
import { useCallback, useId, useMemo, useState } from 'react'
import { VIZ_STATUSES, type VizStatus } from '@/lib/viz/contracts'
import { CHART_VARS, STATUS_ENCODING, useChartTokens } from './tokens'
import { tooltipText } from './tooltip'
import { useFramePlotHeight, usePresentationScale } from './framePlotHeight'
import { usePrefersReducedMotion } from './motion'
import { moveInMatrix, useChartKeyboard, type NavRequest } from './useChartKeyboard'
import {
  buildHeatmapOption,
  heatmapCellIndex,
  heatmapTarget,
  heatmapTooltipContent,
  type HeatmapMatrix,
  type HeatmapSalience,
} from './engines/echarts/heatmapOption'
import { STALE_BUILD_ACTION, STALE_BUILD_MESSAGE } from './engines/lazyChartEngine'
import { useEChart } from './engines/useEChart'
import { CHART_DRAW_ERROR } from './ChartErrorBoundary'
import { useChartAnnouncer } from './ChartAnnouncer'
import {
  ChartLegend,
  patternFill,
  renderPatterns,
  statusPatternId,
  statusPatternSpecs,
  useChartPatternPrefix,
  type PatternSpec,
} from './patterns'
import { NO_DATA } from './chartText'
import { MarkActions } from './MarkActions'
import {
  availableIntents,
  keyboardIntent,
  pointerIntent,
  type ChartMark,
  type MarkActivationProps,
  type MarkIntent,
} from './marks'

interface Props extends MarkActivationProps {
  data: HeatmapMatrix
  /** One sentence for assistive tech; ECharts' generated one is not useful. */
  description: string
  width?: number | string
  height?: number | string
  /** Animation; off by default (dense charts gain nothing from a tween). */
  animate?: boolean
  /** Which end is the concern (gets the most salient colour). Default per metric: rate → 'low', count → 'high'. */
  salient?: HeatmapSalience
  /** Row 0 drawn at the TOP (see `buildHeatmapOption`); the arrow keys follow what is drawn. */
  rowsTopDown?: boolean
  /** What the column axis prints (a day axis: "Sep 5"); the tooltip and the table keep `data.x_labels`. */
  columnLabels?: readonly string[]
  /** What the row axis prints (Wave 3: middle-truncated names); the tooltip and the table keep `data.y_labels`. */
  rowLabels?: readonly string[]
  /** The colour ramp's range ("fit to data", VIZ-501); default 0..1 for a rate, as before. */
  domain?: readonly [number, number]
  /** The mark cell `index` stands for, or `null` when it cannot be acted on (Wave 3; needs `onMarkActivate`). */
  markOf?: (index: number) => ChartMark | null
  /** The column axis' title on the canvas (F-19, a run axis); default none, as before. */
  columnAxisName?: string
  /** Where a too-wide column label is cut: ECharts' end (default) or the start, keeping its tail (F-04). */
  columnLabelCut?: 'end' | 'start'
}

export const HEATMAP_KEYBOARD_HINT = 'Arrow keys move, Escape clears, Tab leaves'
/** The hint's extra words when a cell can be acted on. */
export const HEATMAP_ACTIVATE_HINT = 'Enter opens the cell'

/**
 * The row under the plot that holds the keyboard hint and the selected cell's
 * action buttons, px: kept back in full screen. On the page the row is as
 * tall as what it holds (F-11: an always-empty 32 px band under every
 * heatmap), and the hint takes its own line there instead of covering the
 * legend or the footer below the chart (F-21).
 */
const HEATMAP_MARK_ROW = 32

/**
 * The "No data" key of a STATUS matrix (F-06): vertical axis-coloured lines on
 * the card, as its canvas cells draw them (`statusNoDataDecal`). Not the '/'
 * hatch of the rate heatmaps: that is Failed's shape.
 */
const noDataPatternId = (prefix: string) => `${prefix}-chart-pattern-no-data`
const noDataPatternSpec = (prefix: string): PatternSpec => ({
  id: noDataPatternId(prefix),
  color: CHART_VARS.card,
  decal: 'vertical',
  mark: CHART_VARS.axis,
})

const HINT = 'pointer-events-none hidden rounded border border-[var(--color-border-light)] bg-[var(--color-bg-card)] px-2 py-1 text-xs text-[var(--color-text)] group-focus-visible:block'

const BUTTON = 'rounded border border-[var(--color-border-light)] px-3 py-1 text-[var(--color-text)]'

/** What the keyboard hint and a status legend under the plot keep back in full screen, px. */
const HEATMAP_NOTES_RESERVE = 56

export default function HeatmapChart({
  data,
  description,
  width = '100%',
  height: requestedHeight = 320,
  animate = false,
  salient,
  rowsTopDown = false,
  columnLabels,
  rowLabels,
  domain,
  markOf,
  onMarkActivate,
  markIntents,
  columnAxisName,
  columnLabelCut,
}: Props) {
  // Mark activation is on only with both a handler and a way to name a cell's mark.
  const activatable = onMarkActivate !== undefined && markOf !== undefined
  // Full screen (VIZ-608): a numeric height grows to the frame's body; a CSS
  // height (a caller's '100%') already follows its container.
  const fitted = useFramePlotHeight(
    typeof requestedHeight === 'number' ? requestedHeight : 0,
    activatable ? HEATMAP_NOTES_RESERVE + HEATMAP_MARK_ROW : HEATMAP_NOTES_RESERVE,
  )
  const height = typeof requestedHeight === 'number' ? fitted : requestedHeight
  const tokens = useChartTokens()
  const reducedMotion = usePrefersReducedMotion()
  const effectiveAnimate = animate && !reducedMotion
  const hintId = useId()
  const prefix = useChartPatternPrefix()
  // A px width sizes each x label's column; '100%' falls back to a safe narrow one.
  const chartWidth = typeof width === 'number' ? width : undefined
  // Full screen and presentation mode draw the canvas text larger (K5): the
  // Recharts charts scale their whole SVG, a canvas sizes its text instead.
  const textScale = usePresentationScale()
  const option = useMemo(
    () => buildHeatmapOption({
        data,
        tokens,
        description,
        animate: effectiveAnimate,
        salient,
        chartWidth,
        textScale,
        rowsTopDown,
        columnLabels,
        rowLabels,
        domain,
        columnAxisName,
        columnLabelCut,
      }),
    [
      data,
      tokens,
      description,
      effectiveAnimate,
      salient,
      chartWidth,
      textScale,
      rowsTopDown,
      columnLabels,
      rowLabels,
      domain,
      columnAxisName,
      columnLabelCut,
    ],
  )
  // The cell's mark and the intents it offers; none when activation is off.
  const actionsAt = useCallback(
    (index: number): { mark: ChartMark; intents: MarkIntent[] } | null => {
      if (!activatable) return null
      const mark = markOf(index)
      if (!mark) return null
      const intents = availableIntents(mark, { onMarkActivate, markIntents })
      return intents.length > 0 ? { mark, intents } : null
    },
    [activatable, markOf, onMarkActivate, markIntents],
  )
  // A touch tap selects (no modifier keys to choose an intent with); its
  // buttons stay until the reader moves on. Keyed on the data it was made on.
  const [tapped, setTapped] = useState<{ data: HeatmapMatrix; index: number } | null>(null)
  const events = useMemo(
    () =>
      activatable
        ? {
            click: (params: unknown) => {
              const index = heatmapCellIndex(data, params)
              const actions = index === null ? null : actionsAt(index)
              if (index === null || !actions) return
              const native = (params as { event?: { event?: Partial<PointerEvent> } } | null)?.event?.event
              const intent = pointerIntent(actions.intents, {
                shiftKey: native?.shiftKey,
                ctrlKey: native?.ctrlKey,
                metaKey: native?.metaKey,
                pointerType: native?.pointerType,
              })
              if (intent === null) setTapped({ data, index })
              else onMarkActivate?.(actions.mark, intent)
            },
          }
        : undefined,
    [activatable, actionsAt, data, onMarkActivate],
  )
  const { containerRef, instanceRef, status, retry } = useEChart('heatmap', option, events)

  const dispatch = useCallback(
    (payload: { type: string; [key: string]: unknown }) => instanceRef.current?.dispatchAction?.(payload),
    [instanceRef],
  )
  // `moveInMatrix` reads `y` as growing UPWARD (ECharts' default). Drawn top-down,
  // the rows are handed to it flipped, so ArrowUp still moves to the row above.
  const navCells = useMemo(() => {
    if (!rowsTopDown) return data.cells
    const last = data.y_labels.length - 1
    return data.cells.map((cell) => ({ x: cell.x, y: last - cell.y }))
  }, [data, rowsTopDown])
  const move = useCallback((current: number | null, request: NavRequest) => moveInMatrix(navCells, current, request), [navCells])
  const describe = useCallback((index: number) => tooltipText(heatmapTooltipContent(data, data.cells[index])), [data])
  const announcer = useChartAnnouncer()
  const onActivate = useCallback(
    (index: number) => {
      const target = heatmapTarget(data, index)
      dispatch({ type: 'highlight', ...target })
      dispatch({ type: 'showTip', ...target })
      // The reader's OWN key press, so it is said at once, through the page's
      // one announcer (which also speaks inside a full-screen frame) — as
      // `useChartCursor` does. Spoken here, not on every change of the text:
      // a data refresh under a highlighted cell is not the reader's action.
      // The cell text alone: focus is on the chart, whose name was read on
      // arrival, and a sentence-long description before every arrow press
      // would bury the value.
      announcer?.assertive(describe(index))
    },
    [dispatch, data, announcer, describe],
  )
  const onClear = useCallback(
    (index: number) => {
      dispatch({ type: 'downplay', ...heatmapTarget(data, index) })
      dispatch({ type: 'hideTip' })
    },
    [dispatch, data],
  )
  // Enter / Shift+Enter on the highlighted cell (only with activation on).
  const onEnter = useCallback(
    (index: number, { shiftKey }: { shiftKey: boolean }) => {
      const actions = actionsAt(index)
      const intent = actions ? keyboardIntent(actions.intents, { shiftKey }) : null
      if (actions && intent) onMarkActivate?.(actions.mark, intent)
    },
    [actionsAt, onMarkActivate],
  )
  // Destructured: react-hooks/refs treats a whole object that holds a ref as a ref.
  const {
    containerRef: keyboardRef,
    containerProps,
    activeIndex,
    announcement,
  } = useChartKeyboard({ count: data.cells.length, move, onActivate, onClear, describe, onEnter: activatable ? onEnter : undefined })
  // The selected cell: the keyboard's, else the last tap on this data.
  const selected = activeIndex ?? (tapped && tapped.data === data ? tapped.index : null)
  const selectedActions = selected === null ? null : actionsAt(selected)
  const onButton = useCallback(
    (mark: ChartMark, intent: MarkIntent) => {
      // Focus back on the chart first: the panel the action opens returns
      // focus to whatever had it, and these buttons go when the cell does.
      keyboardRef.current?.focus()
      onMarkActivate?.(mark, intent)
    },
    [keyboardRef, onMarkActivate],
  )

  // A status matrix: which statuses are drawn, for the patterned legend, and
  // whether a cell is "No data" — keyed in the SAME row (F-11), not on the canvas.
  const { statuses, noData } = useMemo(() => {
    if (data.value_type !== 'status') return { statuses: [], noData: false }
    const present = new Set<VizStatus>()
    let missing = false
    for (const cell of data.cells) {
      if (cell.value !== null) present.add(cell.value)
      else missing = true
    }
    return { statuses: VIZ_STATUSES.filter((s) => present.has(s)), noData: missing }
  }, [data])
  const hint = activatable ? `${HEATMAP_KEYBOARD_HINT}, ${HEATMAP_ACTIVATE_HINT}` : HEATMAP_KEYBOARD_HINT

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

  const chart = (
    <div
      ref={keyboardRef}
      {...containerProps}
      role="group"
      aria-label={description}
      aria-describedby={hintId}
      data-chart-keyboard="heatmap"
      data-active-index={activeIndex ?? ''}
      className="group relative rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
      // With activation the plot has its own height and the action row sits
      // under it, inside the focus group (Tab onto a button keeps the cell).
      style={activatable ? { width } : { width, height }}
    >
      <div
        ref={containerRef}
        data-chart-engine="echarts"
        data-chart-type="heatmap"
        data-chart-status={status}
        style={{ width: '100%', height: activatable ? height : '100%' }}
      />
      {/*
        The keyboard hint: shown while the chart has keyboard focus, BELOW the
        plot (it never covers a cell), and always the chart's accessible
        description. With activation it is in the row under the plot, in
        flow, beside the cell's buttons (F-21: drawn over the next line it hid
        the legend and the footer); that row is empty, and takes no room, at
        rest (F-11). Without activation it is as before.
      */}
      {activatable ? (
        <div data-heatmap-mark-row="" className="flex flex-wrap items-center gap-2">
          {selectedActions ? (
            <MarkActions mark={selectedActions.mark} intents={selectedActions.intents} onActivate={onButton} />
          ) : null}
          <p id={hintId} data-chart-keyboard-hint="" className={`mt-1 ${HINT}`}>
            {hint}
          </p>
        </div>
      ) : (
        <p id={hintId} data-chart-keyboard-hint="" className={`absolute left-0 top-full z-10 mt-1 ${HINT}`}>
          {hint}
        </p>
      )}
      {/*
        Only with no page announcer above the chart (an isolated render): the
        one place left to say the cell, so the keyboard path never goes silent.
      */}
      {announcer === null ? (
        <div className="sr-only" aria-live="polite" data-chart-announcement="">
          {announcement}
        </div>
      ) : null}
    </div>
  )
  if (statuses.length === 0 && !noData) return chart

  // A status matrix: the legend swatches use SVG patterns shaped like the canvas
  // decals, "No data" (its own vertical lines, F-06) last in the same row.
  return (
    <div>
      {chart}
      <svg width={0} height={0} aria-hidden="true" focusable="false" className="absolute">
        <defs>{renderPatterns([...statusPatternSpecs(prefix, statuses), ...(noData ? [noDataPatternSpec(prefix)] : [])])}</defs>
      </svg>
      <ChartLegend
        entries={[
          ...statuses.map((s) => ({
            key: s,
            label: STATUS_ENCODING[s].label,
            status: s,
            fill: patternFill(statusPatternId(prefix, s)),
          })),
          ...(noData ? [{ key: 'no-data', label: NO_DATA, fill: patternFill(noDataPatternId(prefix)) }] : []),
        ]}
      />
    </div>
  )
}
