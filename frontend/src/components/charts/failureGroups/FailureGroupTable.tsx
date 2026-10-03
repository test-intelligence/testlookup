/**
 * The ranked table of failure groups (VIZ-504): ALWAYS shown with the plot,
 * because it is the accessible equivalent of the circles and the primary
 * keyboard path — every group's name is a button that opens its details, in
 * rank order, one Tab each. The plot is the overview; this is the list.
 *
 * Every figure is the server's: a figure it did not send is "—", never 0.
 * Every name is React text (a raw error line, `<img onerror>` included).
 * The ten largest are listed; "Show all" lists the rest (at most 200).
 */
import { useId, useState } from 'react'
import { formatNumber } from '@/utils/formatters'
import { useContainerWidth } from '../chartLayout'
import { patternFill, renderPatterns, useChartPatternPrefix } from '../patterns'
import Sparkline from '../Sparkline'
import { CHART_VARS } from '../tokens'
import {
  categoryKey,
  categoryLabel,
  formatShare,
  trendLabel,
  utcDay,
  FAILURE_CATEGORY_KEYS,
  type FailureGroup,
  type TrendGrain,
} from './failureGroups.model'
import { WRAP_ANYWHERE } from './plot.model'
import { categoryPatternId, categoryPatternSpecs } from './categoryPatterns'

/** Rows listed before "Show all". */
export const TABLE_FIRST_ROWS = 10

const TABLE_COLUMNS = [
  'Rank',
  'Failure group',
  'Failures',
  'Share',
  'Tests',
  'Runs',
  'First seen',
  'Last seen',
  'Category',
  'Trend',
] as const
type Column = (typeof TABLE_COLUMNS)[number]

/**
 * Which columns a frame this wide keeps (R2-B F-05: the ten columns need 760 px,
 * 838 in DejaVu Sans, and scrolled sideways at 375/640/768 with no cue). Below
 * `COMPACT_BELOW` the tests, runs, dates and trend go (the category stays: it
 * is the plot's colour key); below `NARROW_BELOW` the category moves under the
 * group's name. Every figure dropped is in the group's panel, which the name
 * opens, and a note under the table says so. Not measured yet (0): every column.
 */
export const COMPACT_BELOW = 768
export const NARROW_BELOW = 480
type TableColumns = 'full' | 'compact' | 'narrow'

function tableColumns(width: number): TableColumns {
  if (!(width > 0) || width >= COMPACT_BELOW) return 'full'
  return width >= NARROW_BELOW ? 'compact' : 'narrow'
}

const KEPT: Record<TableColumns, ReadonlySet<Column>> = {
  full: new Set(TABLE_COLUMNS),
  compact: new Set<Column>(['Rank', 'Failure group', 'Failures', 'Share', 'Category']),
  narrow: new Set<Column>(['Rank', 'Failure group', 'Failures', 'Share']),
}

export const DROPPED_COLUMNS_NOTE = 'Open a group for its tests, runs, first and last seen, and its trend.'

export interface FailureGroupTableProps {
  /** Rank order. */
  groups: readonly FailureGroup[]
  trendGrain: TrendGrain | null
  /** Open a group's details. */
  onOpen: (group: FailureGroup) => void
  /** The group whose details are open (its button is drawn selected). */
  selectedId?: string | null
  caption?: string
  /** The container's width when known (tests, gallery); measured otherwise. */
  width?: number
}

const CELL = 'px-2 py-1 align-top tabular-nums'
const OPEN_BUTTON =
  'text-left text-[var(--color-accent-ink)] underline-offset-2 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]'

const BUTTON =
  'rounded border border-[var(--color-border-light)] px-3 py-1 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]'

