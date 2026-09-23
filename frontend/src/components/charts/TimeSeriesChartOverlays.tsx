/**
 * The reader-facing half of the VIZ-405 trend overlays: the two toggles, the
 * statistics strip and the anomaly marker. The numbers and every sentence come
 * from `lib/trendStats`; this file only lays them out.
 *
 *   - An overlay is drawn in a DASH and a token colour of its own, never a
 *     colour alone. `TREND_OVERLAY_STYLE` is the one place that style lives:
 *     the plot, the legend and the toggle's own swatch all read it.
 *   - A toggle's pressed state is not a tint alone (the tint measured
 *     1.07-1.22:1 against the card): pressed carries a check mark and an
 *     accent border, and an off toggle's swatch is neutral AND struck through.
 *   - An anomaly is a SHAPE (a downward triangle), not a recoloured dot.
 *   - Every statistic is explainable on hover AND from the keyboard — method,
 *     window, sample size — through `Explained`. The explanation is the
 *     control's `aria-describedby`, so a screen reader hears it on focus too.
 *     SC 1.4.13: it stays open while the pointer travels onto it (its box
 *     begins flush with the control — the gap is transparent padding, not a
 *     margin the pointer falls through), Escape anywhere closes it however it
 *     was opened, and it is placed inside the chart's frame and any clipping
 *     scroller — flipped above the control when there is no room below.
 *   - The statistics are TOGGLETIPS (`aria-expanded`, Enter/Space toggle,
 *     reopenable after Escape); the overlay toggles already have an action, so
 *     their explanation opens on focus.
 *   - Below the minimum sample the toggles stay in the tab order but are
 *     `aria-disabled`, described ONCE by the visible reason beside them — a
 *     greyed-out control nobody can ask about explains nothing.
 */
import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import {
  ANOMALY_LABEL,
  formatTrendSlope,
  periodTakeaway,
  type TrendAnalysis,
  type TrendOverlayState,
} from '@/lib/trendStats'
import { CHART_VARS } from './tokens'
import {
  ANOMALY_FILL,
  ANOMALY_STROKE,
  TREND_OVERLAY_STYLE,
  anomalyTrianglePath,
  type TrendOverlayKey,
} from './TimeSeriesChartOverlayStyle'
import { flaggedDaysText, type ZoomedTrendAnalysis } from './zoom/zoomModel'

/** The marker Recharts draws for a flagged day (`<ReferenceDot shape>`). */
export function AnomalyMarker({ cx, cy, day }: { cx?: number; cy?: number; day: string }) {
  if (cx === undefined || cy === undefined || !Number.isFinite(cx) || !Number.isFinite(cy)) return <g />
  return (
    <path
      data-trend-anomaly={day}
      d={anomalyTrianglePath(cx, cy)}
      fill={ANOMALY_FILL}
      stroke={ANOMALY_STROKE}
      strokeWidth={1}
    />
  )
}

const CONTROL =
  'inline-flex items-center gap-1.5 rounded border border-[var(--color-border-light)] px-2 py-0.5 text-left text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)] aria-pressed:border-[var(--color-accent-ink)] aria-pressed:bg-[var(--color-bg-hover)] aria-disabled:cursor-not-allowed aria-disabled:opacity-60'

/** The explanation's preferred width; narrower when its boundary is. */
const TIP_WIDTH = 288
/** Kept between the explanation and its boundary's edge. */
const TIP_MARGIN = 4

interface Placement {
  side: 'below' | 'above'
  /** Horizontal offset from the control's left edge. */
  shift: number
  width: number
}

interface Box {
  left: number
  top: number
  right: number
  bottom: number
}

const intersect = (a: Box, b: Box): Box => ({
  left: Math.max(a.left, b.left),
  top: Math.max(a.top, b.top),
  right: Math.min(a.right, b.right),
  bottom: Math.min(a.bottom, b.bottom),
})

/**
 * Where an explanation may be drawn: the viewport, the chart's own frame
 * (`[data-chart-frame]`, when there is one), and every ancestor that clips
 * its overflow — the gallery's horizontal scroller clipped the 320 px layout.
 */
function boundaryOf(anchor: HTMLElement): Box {
  let box: Box = { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight }
  for (let el = anchor.parentElement; el; el = el.parentElement) {
    const style = getComputedStyle(el)
    const clips = style.overflowX !== 'visible' || style.overflowY !== 'visible'
    if (clips || el.hasAttribute('data-chart-frame')) box = intersect(box, el.getBoundingClientRect())
  }
  return box
}

