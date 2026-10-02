/**
 * The Summary report's catalogue sections while their chunk loads (R1-2,
 * R1 (a)): the page's own stand-in for `SummaryCatalogue`, drawn from the
 * report the page already holds, with NO chart code (no kit import, nothing
 * from Recharts), so it paints in the same frame as the tables.
 *
 * It is the frames' boxes, not a spinner. Each frame is drawn with the frame's
 * own header markup (`ChartFrame`: the `h2` title and the `p` takeaway, the
 * same words from `summaryCatalogueWords`), the toolbar the real frame will
 * show reserved by an invisible copy of its buttons (so the browser measures
 * it in whatever font it has, never a width tuned to one), and a body of the
 * height the real plot will draw at. Two things follow:
 *
 *   - no layout shift: when the chunk arrives the real frames take exactly the
 *     space this held, so the per-suite and top-failing tables under them do
 *     not move (they used to jump ~400 px a second after they painted);
 *   - the takeaway this paints with the page's data is the page's Largest
 *     Contentful Paint candidate, and the real frame's identical paragraph is
 *     not a LARGER element, so it is not a new LCP entry: the page's LCP stays
 *     at its data paint instead of waiting for the first Recharts code.
 *
 * Drift guard: `SummaryCatalogueShell.test.tsx` renders this beside the real
 * sections and holds the header markup, the toolbar's buttons and the body
 * heights equal, for every case below. A change to the frame's header or
 * toolbar fails it until this copy follows.
 *
 * Kept tiny on purpose: it is in the page's own chunk, which every flag-OFF
 * visit downloads too. So no icon library, no `Skeleton`, no `formatters`
 * (which brings date-fns): the icons are boxes of the icons' size (the
 * toolbar is invisible anyway), the plot is a plain pulsing box, and the one
 * number it prints is formatted with `Intl.NumberFormat` as `formatNumber`
 * does it.
 *
 * Not reproduced: the lazy trend is the section's own `LazySection`
 * placeholder (an empty box of its height), exactly as the section first
 * renders it.
 */
import { usePresentationStore } from '@/store/presentationStore'
import type { SummaryReport, SummaryReportMode } from '@/types/summaryReport'
import {
  FAILURES_TITLE,
  STATUS_TITLE,
  SUITES_TITLE,
  SUMMARY_BARS_HEIGHT,
  SUMMARY_DONUT_HEIGHT,
  SUMMARY_HEADLINE_CLASS,
  SUMMARY_HEADLINE_ROW_CLASS,
  SUMMARY_TOP_FAILING_CLASS,
  SUMMARY_TREND_CHROME_PX,
  SUMMARY_TREND_HEIGHT,
  failuresTakeaway,
  statusTakeaway,
  suitesTakeaway,
} from './summaryCatalogueWords'

// ── The frame's markup, copied (ChartFrame.tsx; held equal by the drift test) ──
const FRAME = 'flex min-w-0 flex-col gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)] p-4'
const HEADER = 'flex flex-wrap items-start gap-3'
const TITLE_COLUMN = 'min-w-0 flex-[1_1_8rem]'
const HEADING = 'line-clamp-2 break-words text-sm font-semibold text-[var(--color-text)]'
const TAKEAWAY = 'mt-0.5 text-sm text-[var(--color-text-secondary)]'
const TOOLBAR = 'flex max-w-full shrink-0 flex-wrap items-center gap-1'
const BODY = 'min-w-0 transition-opacity focus:outline-none'
const FOOTER = 'flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--color-text-secondary)]'
const BUTTON =
  'rounded border border-[var(--color-border-light)] px-3 py-1 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]'
const ICON_BUTTON =
  'inline-flex items-center gap-1.5 rounded border border-[var(--color-border-light)] p-1 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]'
// ChartExportMenu.tsx's trigger, and BarChart.tsx's own toolbar buttons.
const EXPORT_TRIGGER =
  'inline-flex min-h-6 items-center gap-1 rounded border border-[var(--color-border-light)] px-3 py-1 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)] aria-disabled:opacity-60'
const BAR_TOOLBAR_BUTTON =
  'rounded border border-[var(--color-border-light)] px-2 py-1 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)] disabled:opacity-50 aria-disabled:opacity-50'

