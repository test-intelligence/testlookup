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
 * and no state that changes on hover. The forwarded ref is the chart body, for
 * export and full-screen later (VIZ-606 / VIZ-608).
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
import type { ChartSeries, EnvelopeMeta } from '@/lib/viz/contracts'
import { formatNumber } from '@/utils/formatters'
import Skeleton from '@/components/ui/Skeleton'
import ChartErrorBoundary from './ChartErrorBoundary'
import ChartTable from './ChartTable'
import { hasChartData, type ChartState } from './chartState'
import { NO_VALUE, summarizeChart, type ChartAxes, type SeriesFormat } from './chartText'
import { STALE_BUILD_ACTION, STALE_BUILD_MESSAGE } from './engines/lazyChartEngine'
import { CHART_MESSAGES } from './chartMessages'
import { useChartAnnouncer } from './ChartAnnouncer'

export type ChartHeadingLevel = 2 | 3 | 4 | 5 | 6

export interface ChartFrameProps {
  title: string
  /** One line, e.g. "Down 3.1 pts in 30 days". Omitted → nothing is rendered in its place. */
  takeaway?: string
  state: ChartState<unknown>
  /** Scope badge slot (VIZ-307). */
  scope?: ReactNode
  /** Toolbar actions (export / full screen arrive in VIZ-606 / VIZ-608). */
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

const BUTTON =
  'rounded border border-[var(--color-border-light)] px-3 py-1 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]'

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

function totalsLine(meta: EnvelopeMeta | null | undefined): string | null {
  const totals = meta?.totals
  if (!totals) return null
  return `${formatNumber(totals.matched_runs)} of ${formatNumber(totals.total_runs)} runs · ${formatNumber(
    totals.matched_executions,
  )} of ${formatNumber(totals.total_executions)} executions`
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

  const bodyRef = useCallback((node: HTMLDivElement | null) => assignRef(ref, node), [ref])

  const Heading = `h${headingLevel}` as 'h2' | 'h3' | 'h4' | 'h5' | 'h6'
  const describedBy = [takeaway ? takeawayId : null, summary ? summaryId : null].filter(Boolean).join(' ') || undefined
  const canTable = drawn && series !== null
  const showTable = canTable && tableOpen
  const meta = 'meta' in state ? state.meta : null
  const totals = totalsLine(meta)
  const revalidating = drawn && state.revalidating

  const body = drawn ? (
    <ChartErrorBoundary resetKey={state.data}>
      {typeof children === 'function' ? (series ? children(series) : null) : children}
    </ChartErrorBoundary>
  ) : state.status === 'loading' ? (
    <>
      <Skeleton variant="chart" height={height} />
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
      data-chart-frame=""
      data-chart-state={state.status}
      data-testid={testId}
      className="flex min-w-0 flex-col gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)] p-4"
    >
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
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
        {(toolbar || canTable) && (
          <div role="toolbar" aria-label={`${title} actions`} data-chart-toolbar="" className="flex shrink-0 items-center gap-1">
            {toolbar}
            {canTable && (
              <button
                type="button"
                aria-expanded={tableOpen}
                aria-controls={tableOpen ? tableId : undefined}
                onClick={() => setTableOpen((open) => !open)}
                className={BUTTON}
              >
                {tableOpen ? CHART_MESSAGES.hideTable : CHART_MESSAGES.viewTable}
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
        style={{ minHeight: height }}
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

      {(state.status === 'truncated' || totals || footer) && (
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
          {totals && <span data-chart-totals="">{totals}</span>}
          {footer}
        </div>
      )}
    </div>
  )
})

export default ChartFrame
