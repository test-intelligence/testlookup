/**
 * The rows panel (VIZ-208 / 602): the executions behind one chart mark, one
 * page at a time, in a `SidePanel` beside the page (focus moves in on open,
 * Escape closes, focus returns to the control that opened it).
 *
 * Shared by every Wave-3 host: a heatmap cell, a treemap leaf, a failure group,
 * a scatter point, a drill level. The host says which rows (`RowsPanelProps`,
 * pinned in `RowsPanel.model.ts`); the panel asks `chart-data/rows` through the
 * kit's request path (`chartGet`: abortable, toast-free, request id on error,
 * inside the 4-in-flight cap) and validates the body before drawing it.
 *
 * Every name (test, suite, release, error line) is untrusted text and reaches
 * the DOM as React text only. The panel speaks once, when its rows arrive,
 * through the page's one announcer; it has no live region of its own.
 */
import { useEffect, useMemo, useRef, useState, type ReactElement } from 'react'
import SidePanel from '@/components/ui/SidePanel'
import ScopedLink from '@/components/ui/ScopedLink'
import StatusBadge from '@/components/ui/StatusBadge'
import { useChartAnnouncer } from '@/components/charts/ChartAnnouncer'
import { useChartData, type ChartKey } from '@/hooks/useChartData'
import { chartGet } from '@/services/chartApi'
import { formatDateTime, formatDuration, NO_VALUE } from '@/utils/formatters'
import {
  reconciliationNotice,
  ROWS_ACCESSORS,
  ROWS_URL,
  rowsAnnouncement,
  rowsCountText,
  rowsHeading,
  rowsRequestParams,
  selectorsKey,
  validateRowsResponse,
  type RowsItem,
  type RowsPanelProps,
  type RowsResponse,
} from './RowsPanel.model'

export type { RowsPanelProps } from './RowsPanel.model'

const PAGER_BUTTON =
  'min-h-6 rounded border border-[var(--color-border)] px-2 py-0.5 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] disabled:opacity-40 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]'