// ── The plot heights the real charts draw at (held equal by the drift test) ──
/** `PRESENTATION_MODE_SCALE` (framePlotHeight.ts): a drawing's box grows by it while presentation mode is on. */
const PRESENTATION_MODE_SCALE = 16 / 11
/** `MIN_BAR_ROW_HEIGHT`, `MAX_BARS_PER_PAGE`, `TIE_CAP_EXTRA` (BarChart.model.ts). */
const BAR_ROW_PX = 20
const BARS_PER_PAGE = 50
const TIE_CAP_EXTRA = 5
/** BarChart.tsx: margins + value axis (68), and a status chart's legend on top (32). */
const RANKED_CHROME_PX = 68
const STATUS_CHROME_PX = RANKED_CHROME_PX + 32
/** Summary's failing-test bars draw the top 10 (`topN`). */
const FAILURES_TOP_N = 10

/** `formatNumber`'s output for a count (en-US, whole numbers), without its module. */
const COUNT_FORMAT = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 })
const formatNumber = (n: number) => COUNT_FORMAT.format(n)

/** An icon's box (lucide's `svg` at the same size classes): the toolbar is never painted. */
const IconBox = ({ size }: { size: string }) => <span aria-hidden="true" data-summary-shell-icon="" className={size} />

const count = (value: unknown): number => (typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : 0)
const barPlotHeight = (rows: number, chrome: number) =>
  Math.max(SUMMARY_BARS_HEIGHT, Math.ceil(Math.min(rows, BARS_PER_PAGE) * BAR_ROW_PX + chrome))

interface ShellFrameProps {
  title: string
  takeaway: string
  /** The real frame draws its chart (else it shows a state message, with no toolbar). */
  drawn: boolean
  /** The stacked bars' "Show 100%" toggle sits first in their toolbar. */
  modeToggle?: boolean
  /** The plot's height, px, before presentation mode scales it. */
  plotHeight: number
  /** The requested height: the body's floor, and its height when nothing is drawn. */
  height: number
  /**
   * The bars' footer. A drawn `BarChart` always hands its frame one (empty
   * when it has no notes and one page), and the frame lays out even an empty
   * footer row: one more gap in its column.
   */
  footer?: { notes: string[]; pages: number }
}

function ShellFrame({ title, takeaway, drawn, modeToggle = false, plotHeight, height, footer }: ShellFrameProps) {
  const presenting = usePresentationStore((s) => s.enabled)
  const body = drawn ? Math.max(height, presenting ? Math.round(plotHeight * PRESENTATION_MODE_SCALE) : plotHeight) : height
  return (
    <div className={FRAME} data-summary-shell-frame={title}>
      <div className={HEADER}>
        <div className={TITLE_COLUMN}>
          <h2 className={HEADING}>{title}</h2>
          <p className={TAKEAWAY} data-summary-shell-takeaway="">
            {takeaway}
          </p>
        </div>
        {drawn && (
          // The real toolbar's buttons, invisible: they hold its width (and
          // its wrap) in the reader's own font; nothing here can be focused,
          // pressed or heard.
          <div className={`invisible ${TOOLBAR}`} aria-hidden="true" data-summary-shell-toolbar="">
            {modeToggle && (
              <button type="button" tabIndex={-1} className={BAR_TOOLBAR_BUTTON}>
                <SwapWords labels={['Show 100%', 'Show counts']} />
              </button>
            )}
            <button type="button" tabIndex={-1} className={BUTTON}>
              <SwapWords labels={['View as table', 'Hide table']} />
            </button>
            <div className="relative">
              <button type="button" tabIndex={-1} className={EXPORT_TRIGGER}>
                Export
                <IconBox size="h-3 w-3" />
              </button>
            </div>
            <button type="button" tabIndex={-1} className={ICON_BUTTON}>
              <IconBox size="h-4 w-4" />
            </button>
          </div>
        )}
      </div>
      <div className={BODY} style={{ minHeight: height }}>
        <div
          aria-hidden="true"
          data-summary-shell-plot=""
          className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-hover)] motion-safe:animate-pulse"
          style={{ height: body }}
        />
      </div>
      {drawn && footer && (
        <div className={`invisible ${FOOTER}`} aria-hidden="true">
          {footer.notes.map((note) => (
            <span key={note}>{note}</span>
          ))}
          {footer.pages > 1 && (
            <span className="flex items-center gap-2">
              <button type="button" tabIndex={-1} className={BAR_TOOLBAR_BUTTON}>
                Previous bars
              </button>
              <span>{`Page 1 of ${footer.pages}`}</span>
              <button type="button" tabIndex={-1} className={BAR_TOOLBAR_BUTTON}>
                Next bars
              </button>
            </span>
          )}
        </div>
      )}
    </div>
  )
}

