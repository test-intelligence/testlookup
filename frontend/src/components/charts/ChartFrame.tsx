/**
 * `ChartFrame` (VIZ-101) — the one shell every chart renders inside.
 *
 *   header   title (a real heading at `headingLevel`; wraps to two lines, then
 *            truncates with the full text in `title`), the optional one-line
 *            takeaway, the scope slot and the toolbar
 *   body     `role="group"`, named by the title and described by the takeaway
 *            and a summary generated from the normalised series (VIZ-105). The
 *            frame OWNS every non-data state (VIZ-107): the child renderer is
 *            mounted only for `ready` and `truncated`, inside a per-frame error
 *            boundary, so one failing chart never blanks the page
 *   table    "View as table", built from the same `series` the renderer gets
 *   footer   "Showing top N of M" when truncated, "N of M runs" from
 *            `meta.totals`, then the caller's footer
 *
 * State messages are static text in the group (no `alert` / `status` role). A
 * CHANGE after the first load is reported to the page's one
 * `ChartAnnouncerProvider`, which coalesces every frame's changes into one
 * short polite message; the result of the reader's own Retry is announced
 * assertively. The long summary is only ever the group's description.
 *
 * Purely presentational: no fetching here (data comes from `useChartData`),
 * and no state that changes on hover. The forwarded ref is the chart body.
 *
 * The toolbar's own actions, once the chart is drawn: "View as table",
 * Export (VIZ-606 — PNG / SVG / CSV, stamped with the scope from `meta` and
 * with `zoomNote` when the chart is locally zoomed), and Full screen
 * (VIZ-608). Full screen is the WHOLE frame, not just the plot — title,
 * takeaway, legend, toolbar and footer come along, in presentation type sizes
 * (the `[data-chart-fullscreen]` block in `index.css`). While it is on, the
 * frame publishes its body height and itself as the portal container through
 * `chartFrameContext`, so a chart can fill the screen and its popovers stay
 * visible. Outside full screen the frame renders exactly as it did before
 * full screen existed, apart from the two new buttons.
 *
 * While full screen the frame is a modal and the page behind it is `inert`
 * (`useFullscreen`), so the page's one announcer would be out of reach of a
 * screen reader: the frame renders a `ChartAnnouncerOutlet` and the announcer
 * speaks from inside it meanwhile. The header reflows (the toolbar wraps
 * under the title) so a 320 px screen never scrolls sideways. And Escape
 * closes the innermost thing first — an open menu, then a tooltip, then full
 * screen itself.
 */
import {
  forwardRef,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type Ref,
} from 'react'
import { Maximize2, Minimize2 } from 'lucide-react'
import type { ChartSeries } from '@/lib/viz/contracts'
// `totalsLine` is shared with the export so the footer and an exported file
// can never state the scope differently.
import { provenanceFromMeta, totalsLine, WINDOW_TOTALS_LABEL, type ChartProvenance } from '@/lib/viz/chartExport'
import { formatNumber } from '@/utils/formatters'
import { useFullscreen } from '@/hooks/useFullscreen'
import Skeleton from '@/components/ui/Skeleton'
import ChartErrorBoundary from './ChartErrorBoundary'
import ChartExportMenu from './ChartExportMenu'
import ChartTable from './ChartTable'
import { hasChartData, seriesHasPoints, type ChartState } from './chartState'
import { NO_VALUE, summarizeChart, type ChartAxes, type SeriesFormat } from './chartText'
import { STALE_BUILD_ACTION, STALE_BUILD_MESSAGE } from './engines/lazyChartEngine'
import { CHART_MESSAGES } from './chartMessages'
import { ChartAnnouncerOutlet, useChartAnnouncer } from './ChartAnnouncer'
import { ChartFrameContext, type ChartFrameContextValue } from './chartFrameContext'
import SwapLabel from './SwapLabel'

export type ChartHeadingLevel = 2 | 3 | 4 | 5 | 6

