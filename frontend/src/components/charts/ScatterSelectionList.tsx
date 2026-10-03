/**
 * The tests a scatter selection holds (VIZ-506), as a sortable table: the
 * accessible alternative to the brush (which is pointer-only) and the place a
 * selection is READ — names never fit on a 5,000-point canvas.
 *
 * At most `SELECTION_LIST_LIMIT` rows, in the reader's chosen order; the rest
 * are counted, never silently dropped. Every name is React text (untrusted CI
 * input), cut with an ellipsis by CSS, with the whole name in `title`. Each
 * row can open the test's executions ("View rows") when the host offers it.
 */
import { useMemo, useState } from 'react'
import type { PointsChart } from '@/lib/viz/contracts'
import { formatNumber } from '@/utils/formatters'
import { formatPlainValue } from './chartText'
import { useChartFullscreen } from './chartFrameContext'
import {
  DEFAULT_SORT,
  QUADRANT_LABELS,
  SELECTION_LIST_LIMIT,
  formatX,
  formatY,
  quadrantOf,
  selectionSentence,
  sortSelection,
  type ScatterSortKey,
  type SortDirection,
} from './testScatter.model'

export interface ScatterSelectionListProps {
  chart: PointsChart
  /** The selected points (indices into `chart.points`). */
  indices: readonly number[]
  /** "View rows" for one point; absent = no button. */
  onViewRows?: (index: number) => void
  headingLevel?: 3 | 4 | 5
}

export const SELECTION_LIST_TITLE = 'Selected tests'
export const VIEW_ROWS_LABEL = 'View rows'

const BUTTON =
  'min-h-6 rounded border border-[var(--color-border-light)] px-2 py-0.5 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]'
/**
 * The header cells: the Wave 3 tables' header (ChartTable, the failure groups table), not the
 * letter-spaced uppercase `.th` (F-08), in the table's secondary text colour, not `.th`'s muted one,
 * which is 4.15:1 on the lab theme (under AA for 12 px text; B0's axe run). Existing classes only.
 */
const HEADER =
  'sticky top-0 border-b border-[var(--color-border)] bg-[var(--color-bg-card)] px-2 py-1 font-semibold text-[var(--color-text-secondary)]'
/** A body cell, on the headers' gutter (as ChartTable's). */
const CELL = 'px-2 py-1'
const SORT_BUTTON =
  'inline-flex min-h-6 items-center gap-1 rounded px-1 font-semibold hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]'

/** A new column starts at the direction a reader of it wants first: names A-Z, numbers high to low. */
const FIRST_DIRECTION: Record<ScatterSortKey, SortDirection> = { label: 'asc', x: 'desc', y: 'desc', size: 'desc' }

export function ScatterSelectionList({ chart, indices, onViewRows, headingLevel = 4 }: ScatterSelectionListProps) {
  const fullscreen = useChartFullscreen()
  const [sort, setSort] = useState(DEFAULT_SORT)
  const sorted = useMemo(() => sortSelection(chart.points, indices, sort.key, sort.direction), [chart, indices, sort])
  const shown = sorted.slice(0, SELECTION_LIST_LIMIT)
  const hidden = sorted.length - shown.length
  const Heading = `h${headingLevel}` as 'h3' | 'h4' | 'h5'
  const medians = chart.medians

  const columns: { key: ScatterSortKey; label: string; numeric: boolean }[] = [
    { key: 'label', label: 'Test', numeric: false },
    { key: 'x', label: chart.x.label, numeric: true },
    { key: 'y', label: chart.y.label, numeric: true },
    { key: 'size', label: chart.size.label, numeric: true },
  ]
  const sortBy = (key: ScatterSortKey) =>
    setSort((now) => (now.key === key ? { key, direction: now.direction === 'asc' ? 'desc' : 'asc' } : { key, direction: FIRST_DIRECTION[key] }))

  // Drawn inside its frame's card (F-08); full screen enlarges the plot alone
  // (its height is the screen's), and the list is back on the way out.
  if (fullscreen) return null
  return (
    <section data-scatter-selection="" aria-label={SELECTION_LIST_TITLE} className="mt-3">
      {/* The count once ("3 tests selected"); the region is named by the title. */}
      <Heading className="text-sm font-semibold text-[var(--color-text)]">
        <span data-scatter-selection-count="">{selectionSentence(indices.length)}</span>
      </Heading>
      {indices.length === 0 ? null : (
        <>
          {/*
            A capped, scrolling box (as ChartTable's): 200 rows never push the
            page down by metres. It is itself focusable and named, so a keyboard
            can scroll it even when the rows hold no button.
          */}
          <div
            role="region"
            aria-label={`${SELECTION_LIST_TITLE}, scrollable`}
            tabIndex={0}
            data-scatter-selection-box=""
            className="mt-2 max-h-96 overflow-auto rounded border border-[var(--color-border)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
          >
            <table className="w-full border-collapse text-left text-xs text-[var(--color-text)]">
              <caption className="sr-only">{`${SELECTION_LIST_TITLE}, ${selectionSentence(indices.length)}`}</caption>
              <thead>
                <tr>
                  {columns.map((column) => {
                    const active = sort.key === column.key
                    return (
                      <th
                        key={column.key}
                        scope="col"
                        aria-sort={active ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}
                        className={`${HEADER} ${column.numeric ? 'text-right' : 'text-left'}`}
                      >
                        <button type="button" data-sort-key={column.key} onClick={() => sortBy(column.key)} className={SORT_BUTTON}>
                          {column.label}
                          <span aria-hidden="true">{active ? (sort.direction === 'asc' ? '▲' : '▼') : ''}</span>
                        </button>
                      </th>
                    )
                  })}
                  {medians ? (
                    <th scope="col" className={`${HEADER} text-left`}>
                      Quadrant
                    </th>
                  ) : null}
                  {onViewRows ? (
                    <th scope="col" className={HEADER}>
                      <span className="sr-only">Actions</span>
                    </th>
                  ) : null}
                </tr>
              </thead>
              <tbody>
                {shown.map((index) => {
                  const point = chart.points[index]
                  return (
                    <tr key={point.id} data-point-id={point.id} className="border-b border-[var(--color-border)] last:border-b-0">
                      <th scope="row" className={`${CELL} max-w-[280px] truncate text-left font-normal`} title={point.label}>
                        {point.label}
                      </th>
                      <td className={`${CELL} text-right tabular-nums`}>{formatX(chart, point.x)}</td>
                      <td className={`${CELL} text-right tabular-nums`}>{formatY(chart, point.y)}</td>
                      <td className={`${CELL} text-right tabular-nums`}>{formatPlainValue(point.size)}</td>
                      {medians ? <td className={CELL}>{QUADRANT_LABELS[quadrantOf(point, medians)]}</td> : null}
                      {onViewRows ? (
                        <td className={`${CELL} text-right`}>
                          <button
                            type="button"
                            data-view-rows={point.id}
                            aria-label={`${VIEW_ROWS_LABEL}: ${point.label}`}
                            onClick={() => onViewRows(index)}
                            className={BUTTON}
                          >
                            {VIEW_ROWS_LABEL}
                          </button>
                        </td>
                      ) : null}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {hidden > 0 ? (
            <p data-scatter-selection-more="" className="mt-1 text-xs text-[var(--color-text-secondary)]">
              {`And ${formatNumber(hidden)} more, not listed: narrow the selection, or open the table view for every test.`}
            </p>
          ) : null}
        </>
      )}
    </section>
  )
}

export default ScatterSelectionList