/** `SwapLabel`'s markup: every label in one grid cell, so the button is as wide as the widest. */
function SwapWords({ labels }: { labels: readonly [string, string] }) {
  return (
    <span className="inline-grid justify-items-center">
      <span className="col-start-1 row-start-1">{labels[0]}</span>
      <span className="invisible col-start-1 row-start-1">{labels[1]}</span>
    </span>
  )
}

/** The failing-test bars: the top 10, plus any tied with the 10th (capped), as `rankedModel` keeps them. */
function failuresKept(report: SummaryReport): { bars: number; ties: number; capped: boolean } {
  const values = report.top_failing_tests.map((row) => row.failures).filter((v): v is number => typeof v === 'number' && Number.isFinite(v))
  const sorted = [...values].sort((a, b) => b - a)
  if (sorted.length <= FAILURES_TOP_N) return { bars: sorted.length, ties: 0, capped: false }
  const boundary = sorted[FAILURES_TOP_N - 1]
  const cap = FAILURES_TOP_N + TIE_CAP_EXTRA
  let end = FAILURES_TOP_N
  while (end < sorted.length && end < cap && sorted[end] === boundary) end += 1
  return { bars: end, ties: end - FAILURES_TOP_N, capped: end === cap && sorted.length > cap && sorted[cap] === boundary }
}

export interface SummaryCatalogueShellProps {
  part: 'headline' | 'top-failing'
  report: SummaryReport
  days: number
  mode: SummaryReportMode
}

export default function SummaryCatalogueShell({ part, report, days, mode }: SummaryCatalogueShellProps) {
  if (part === 'top-failing') {
    const { bars, ties, capped } = failuresKept(report)
    const drawn = report.top_failing_tests.some((row) => count(row.failures) > 0)
    const notes =
      ties > 0
        ? [
            `Includes ${formatNumber(ties)} more tied with the ${FAILURES_TOP_N}th${capped ? `, capped at ${FAILURES_TOP_N + TIE_CAP_EXTRA}` : ''}: ` +
              `${formatNumber(bars)} bars in all.`,
          ]
        : []
    return (
      <div className={SUMMARY_TOP_FAILING_CLASS} aria-busy="true" data-summary-catalogue-shell="top-failing">
        <ShellFrame
          title={FAILURES_TITLE}
          takeaway={failuresTakeaway(days, mode)}
          drawn={drawn}
          plotHeight={barPlotHeight(bars, RANKED_CHROME_PX)}
          height={SUMMARY_BARS_HEIGHT}
          footer={{ notes, pages: 1 }}
        />
      </div>
    )
  }

  const { totals, suites } = report
  const donutDrawn = count(totals.passed) + count(totals.failed) + count(totals.broken) + count(totals.skipped) > 0
  const suitesDrawn = suites.some((row) => count(row.passed) + count(row.failed) + count(row.broken) + count(row.skipped) > 0)
  const pages = Math.max(1, Math.ceil(suites.length / BARS_PER_PAGE))
  return (
    <div className={SUMMARY_HEADLINE_CLASS} aria-busy="true" data-summary-catalogue-shell="headline">
      <div className={SUMMARY_HEADLINE_ROW_CLASS}>
        <div className="min-w-0">
          <ShellFrame
            title={STATUS_TITLE}
            takeaway={statusTakeaway(report, mode)}
            drawn={donutDrawn}
            plotHeight={SUMMARY_DONUT_HEIGHT}
            height={SUMMARY_DONUT_HEIGHT}
          />
        </div>
        <div className="min-w-0">
          <ShellFrame
            title={SUITES_TITLE}
            takeaway={suitesTakeaway(report, mode)}
            drawn={suitesDrawn}
            modeToggle
            plotHeight={barPlotHeight(suites.length, STATUS_CHROME_PX)}
            height={SUMMARY_BARS_HEIGHT}
            footer={{ notes: pages > 1 ? [`${formatNumber(suites.length)} bars in all`] : [], pages }}
          />
        </div>
      </div>
      {/* The trend's own LazySection placeholder, as the section first renders it. */}
      <div aria-hidden="true" style={{ minHeight: SUMMARY_TREND_HEIGHT + SUMMARY_TREND_CHROME_PX }} />
    </div>
  )
}