export interface ChartFrameProps {
  title: string
  /** One line, e.g. "Down 3.1 pts in 30 days". Omitted → nothing is rendered in its place. */
  takeaway?: string
  state: ChartState<unknown>
  /** Scope badge slot (VIZ-307). */
  scope?: ReactNode
  /** The chart's own toolbar actions, placed before the frame's (table, export, full screen). */
  toolbar?: ReactNode
  headingLevel: ChartHeadingLevel
  /** Extra footer content, after the frame's own "N of M". */
  footer?: ReactNode
  /**
   * The renderer. A function receives `series` — pass the renderer that exact
   * object so the plot, the summary and the table read the same data.
   */
  children: ReactNode | ((series: ChartSeries) => ReactNode)
  'data-testid'?: string
  /** The normalised series the renderer draws. Enables the summary and "View as table". */
  series?: ChartSeries | null
  /** For the summary, e.g. "Heatmap", "Line chart". */
  chartType?: string
  /**
   * Extra content for the TABLE VIEW, rendered under the data table when it is
   * open (VIZ-403). Annotations a chart draws over the plot but that are not
   * values in the series — release markers are the case this exists for — have
   * to reach the table reader too, or the table is a lesser view of the chart
   * rather than an equal one.
   */
  tableExtras?: ReactNode
  axes?: ChartAxes
  /** The scope in words, for the summary. */
  scopeLabel?: string
  /**
   * How the values are formatted in the summary and the table view: ONE
   * formatter, or one PER SERIES keyed on `series.key`. A chart whose series
   * are a duration and a run count needs the keyed form — a single formatter
   * prints "10 runs" as "10ms" in both places.
   */
  format?: SeriesFormat
  /**
   * What a CHANGE to this frame is called in the page-level announcement, when
   * the frame is DRAWN. The default is "updated", which is right for new data
   * and wrong for a control the reader just used: turning to the next page of
   * bars is a page change, not an update. Pass the words the reader would use
   * ("page 2 of 2") and the announcement says exactly that, ONCE — the frame
   * still makes only its own single report, so nothing is announced twice.
   */
  changeLabel?: string
  /** Body height in px: the skeleton and state messages hold this space. */
  height?: number
  /** Offered in `filtered-empty`. Without it, no button is shown. */
  onClearFilters?: () => void
  /** Where "Ingest test results" goes in `never-had-data`. */
  ingestHref?: string
  /** Where "Sign in" goes when the session expired (a 401 after the refresh retry). */
  signInHref?: string
  /**
   * Set ONLY while the chart is locally zoomed (VIZ-407), in words, e.g.
   * "Zoomed to 3 Sep – 12 Sep (not the page window)". It is shown in the
   * footer and stamped on every export, so neither a reader nor an exported
   * image can take the zoomed slice for the page's whole window.
   */
  zoomNote?: string
  /** The chart's part of the export file name (VIZ-606). Default: a slug of the title. */
  exportSlug?: string
  /**
   * The scope stamped on an export. Default: built from the state's `meta`
   * (plus `zoomNote`). Pass one only when the chart's scope is not its
   * envelope's — `null` exports with "Scope unavailable".
   */
  provenance?: ChartProvenance | null
}

function assignRef<T>(ref: Ref<T> | undefined, value: T | null) {
  if (typeof ref === 'function') ref(value)
  else if (ref) (ref as { current: T | null }).current = value
}

/** How a settled state is named in a (short) change announcement. */
const CHANGE_LABEL: Record<Exclude<ChartState['status'], 'loading'>, string> = {
  ready: 'updated',
  truncated: 'updated',
  'never-had-data': 'no data yet',
  'filtered-empty': 'no data matches the filters',
  'not-measured': 'not measured',
  error: 'error',
  forbidden: 'no access',
}

const TABLE_TOGGLE_LABELS = [CHART_MESSAGES.viewTable, CHART_MESSAGES.hideTable] as const