export default function FailureGroupTable({
  groups,
  trendGrain,
  onOpen,
  selectedId = null,
  caption = 'Failure groups, largest first',
  width: fixedWidth,
}: FailureGroupTableProps) {
  const prefix = useChartPatternPrefix()
  const [measure, measuredWidth] = useContainerWidth<HTMLDivElement>()
  const columns = tableColumns(fixedWidth ?? measuredWidth)
  const kept = KEPT[columns]
  const shows = (column: Column) => kept.has(column)
  const [all, setAll] = useState(false)
  const tableId = useId()
  const rows = all ? groups : groups.slice(0, TABLE_FIRST_ROWS)
  const hidden = groups.length - rows.length
  const trendName = trendLabel(trendGrain)
  return (
    <div ref={measure} data-group-table="" data-columns={columns} className="min-w-0">
      <svg width={0} height={0} aria-hidden="true" focusable="false" className="absolute">
        <defs>{renderPatterns(categoryPatternSpecs(prefix, FAILURE_CATEGORY_KEYS))}</defs>
      </svg>
      <div
        role="region"
        aria-labelledby={`${tableId}-caption`}
        tabIndex={0}
        className="overflow-x-auto rounded border border-[var(--color-border)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
      >
        <table id={tableId} className="w-full border-collapse text-left text-xs text-[var(--color-text)]">
          <caption id={`${tableId}-caption`} className="px-2 py-1 text-left text-sm font-medium text-[var(--color-text)]">
            {caption}
          </caption>
          <thead>
            <tr>
              {TABLE_COLUMNS.filter(shows).map((column) => (
                <th
                  key={column}
                  scope="col"
                  className="border-b border-[var(--color-border)] bg-[var(--color-bg-card)] px-2 py-1 font-semibold"
                >
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((group) => {
              const key = categoryKey(group.dominantCategory)
              const category = (
                <span className="inline-flex items-center gap-1.5">
                  <svg width={12} height={12} aria-hidden="true" focusable="false">
                    <rect
                      x={0.5}
                      y={0.5}
                      width={11}
                      height={11}
                      rx={2}
                      fill={patternFill(categoryPatternId(prefix, key))}
                      stroke={CHART_VARS.border}
                    />
                  </svg>
                  {categoryLabel(group.dominantCategory)}
                </span>
              )
              return (
                <tr key={group.id} data-group-row={group.rank} className="border-b border-[var(--color-border)] last:border-b-0">
                  <td className={CELL}>{group.rank}</td>
                  <th
                    scope="row"
                    className={
                      columns === 'full'
                        ? 'min-w-[12rem] max-w-md px-2 py-1 align-top font-normal'
                        : 'max-w-md px-2 py-1 align-top font-normal'
                    }
                  >
                    <button
                      type="button"
                      className={group.id === selectedId ? `${OPEN_BUTTON} font-semibold` : OPEN_BUTTON}
                      style={WRAP_ANYWHERE}
                      data-selected={group.id === selectedId ? 'true' : undefined}
                      onClick={() => onOpen(group)}
                      data-group-open={group.rank}
                    >
                      {group.label}
                    </button>
                    {/* The category column's place on a narrow frame: under the name. */}
                    {!shows('Category') && (
                      <span className="mt-0.5 block text-[var(--color-text-secondary)]" data-group-row-category="">
                        {category}
                      </span>
                    )}
                  </th>
                  <td className={CELL}>{formatNumber(group.failureCount)}</td>
                  <td className={CELL}>{formatShare(group.share)}</td>
                  {shows('Tests') && <td className={CELL}>{formatNumber(group.affectedTests)}</td>}
                  {shows('Runs') && <td className={CELL}>{formatNumber(group.affectedRuns)}</td>}
                  {shows('First seen') && <td className={`${CELL} whitespace-nowrap`}>{utcDay(group.firstSeen)}</td>}
                  {shows('Last seen') && <td className={`${CELL} whitespace-nowrap`}>{utcDay(group.lastSeen)}</td>}
                  {shows('Category') && <td className={`${CELL} whitespace-nowrap`}>{category}</td>}
                  {shows('Trend') && (
                    <td className={`${CELL} w-24`}>
                      {group.trend.length >= 2 ? (
                        <Sparkline
                          series={group.trend.map((point) => point.y)}
                          label={`${trendName}, group ${group.rank}`}
                          tone="bad"
                          height={20}
                        />
                      ) : (
                        <>
                          <span aria-hidden="true">—</span>
                          <span className="sr-only">{`${trendName}: not enough points`}</span>
                        </>
                      )}
                    </td>
                  )}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {columns !== 'full' && (
        <p data-group-table-note="" className="m-0 mt-1 text-xs text-[var(--color-text-secondary)]">
          {DROPPED_COLUMNS_NOTE}
        </p>
      )}
      {groups.length > TABLE_FIRST_ROWS && (
        <button
          type="button"
          className={`${BUTTON} mt-2`}
          aria-controls={tableId}
          aria-expanded={all}
          onClick={() => setAll((open) => !open)}
        >
          {all
            ? `Show the ${formatNumber(TABLE_FIRST_ROWS)} largest`
            : `Show all ${formatNumber(groups.length)} groups (${formatNumber(hidden)} more)`}
        </button>
      )}
    </div>
  )
}
