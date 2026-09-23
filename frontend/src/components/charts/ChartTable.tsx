/**
 * "View as table" (VIZ-105): a semantic `<table>` built from the SAME
 * normalised series the renderer draws (`chartTableModel`), with a caption,
 * column headers and row headers. Focus moves to the caption when it opens,
 * so a screen-reader user lands on the table's name.
 *
 * Above `PAGINATE_ABOVE` rows it paginates (`PAGE_SIZE` a page) rather than
 * putting thousands of rows in the DOM.
 */
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import type { ChartSeries } from '@/lib/viz/contracts'
import { chartTableModel, type ChartAxes, type SeriesFormat } from './chartText'

export const PAGINATE_ABOVE = 500
export const PAGE_SIZE = 100

interface Props {
  caption: string
  series: ChartSeries
  axes?: ChartAxes
  /** One formatter, or one per series keyed on `series.key` (see `SeriesFormat`). */
  format?: SeriesFormat
  /** Move focus to the caption on mount (true when the reader opened it). */
  autoFocus?: boolean
}

export default function ChartTable({ caption, series, axes, format, autoFocus = true }: Props) {
  const model = useMemo(() => chartTableModel(series, axes, format), [series, axes, format])
  const captionRef = useRef<HTMLTableCaptionElement>(null)
  const [page, setPage] = useState(0)
  const statusId = useId()

  const paginated = model.rows.length > PAGINATE_ABOVE
  const pages = paginated ? Math.ceil(model.rows.length / PAGE_SIZE) : 1
  const current = Math.min(page, pages - 1)
  const start = paginated ? current * PAGE_SIZE : 0
  const rows = paginated ? model.rows.slice(start, start + PAGE_SIZE) : model.rows

  useEffect(() => {
    if (autoFocus) captionRef.current?.focus()
  }, [autoFocus])

  return (
    <div data-chart-table className="mt-3 max-h-96 overflow-auto rounded border border-[var(--color-border)]">
      {model.warnings.length > 0 && (
        <ul data-chart-table-warnings="" className="border-b border-[var(--color-border)] px-2 py-1 text-xs text-[var(--color-text)]">
          {model.warnings.map((warning, index) => (
            <li key={index}>{warning}</li>
          ))}
        </ul>
      )}
      <table className="w-full border-collapse text-left text-xs text-[var(--color-text)]">
        <caption
          ref={captionRef}
          tabIndex={-1}
          className="px-2 py-1 text-left text-sm font-medium text-[var(--color-text)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
        >
          {caption}
        </caption>
        <thead>
          <tr>
            {model.columns.map((column, index) => (
              <th
                key={index}
                scope="col"
                className="sticky top-0 border-b border-[var(--color-border)] bg-[var(--color-bg-card)] px-2 py-1 font-semibold"
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr
              key={start + index}
              data-chart-row-duplicate={row.duplicate ? '' : undefined}
              className="border-b border-[var(--color-border)] last:border-b-0"
            >
              <th scope="row" className="px-2 py-1 font-medium">
                {row.header}
              </th>
              {row.cells.map((cell, cellIndex) => (
                <td key={cellIndex} className="px-2 py-1 tabular-nums">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {paginated && (
        <div className="flex items-center justify-between gap-2 border-t border-[var(--color-border)] px-2 py-1 text-xs">
          <button
            type="button"
            onClick={() => setPage(current - 1)}
            disabled={current === 0}
            aria-describedby={statusId}
            className="rounded border border-[var(--color-border-light)] px-2 py-0.5 disabled:opacity-50"
          >
            Previous rows
          </button>
          <span id={statusId} aria-live="polite">
            Rows {start + 1}–{start + rows.length} of {model.rows.length}
          </span>
          <button
            type="button"
            onClick={() => setPage(current + 1)}
            disabled={current >= pages - 1}
            aria-describedby={statusId}
            className="rounded border border-[var(--color-border-light)] px-2 py-0.5 disabled:opacity-50"
          >
            Next rows
          </button>
        </div>
      )}
    </div>
  )
}