const BUTTON =
  'rounded border border-[var(--color-border-light)] px-3 py-1 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]'

/**
 * The full-screen button outside full screen: an icon, 26 × 26 px — the height
 * of `BUTTON` — so it neither makes the toolbar row ragged nor takes the width
 * a worded button would from the title (the gallery draws frames at 400 px).
 * Its name is the `aria-label`; in full screen, where width is no object and a
 * presenter has to find it, the words are shown too.
 */
const ICON_BUTTON =
  'inline-flex items-center gap-1.5 rounded border border-[var(--color-border-light)] p-1 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]'

/**
 * The chart body's height in full screen before it has been measured (the
 * first frame, and anywhere without layout, such as jsdom): most of the
 * viewport, and never less than the height the page asked for.
 */
function unmeasuredFullscreenHeight(height: number): number {
  const viewport = typeof window === 'undefined' ? 0 : window.innerHeight
  return Math.max(height, Math.round(viewport * 0.6))
}

/**
 * What Escape dismisses inside a chart before it leaves full screen (VIZ-608,
 * innermost first): a Recharts pointer tooltip (the chart cursor's document
 * listener hides it) and an explanation popover (`role="tooltip"`, which
 * closes on Escape anywhere). An ECharts tooltip carries the same marker but
 * Escape does nothing to it, so it is not waited on. An open menu needs no
 * entry here: it stops its own Escape before full screen hears it.
 */
const ESCAPE_DISMISSES = '[data-chart-tooltip], [role="tooltip"]'
const CANVAS_ENGINE = '[_echarts_instance_]'

/** The ones showing now inside `root`: not `hidden`, not `display: none` or `visibility: hidden` on the way up. */
function dismissibleShowing(root: HTMLElement | null): Element[] {
  if (!root) return []
  const showing: Element[] = []
  for (const el of Array.from(root.querySelectorAll(ESCAPE_DISMISSES))) {
    if (el.closest(CANVAS_ENGINE) || el.closest('[hidden]')) continue
    if (getComputedStyle(el).visibility === 'hidden') continue
    let displayed = true
    for (let node: Element | null = el; node && node !== root; node = node.parentElement) {
      if (getComputedStyle(node).display === 'none') {
        displayed = false
        break
      }
    }
    if (displayed) showing.push(el)
  }
  return showing
}

/**
 * A state's message: STATIC text inside the frame's group — no `alert` or
 * `status` role. A page of frames must not be a stack of live regions; a
 * change is announced once, page-wide, by `ChartAnnouncer`.
 */
function Message({ children, state }: { children: ReactNode; state: string }) {
  return (
    <div
      data-chart-state-message={state}
      className="flex h-full min-h-[inherit] flex-col items-center justify-center gap-2 p-4 text-center text-sm text-[var(--color-text-secondary)]"
    >
      {children}
    </div>
  )
}