function RowsTable({ items }: { items: readonly RowsItem[] }) {
  return (
    <table className="w-full text-xs" data-rows-table="">
      <caption className="sr-only">Executions, newest first</caption>
      <thead>
        <tr className="text-left text-[var(--color-text-secondary)]">
          <th scope="col" className="w-[40%] py-1 pr-2 font-medium">Test</th>
          <th scope="col" className="py-1 pr-2 font-medium">Status</th>
          <th scope="col" className="py-1 pr-2 font-medium">Suite</th>
          <th scope="col" className="whitespace-nowrap py-1 pr-2 font-medium">Duration</th>
          <th scope="col" className="py-1 pr-2 font-medium">Release</th>
          <th scope="col" className="whitespace-nowrap py-1 font-medium">Run date</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={item.id} className="border-t border-[var(--color-border)] align-top" data-rows-item={item.id}>
            <th scope="row" className="break-words py-1 pr-2 text-left font-normal">
              <ScopedLink
                to={`/runs/${encodeURIComponent(item.run_id)}/tests/${encodeURIComponent(item.id)}`}
                className="text-[var(--color-accent)] underline-offset-2 hover:underline"
              >
                {item.test_name}
              </ScopedLink>
              {item.error_line && (
                <span className="mt-0.5 block break-words text-[var(--color-text-muted)]">{item.error_line}</span>
              )}
            </th>
            <td className="py-1 pr-2">
              <StatusBadge status={item.status} />
            </td>
            <td className="break-words py-1 pr-2">{item.suite ?? NO_VALUE}</td>
            <td className="whitespace-nowrap py-1 pr-2">{formatDuration(item.duration_ms)}</td>
            <td className="break-words py-1 pr-2">{item.release ? (item.release.name ?? item.release.id) : NO_VALUE}</td>
            {/* A date never wraps a character per line: the table grows and its region scrolls instead. */}
            <td className="whitespace-nowrap py-1">{formatDateTime(item.created_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** One open panel's body. Keyed by its selectors, so a new mark starts on page 1. */
function RowsBody({ selectors, chart, scope, title, expected }: Omit<RowsPanelProps, 'onClose'>) {
  const announcer = useChartAnnouncer()
  const [page, setPage] = useState(1)
  const params = useMemo(() => rowsRequestParams({ selectors, chart, scope }, page), [selectors, chart, scope, page])
  const key: ChartKey | null = params === null ? null : (['chart-rows', params] as const)
  const state = useChartData<RowsResponse, ChartKey>(
    key,
    async (_key, { signal }) => chartGet(ROWS_URL, { params: params ?? {}, signal }),
    { validate: validateRowsResponse, everHadData: true, accessors: ROWS_ACCESSORS },
  )

  // One announcement per opened panel, when its first page arrives (the
  // reader's own action, so assertive; the page's announcer, never ours).
  const spoken = useRef(false)
  const total = state.status === 'ready' || state.status === 'truncated' ? state.data.total : state.status === 'filtered-empty' ? 0 : null
  useEffect(() => {
    if (spoken.current || total === null) return
    spoken.current = true
    announcer?.assertive(rowsAnnouncement(title, total))
  }, [announcer, title, total])

  switch (state.status) {
    case 'loading':
    case 'never-had-data':
      return <p className="text-xs text-[var(--color-text-muted)]" data-rows-state="loading">Loading executions…</p>
    case 'forbidden':
      return <p className="text-xs" data-rows-state="forbidden">You do not have access to these executions.</p>
    case 'not-measured':
      return <p className="text-xs" data-rows-state="not-measured">{state.reason || 'These executions were not measured.'}</p>
    case 'filtered-empty':
      return <p className="text-xs" data-rows-state="empty">No executions match this selection.</p>
    case 'error':
      return (
        <div className="text-xs" data-rows-state="error">
          <p>{state.error.message}</p>
          {state.error.requestId && <p className="text-[var(--color-text-muted)]">Request id: {state.error.requestId}</p>}
          {state.retry && (
            <button type="button" className={`${PAGER_BUTTON} mt-1`} onClick={state.retry}>
              Retry
            </button>
          )}
        </div>
      )
    case 'ready':
    case 'truncated': {
      const data = state.data
      const notice = reconciliationNotice(data, expected)
      return (
        <div data-rows-state="ready">
          <p className="text-sm font-medium" data-rows-count="">{rowsCountText(data)}</p>
          {notice && (
            <p className="mt-1 text-xs text-[var(--color-text-secondary)]" data-rows-notice="">
              {notice}
            </p>
          )}
          {/* Six columns are wider than the 420 px panel: the table scrolls sideways
              inside a named, focusable region (keyboard users can scroll it too),
              never clipping "Run date" (B0, Wave 3). Classes the app already uses. */}
          <div
            role="region"
            aria-label="Executions table"
            tabIndex={0}
            data-rows-scroller=""
            className="mt-2 max-w-full overflow-x-auto rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
          >
            <RowsTable items={data.items} />
          </div>
          {data.pages > 1 && (
            <nav aria-label="Pages of executions" className="mt-2 flex items-center gap-2 text-xs">
              <button type="button" className={PAGER_BUTTON} disabled={page <= 1} onClick={() => setPage(page - 1)}>
                Previous page
              </button>
              <span data-rows-page="">
                Page {data.page} of {data.pages}
              </span>
              <button type="button" className={PAGER_BUTTON} disabled={page >= data.pages} onClick={() => setPage(page + 1)}>
                Next page
              </button>
            </nav>
          )}
        </div>
      )
    }
  }
}

export function RowsPanel({ selectors, chart, scope, title, expected, onClose }: RowsPanelProps): ReactElement | null {
  if (selectors.length === 0) return null
  return (
    <SidePanel open onClose={onClose} title={rowsHeading(title)} closeLabel="Close executions">
      <RowsBody key={selectorsKey(selectors)} selectors={selectors} chart={chart} scope={scope} title={title} expected={expected} />
    </SidePanel>
  )
}

export default RowsPanel