/**
 * A control with an explanation. The explanation is the control's
 * description (read on focus even while closed). It opens on hover, and on
 * focus (`toggletip: false`) or on activation (`toggletip: true`, with
 * `aria-expanded`); it stays open while the pointer is over it, and closes on
 * Escape anywhere, when focus and pointer have both left, or — a toggletip —
 * on a second activation. `explanation: null` gives a control with no
 * explanation of its own (a disabled toggle is described by its visible reason).
 */
function Explained({
  explanation,
  describedBy,
  toggletip = false,
  children,
  onClick,
  ...button
}: {
  explanation: string | null
  /** Extra ids the control is described by (a visible reason). */
  describedBy?: string
  toggletip?: boolean
  children: ReactNode
  onClick?: () => void
  'aria-pressed'?: boolean
  'aria-disabled'?: true
  'data-trend-toggle'?: string
}) {
  const id = useId()
  const anchorRef = useRef<HTMLSpanElement>(null)
  const tipRef = useRef<HTMLSpanElement>(null)
  const [hovered, setHovered] = useState(false)
  const [focused, setFocused] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [dismissed, setDismissed] = useState(false)
  const [placement, setPlacement] = useState<Placement | null>(null)
  const open = explanation !== null && !dismissed && (hovered || (toggletip ? expanded : focused))

  const close = () => {
    setDismissed(true)
    setExpanded(false)
  }

  // Escape anywhere closes it — an explanation opened by HOVER has no focus
  // on its control for a key handler there to see.
  useEffect(() => {
    if (!open) return undefined
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setDismissed(true)
      setExpanded(false)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open])

  // Placed before paint: inside the boundary, below the control unless only above has room.
  useLayoutEffect(() => {
    const anchor = anchorRef.current
    const tip = tipRef.current
    if (!open || !anchor || !tip) {
      setPlacement(null)
      return
    }
    const bounds = boundaryOf(anchor)
    const room = bounds.right - bounds.left - 2 * TIP_MARGIN
    // A layout engine that measures nothing (jsdom) keeps the default placement.
    if (room < 120) return
    const at = anchor.getBoundingClientRect()
    const width = Math.min(TIP_WIDTH, room)
    const left = Math.max(bounds.left + TIP_MARGIN, Math.min(at.left, bounds.right - TIP_MARGIN - width))
    tip.style.width = `${width}px`
    const height = tip.getBoundingClientRect().height
    const below = bounds.bottom - at.bottom
    const above = at.top - bounds.top
    const side = height <= below || below >= above ? 'below' : 'above'
    setPlacement({ side, shift: left - at.left, width })
  }, [open, explanation])

  const tipStyle: CSSProperties | undefined = placement ? { left: placement.shift, width: placement.width } : undefined
  const above = placement?.side === 'above'

  return (
    <span
      ref={anchorRef}
      className="relative inline-flex"
      onMouseEnter={() => {
        setHovered(true)
        setDismissed(false)
      }}
      onMouseLeave={() => setHovered(false)}
    >
      <button
        type="button"
        {...button}
        aria-expanded={toggletip ? open : undefined}
        aria-describedby={[describedBy, explanation !== null ? id : undefined].filter(Boolean).join(' ') || undefined}
        onClick={() => {
          if (toggletip) {
            if (open) close()
            else {
              setExpanded(true)
              setDismissed(false)
            }
          }
          onClick?.()
        }}
        onFocus={() => {
          setFocused(true)
          setDismissed(false)
        }}
        onBlur={() => {
          setFocused(false)
          setExpanded(false)
        }}
        onKeyDown={(event) => {
          if (event.key === 'Escape' && open) {
            event.stopPropagation()
            close()
          }
        }}
        className={CONTROL}
      >
        {children}
      </button>
      {explanation !== null && (
        <span
          ref={tipRef}
          id={id}
          role="tooltip"
          data-trend-explanation=""
          data-placement={placement?.side ?? 'below'}
          hidden={!open}
          style={tipStyle}
          // The padding, not a margin, spaces it from the control: the pointer
          // crossing it is over the explanation already, so it stays open.
          className={`absolute left-0 z-20 w-72 max-w-[80vw] ${above ? 'bottom-full pb-1' : 'top-full pt-1'}`}
        >
          <span className="block rounded border border-[var(--color-border-light)] bg-[var(--color-bg-card)] p-2 text-left text-xs font-normal text-[var(--color-text)] shadow">
            {explanation}
          </span>
        </span>
      )}
    </span>
  )
}