function StateBody({
  state,
  onClearFilters,
  ingestHref,
  signInHref,
  onRetry,
}: {
  state: ChartState<unknown>
  onClearFilters?: () => void
  ingestHref: string
  signInHref: string
  onRetry?: () => void
}) {
  switch (state.status) {
    case 'never-had-data':
      return (
        <Message state={state.status}>
          <p className="font-medium text-[var(--color-text)]">{CHART_MESSAGES.neverHadData}</p>
          <a href={ingestHref} className={BUTTON}>
            {CHART_MESSAGES.ingestAction}
          </a>
        </Message>
      )
    case 'filtered-empty':
      return (
        <Message state={state.status}>
          <p className="font-medium text-[var(--color-text)]">{CHART_MESSAGES.filteredEmpty}</p>
          {onClearFilters && (
            <button type="button" onClick={onClearFilters} className={BUTTON}>
              {CHART_MESSAGES.clearFilters}
            </button>
          )}
        </Message>
      )
    case 'not-measured':
      return (
        <Message state={state.status}>
          {/* "—", never 0 or 0%: an unmeasured value is not a measured zero. */}
          <span aria-hidden="true" data-chart-value="" className="text-3xl font-semibold text-[var(--color-text)]">
            {NO_VALUE}
          </span>
          <p className="font-medium text-[var(--color-text)]">{CHART_MESSAGES.notMeasured}</p>
          {state.reason && <p data-chart-reason="">{state.reason}</p>}
        </Message>
      )
    case 'forbidden':
      return (
        <Message state={state.status}>
          <p className="font-medium text-[var(--color-text)]">{CHART_MESSAGES.forbidden}</p>
          {state.requestId && (
            <p className="text-xs">
              Request ID: <code data-chart-request-id="">{state.requestId}</code>
            </p>
          )}
        </Message>
      )
    case 'error': {
      const { error } = state
      const stale = error.kind === 'stale-build'
      const waiting = error.kind === 'rate-limited'
      const expired = error.kind === 'session-expired'
      return (
        <Message state={state.status}>
          <p className="font-medium text-[var(--color-text)]" data-chart-error-kind={error.kind}>
            {stale
              ? STALE_BUILD_MESSAGE
              : waiting
                ? CHART_MESSAGES.rateLimited
                : expired
                  ? CHART_MESSAGES.sessionExpired
                  : CHART_MESSAGES.error}
          </p>
          {!stale && <p>{error.message}</p>}
          {error.requestId && (
            <p className="text-xs">
              Request ID: <code data-chart-request-id="">{error.requestId}</code>
            </p>
          )}
          {stale ? (
            <button type="button" onClick={() => window.location.reload()} className={BUTTON}>
              {STALE_BUILD_ACTION}
            </button>
          ) : expired ? (
            <a href={signInHref} className={BUTTON}>
              {CHART_MESSAGES.signIn}
            </a>
          ) : (
            onRetry && (
              <button type="button" onClick={onRetry} className={BUTTON}>
                {CHART_MESSAGES.retry}
              </button>
            )
          )}
        </Message>
      )
    }
    default:
      return null
  }
}

