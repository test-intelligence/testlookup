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
 * tooltip text is announced in a polite live region — a response to the
 * reader's own key press. Escape clears the highlight and keeps focus on the
 * chart; Tab leaves. Animation is off under `prefers-reduced-motion`.
 *
 * No-data cells are hatched, never the lowest ramp colour; a status matrix
 * carries each status decal plus a legend drawn with the same patterns.
 */
import { useCallback, useId, useMemo } from 'react'
import { VIZ_STATUSES, type VizStatus } from '@/lib/viz/contracts'
import { STATUS_ENCODING, useChartTokens } from './tokens'
import { tooltipText } from './tooltip'
import { usePrefersReducedMotion } from './motion'
import { moveInMatrix, useChartKeyboard, type NavRequest } from './useChartKeyboard'
import {
  buildHeatmapOption,
  heatmapTarget,
  heatmapTooltipContent,
  type HeatmapMatrix,
  type HeatmapSalience,
} from './engines/echarts/heatmapOption'
import { STALE_BUILD_ACTION, STALE_BUILD_MESSAGE } from './engines/lazyChartEngine'
import { useEChart } from './engines/useEChart'
import { CHART_DRAW_ERROR } from './ChartErrorBoundary'
import { ChartLegend, patternFill, renderPatterns, statusPatternId, statusPatternSpecs, useChartPatternPrefix } from './patterns'

interface Props {
  data: HeatmapMatrix
  /** One sentence for assistive tech; ECharts' generated one is not useful. */
  description: string
  width?: number | string
  height?: number | string
  /** Animation; off by default (dense charts gain nothing from a tween). */
  animate?: boolean
  /** Which end is the concern (gets the most salient colour). Default per metric: rate → 'low', count → 'high'. */
  salient?: HeatmapSalience
}

export const HEATMAP_KEYBOARD_HINT = 'Arrow keys move, Escape clears, Tab leaves'

const BUTTON = 'rounded border border-[var(--color-border-light)] px-3 py-1 text-[var(--color-text)]'

export default function HeatmapChart({ data, description, width = '100%', height = 320, animate = false, salient }: Props) {
  const tokens = useChartTokens()
  const reducedMotion = usePrefersReducedMotion()
  const effectiveAnimate = animate && !reducedMotion
  const hintId = useId()
  const prefix = useChartPatternPrefix()
  const option = useMemo(
    () => buildHeatmapOption({ data, tokens, description, animate: effectiveAnimate, salient }),
    [data, tokens, description, effectiveAnimate, salient],
  )
  const { containerRef, instanceRef, status, retry } = useEChart('heatmap', option)

  const dispatch = useCallback(
    (payload: { type: string; [key: string]: unknown }) => instanceRef.current?.dispatchAction?.(payload),
    [instanceRef],
  )
  const move = useCallback((current: number | null, request: NavRequest) => moveInMatrix(data.cells, current, request), [data.cells])
  const onActivate = useCallback(
    (index: number) => {
      const target = heatmapTarget(data, index)
      dispatch({ type: 'highlight', ...target })
      dispatch({ type: 'showTip', ...target })
    },
    [dispatch, data],
  )
  const onClear = useCallback(
    (index: number) => {
      dispatch({ type: 'downplay', ...heatmapTarget(data, index) })
      dispatch({ type: 'hideTip' })
    },
    [dispatch, data],
  )
  const describe = useCallback((index: number) => tooltipText(heatmapTooltipContent(data, data.cells[index])), [data])
  // Destructured: react-hooks/refs treats a whole object that holds a ref as a ref.
  const {
    containerRef: keyboardRef,
    containerProps,
    activeIndex,
    announcement,
  } = useChartKeyboard({ count: data.cells.length, move, onActivate, onClear, describe })

  // A status matrix: which statuses are drawn, for the patterned legend.
  const statuses = useMemo(() => {
    if (data.value_type !== 'status') return []
    const present = new Set<VizStatus>()
    for (const cell of data.cells) if (cell.value !== null) present.add(cell.value)
    return VIZ_STATUSES.filter((s) => present.has(s))
  }, [data])

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
      style={{ width, height }}
    >
      <div
        ref={containerRef}
        data-chart-engine="echarts"
        data-chart-type="heatmap"
        data-chart-status={status}
        style={{ width: '100%', height: '100%' }}
      />
      {/*
        Shown while the chart has keyboard focus — just BELOW the plot, so it
        never covers a cell (the first active cell is the top-left one) — and
        always the chart's accessible description.
      */}
      <p
        id={hintId}
        data-chart-keyboard-hint=""
        className="pointer-events-none absolute left-0 top-full z-10 mt-1 hidden rounded border border-[var(--color-border-light)] bg-[var(--color-bg-card)] px-2 py-1 text-xs text-[var(--color-text)] group-focus-visible:block"
      >
        {HEATMAP_KEYBOARD_HINT}
      </p>
      <div className="sr-only" aria-live="polite" data-chart-announcement="">
        {announcement}
      </div>
    </div>
  )
  if (statuses.length === 0) return chart

  // A status matrix: the legend swatches use SVG patterns shaped like the canvas decals.
  return (
    <div>
      {chart}
      <svg width={0} height={0} aria-hidden="true" focusable="false" className="absolute">
        <defs>{renderPatterns(statusPatternSpecs(prefix, statuses))}</defs>
      </svg>
      <ChartLegend
        entries={statuses.map((s) => ({
          key: s,
          label: STATUS_ENCODING[s].label,
          status: s,
          fill: patternFill(statusPatternId(prefix, s)),
        }))}
      />
    </div>
  )
}