/** The overlay's line, as drawn — neutral and struck through while the overlay is off. */
function LineSwatch({ overlay, on }: { overlay: TrendOverlayKey; on: boolean }) {
  const style = TREND_OVERLAY_STYLE[overlay]
  return (
    <svg data-trend-toggle-swatch="" width={20} height={10} aria-hidden="true" focusable="false">
      <line x1={1} y1={5} x2={19} y2={5} stroke={on ? style.stroke : CHART_VARS.neutral} strokeWidth={2} strokeDasharray={style.dash} />
      {!on && <line data-trend-swatch-strike="" x1={4} y1={9} x2={16} y2={1} stroke={CHART_VARS.neutral} strokeWidth={1.5} />}
    </svg>
  )
}

/** The pressed state's mark. Its slot is the same size either way, so toggling never shifts the row. */
function PressedMark({ on }: { on: boolean }) {
  return (
    <span data-trend-toggle-mark="" className="inline-flex h-2.5 w-2.5 shrink-0 items-center justify-center" aria-hidden="true">
      {on && (
        <svg data-trend-toggle-check="" width={10} height={10} viewBox="0 0 10 10" focusable="false">
          <path d="M1.5 5.2 4 7.6 8.6 2.4" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )}
    </span>
  )
}

function AnomalySwatch() {
  return (
    <svg width={12} height={12} aria-hidden="true" focusable="false">
      <path data-trend-anomaly-swatch="" d={anomalyTrianglePath(6, 6, 5)} fill={ANOMALY_FILL} stroke={ANOMALY_STROKE} strokeWidth={1} />
    </svg>
  )
}

/** The two toggles, above the plot. Disabled — with the visible reason — below the minimum sample. */
export function TrendOverlayControls({
  analysis,
  shown,
  onChange,
}: {
  analysis: TrendAnalysis
  shown: TrendOverlayState
  onChange: (next: TrendOverlayState) => void
}) {
  const reasonId = useId()
  const available = analysis.available
  const toggles: TrendOverlayKey[] = ['movingAverage', 'trendLine']
  return (
    <div role="group" aria-label="Trend overlays" data-trend-controls="" className="flex flex-wrap items-center gap-2">
      {toggles.map((key) => {
        const on = available && shown[key]
        return (
          <Explained
            key={key}
            data-trend-toggle={key}
            aria-pressed={on}
            aria-disabled={available ? undefined : true}
            describedBy={available ? undefined : reasonId}
            // Disabled: the visible reason IS the description — a tooltip repeating it was read twice.
            explanation={available ? analysis.explain[key] : null}
            onClick={() => {
              if (available) onChange({ ...shown, [key]: !shown[key] })
            }}
          >
            <PressedMark on={on} />
            <LineSwatch overlay={key} on={on} />
            {TREND_OVERLAY_STYLE[key].label}
          </Explained>
        )
      })}
      {!available && (
        <span id={reasonId} data-trend-disabled-reason="" className="text-xs text-[var(--color-text-secondary)]">
          {analysis.reason}
        </span>
      )}
    </div>
  )
}

const lowerFirst = (text: string) => text.charAt(0).toLowerCase() + text.slice(1)

/**
 * The statistics, each a toggletip: the long-window trend, the last week vs
 * the one before, the flagged days.
 *
 * VIZ-407: on a zoomed chart the analysis carries `zoomed` — its anomalies are
 * the days in view, while every explanation is the whole window's. The flagged
 * count then states both ("2 days flagged as unusual in view (5 in the
 * window)"), so the number and the explanation under it agree.
 */
export function TrendStatsStrip({ analysis }: { analysis: ZoomedTrendAnalysis }) {
  const period = periodTakeaway(analysis.period)
  return (
    <ul data-trend-stats="" className="m-0 flex list-none flex-wrap items-center gap-2 p-0">
      {analysis.available && (
        <li data-trend-stat="trend">
          <Explained toggletip explanation={analysis.explain.trendLine}>
            Trend: {analysis.fit.direction === 'flat' ? 'flat' : `${analysis.fit.direction} ${formatTrendSlope(analysis)}`}
          </Explained>
        </li>
      )}
      <li data-trend-stat="period">
        <Explained toggletip explanation={analysis.period.explain}>
          {period ?? `Last 7 days vs previous 7: ${analysis.period.measurable ? '' : lowerFirst(analysis.period.reason)}`}
        </Explained>
      </li>
      {analysis.available && (
        <li data-trend-stat="anomalies">
          <Explained toggletip explanation={analysis.explain.anomalies}>
            <AnomalySwatch />
            {flaggedDaysText(ANOMALY_LABEL, analysis.anomalies.length, analysis.zoomed?.anomaliesInWindow)}
          </Explained>
        </li>
      )}
    </ul>
  )
}