const ChartFrame = forwardRef<HTMLDivElement, ChartFrameProps>(function ChartFrame(
  {
    title,
    takeaway,
    state,
    scope,
    toolbar,
    headingLevel,
    footer,
    children,
    'data-testid': testId,
    series = null,
    chartType = 'Chart',
    tableExtras,
    axes,
    scopeLabel,
    format,
    changeLabel: changeLabelProp,
    height = 240,
    onClearFilters,
    ingestHref = '/getting-started',
    signInHref = '/login',
    zoomNote,
    exportSlug,
    provenance: provenanceProp,
  },
  ref,
) {
  const titleId = useId()
  const takeawayId = useId()
  const summaryId = useId()
  const tableId = useId()
  const frameId = useId()
  const [tableOpen, setTableOpen] = useState(false)

  const drawn = hasChartData(state)
  const summary = useMemo(
    () => (drawn && series ? summarizeChart({ chartType, series, axes, scope: scopeLabel, format }) : ''),
    [drawn, series, chartType, axes, scopeLabel, format],
  )

  // ── Announcing a change (never the first load) through the page's one announcer ──
  const announcer = useChartAnnouncer()
  // What a reader would notice: the drawn summary, or the state (and error kind).
  const settledKey =
    state.status === 'loading'
      ? null
      : drawn
        ? `drawn:${summary}`
        : state.status === 'error'
          ? `error:${state.error.kind}`
          : state.status
  const busy = state.status === 'loading' || ('revalidating' in state && state.revalidating === true)
  const changeLabel =
    state.status === 'loading'
      ? ''
      : drawn && changeLabelProp
        ? changeLabelProp
        : CHANGE_LABEL[state.status]
  const lastKey = useRef<string | null>(null)
  // Set by the reader's Retry; the result of THAT request is announced assertively.
  const pendingRetry = useRef<{ inflight: boolean } | null>(null)
  useEffect(() => {
    const retrying = pendingRetry.current
    if (retrying) {
      if (busy) {
        retrying.inflight = true
        return
      }
      if (!retrying.inflight && state.status === 'error') return // the request has not started yet
      pendingRetry.current = null
      lastKey.current = settledKey
      const outcome = drawn ? 'loaded' : state.status === 'error' ? 'still could not load' : changeLabel
      announcer?.assertive(`${title} chart: ${outcome}`)
      return
    }
    if (settledKey === null) return
    if (lastKey.current === null) {
      lastKey.current = settledKey // the first load: remembered, not announced
      return
    }
    if (lastKey.current === settledKey) return
    lastKey.current = settledKey
    announcer?.report(frameId, title, changeLabel)
  }, [announcer, busy, changeLabel, drawn, frameId, settledKey, state.status, title])

  const retry = state.status === 'error' ? state.retry : undefined
  const onRetry = useMemo(
    () =>
      retry
        ? () => {
            pendingRetry.current = { inflight: false }
            retry()
          }
        : undefined,
    [retry],
  )

  // The body element, for the forwarded ref, the export menu and the
  // full-screen measurement.
  const bodyEl = useRef<HTMLDivElement | null>(null)
  const bodyRef = useCallback(
    (node: HTMLDivElement | null) => {
      bodyEl.current = node
      assignRef(ref, node)
    },
    [ref],
  )
  const getBody = useCallback(() => bodyEl.current, [])

  // ── Full screen (VIZ-608) ──
  const fullscreenButton = useRef<HTMLButtonElement | null>(null)
  const returnFocus = useCallback(() => fullscreenButton.current, [])
  // Read by the hook at the start of each Escape key press, never while rendering.
  const frameEl = useRef<HTMLDivElement | null>(null)
  const escapeFirst = useCallback(() => dismissibleShowing(frameEl.current), [])
  const {
    ref: attachFullscreen,
    element: fullscreenElement,
    mode: fullscreenMode,
    isFullscreen,
    enter: enterFullscreen,
    exit: exitFullscreen,
  } = useFullscreen<HTMLDivElement>({ returnFocus, escapeFirst })
  const frameRef = useCallback(
    (node: HTMLDivElement | null) => {
      frameEl.current = node
      attachFullscreen(node)
    },
    [attachFullscreen],
  )
  // The body's measured height while full screen. The body is sized by the
  // full-screen layout (CSS), never by its content, so a chart drawn at this
  // height cannot grow the body it was measured from. Forgotten on the way
  // out: the next entry may be another size (a resized window, the other
  // mode), and must not draw its first frame at this one.
  const [measuredBody, setMeasuredBody] = useState<number | null>(null)
  const [measuredFor, setMeasuredFor] = useState(isFullscreen)
  if (measuredFor !== isFullscreen) {
    setMeasuredFor(isFullscreen)
    if (!isFullscreen) setMeasuredBody(null)
  }
  useEffect(() => {
    if (!isFullscreen) return
    const node = bodyEl.current
    if (!node || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver((entries) => {
      const measured = entries[0]?.contentRect.height
      if (typeof measured === 'number' && measured > 0) setMeasuredBody(Math.floor(measured))
    })
    observer.observe(node)
    return () => observer.disconnect()
  }, [isFullscreen])
  const frameHeight = isFullscreen ? (measuredBody ?? unmeasuredFullscreenHeight(height)) : height
  const frameContext = useMemo<ChartFrameContextValue>(
    () => ({
      fullscreen: isFullscreen,
      bodyHeight: isFullscreen ? frameHeight : null,
      portalContainer: isFullscreen ? fullscreenElement : null,
    }),
    [isFullscreen, fullscreenElement, frameHeight],
  )

  const Heading = `h${headingLevel}` as 'h2' | 'h3' | 'h4' | 'h5' | 'h6'
  const describedBy = [takeaway ? takeawayId : null, summary ? summaryId : null].filter(Boolean).join(' ') || undefined
  // A drawn frame whose series has NO points — the histogram with nothing
  // timed states its empty sentence inside a ready state — has no table to
  // open, nothing to export and nothing to enlarge. Hidden, like everything
  // else with nothing to act on (VIZ-101); a frame handed no series at all
  // keeps full screen, as before.
  const plotted = drawn && (series === null || seriesHasPoints(series))
  const canTable = plotted && series !== null
  const showTable = canTable && tableOpen
  const meta = 'meta' in state ? state.meta : null
  const totals = totalsLine(meta)
  const revalidating = drawn && state.revalidating
  // Hidden, never disabled, when there is nothing to act on (VIZ-101) — except
  // that a frame already full screen keeps its way out whatever its state.
  const canFullscreen = plotted || isFullscreen
  const exportProvenance = useMemo(
    () => (provenanceProp !== undefined ? provenanceProp : provenanceFromMeta(meta, null, zoomNote)),
    [provenanceProp, meta, zoomNote],
  )

  const body = drawn ? (
    <ChartErrorBoundary resetKey={state.data}>
      {typeof children === 'function' ? (series ? children(series) : null) : children}
    </ChartErrorBoundary>
  ) : state.status === 'loading' ? (
    <>
      <Skeleton variant="chart" height={frameHeight} />
      <span className="sr-only">
        {CHART_MESSAGES.loading}: {title}
      </span>
    </>
  ) : (
    <StateBody
      state={state}
      onClearFilters={onClearFilters}
      ingestHref={ingestHref}
      signInHref={signInHref}
      onRetry={onRetry}
    />
  )

  return (
    <div
      ref={frameRef}
      data-chart-frame=""
      data-chart-state={state.status}
      data-testid={testId}
      // Full screen is a modal in both modes: the page under it is either not
      // painted (the API) or covered (the overlay), so a screen reader must not
      // wander out into it. The attributes exist only while it is on.
      data-chart-fullscreen={fullscreenMode ?? undefined}
      role={isFullscreen ? 'dialog' : undefined}
      aria-modal={isFullscreen ? true : undefined}
      aria-labelledby={isFullscreen ? titleId : undefined}
      className="flex min-w-0 flex-col gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)] p-4"
    >
      <ChartFrameContext.Provider value={frameContext}>
        {/* In full screen the header REFLOWS (SC 1.4.10): the worded, larger
            toolbar wraps under the title and its buttons wrap among
            themselves, so a 320 px screen never scrolls sideways. Outside
            full screen the classes are exactly the frame's old ones. */}
        <div className={isFullscreen ? 'flex flex-wrap items-start gap-3' : 'flex items-start gap-3'}>
          <div className={isFullscreen ? 'min-w-0 flex-[1_1_12rem]' : 'min-w-0 flex-1'}>
            <Heading
              id={titleId}
              title={title}
              className="line-clamp-2 break-words text-sm font-semibold text-[var(--color-text)]"
            >
              {title}
            </Heading>
            {takeaway && (
              <p id={takeawayId} data-chart-takeaway="" className="mt-0.5 text-sm text-[var(--color-text-secondary)]">
                {takeaway}
              </p>
            )}
          </div>
          {scope && <div data-chart-scope="">{scope}</div>}
          {(toolbar || canTable || canFullscreen) && (
            <div
              role="toolbar"
              aria-label={`${title} actions`}
              data-chart-toolbar=""
              className={
                isFullscreen ? 'flex min-w-0 max-w-full flex-wrap items-center gap-1' : 'flex shrink-0 items-center gap-1'
              }
            >
              {toolbar}
              {canTable && (
                <button
                  type="button"
                  aria-expanded={tableOpen}
                  aria-controls={tableOpen ? tableId : undefined}
                  onClick={() => setTableOpen((open) => !open)}
                  className={BUTTON}
                >
                  {/* One width for both labels, so opening the table never re-wraps the title. */}
                  <SwapLabel
                    labels={TABLE_TOGGLE_LABELS}
                    current={tableOpen ? CHART_MESSAGES.hideTable : CHART_MESSAGES.viewTable}
                  />
                </button>
              )}
              {/* Export needs what the table needs: a drawn chart and its series. */}
              {canTable && series && (
                <ChartExportMenu
                  title={title}
                  chartSlug={exportSlug}
                  getBody={getBody}
                  series={series}
                  format={format}
                  axes={axes}
                  provenance={exportProvenance}
                />
              )}
              {canFullscreen && (
                <button
                  ref={fullscreenButton}
                  type="button"
                  data-chart-fullscreen-toggle=""
                  aria-label={isFullscreen ? undefined : CHART_MESSAGES.fullScreen}
                  title={isFullscreen ? undefined : CHART_MESSAGES.fullScreen}
                  // Called straight from the click: the Fullscreen API refuses a
                  // request that is not made inside a user gesture.
                  onClick={isFullscreen ? exitFullscreen : enterFullscreen}
                  className={ICON_BUTTON}
                >
                  {isFullscreen ? (
                    <>
                      <Minimize2 aria-hidden="true" className="h-4 w-4" />
                      {CHART_MESSAGES.exitFullScreen}
                    </>
                  ) : (
                    <Maximize2 aria-hidden="true" className="h-4 w-4" />
                  )}
                </button>
              )}
            </div>
          )}
        </div>

        <div
          ref={bodyRef}
          role="group"
          aria-labelledby={titleId}
          aria-describedby={describedBy}
          aria-busy={state.status === 'loading' || revalidating ? true : undefined}
          tabIndex={-1}
          data-chart-body=""
          data-revalidating={revalidating ? 'true' : undefined}
          // In full screen the body's size comes from the full-screen layout in
          // index.css, which an inline min-height would override.
          style={isFullscreen ? undefined : { minHeight: height }}
          className={`min-w-0 transition-opacity focus:outline-none ${revalidating ? 'opacity-60' : ''}`}
        >
          {body}
        </div>

        {summary && (
          <p id={summaryId} className="sr-only" data-chart-summary="">
            {summary}
          </p>
        )}

        {showTable && series && (
          <div id={tableId}>
            <ChartTable caption={`${title} — data table`} series={series} axes={axes} format={format} />
            {tableExtras}
          </div>
        )}

        {(state.status === 'truncated' || totals || zoomNote || footer) && (
          <div data-chart-footer="" className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--color-text-secondary)]">
            {state.status === 'truncated' && (
              <span data-chart-truncation="" className="flex items-center gap-2">
                Showing top {formatNumber(state.shown)} of {formatNumber(state.total)}
                {canTable && !tableOpen && (
                  <button type="button" onClick={() => setTableOpen(true)} className={BUTTON}>
                    View the top {formatNumber(state.shown)} as a table
                  </button>
                )}
              </span>
            )}
            {/* While zoomed the totals are still the WHOLE window's (the
                envelope has no per-day run counts), and they sit right above a
                note saying the chart shows only some days: labelled as the
                window's, as the export labels them. */}
            {totals && <span data-chart-totals="">{zoomNote ? `${WINDOW_TOTALS_LABEL}: ${totals}` : totals}</span>}
            {zoomNote && <span data-chart-zoom-note="">{zoomNote}</span>}
            {footer}
          </div>
        )}

        {/* Full screen hides the page's announcer from assistive technology
            (the API prunes it, the overlay makes the page inert), so the one
            announcer speaks from in here meanwhile. Last, so the CSS that
            addresses the header as the first child still does. */}
        {isFullscreen && <ChartAnnouncerOutlet />}
      </ChartFrameContext.Provider>
    </div>
  )
})

export default ChartFrame
