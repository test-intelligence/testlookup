/**
 * VIZ-501: what each heatmap kind is called, drawn and measured as, and the
 * section's words — the pure half of `HeatmapSection.tsx` (a component file
 * that exports only components keeps React Fast Refresh working).
 */
import type { DrillLevel, MatrixChart } from '@/lib/viz/contracts'
import type { HeatmapMarkDimensions, HeatmapNouns, HeatmapRowSort } from '@/components/charts/heatmapFromMatrix'
import { matrixAxisKeys } from '@/components/charts/chartText'
import { RUN_AXIS_TITLE } from '@/components/charts/heatmapFromMatrix'
import type { ChartMark } from '@/components/charts/marks'
import type { RowsChartSpec, RowsExpectation } from './RowsPanel.model'
import type { HeatmapKind } from './sectionContracts'

/** What each kind is called, drawn and measured as. */
export interface HeatmapKindSpec {
  /**
   * The selector's label: what the COLUMNS are (the rows of every kind a host
   * offers together are the same). Short on purpose (F-11): "Suite ×
   * environment" and "Suite × release" beside the toolbar's own buttons left
   * the frame's title two lines in a 976 px frame.
   */
  option: string
  /** The frame's title. */
  title: string
  nouns: HeatmapNouns
  rowAxis: string
  columnAxis: string
  /** A status matrix (no rate, no colour scale to fit). */
  status: boolean
  /** Why this kind asks nothing in All Projects, or `null` when it can. */
  allProjectsReason: string | null
  /** The shortest window, days, that is worth drawing (a suite x day of one day is one column). */
  minDays: number
  /**
   * What a cell's row and column are as C5 dimensions: the selectors "View
   * rows" sends (`bucket_<dimension>` on `/analytics/chart-data/rows`). A run
   * is not a dimension the rows endpoint selects by, so a test x run cell
   * lists the TEST's executions in the window.
   */
  marks: HeatmapMarkDimensions
}

export const ONE_DAY_HEATMAP_REASON =
  'a one-day window is one column per suite, which is not a trend; pick 7 days or more to compare days'

export const HEATMAP_KIND_SPECS: Readonly<Record<HeatmapKind, HeatmapKindSpec>> = {
  suite_day: {
    option: 'Day',
    title: 'Suite pass rate by day',
    nouns: { rows: ['suite', 'suites'], columns: ['day', 'days'] },
    rowAxis: 'Suite',
    columnAxis: 'Day (UTC)',
    status: false,
    allProjectsReason: null,
    minDays: 2,
    marks: { row: 'suite', column: 'day' },
  },
  test_run: {
    option: 'Run',
    title: 'Test results by run',
    nouns: { rows: ['test', 'tests'], columns: ['run', 'runs'] },
    rowAxis: 'Test',
    // The columns are labelled by build (F-19): the axis title, the table and the summary say so.
    columnAxis: RUN_AXIS_TITLE,
    status: true,
    allProjectsReason: 'runs of several projects are not one sequence; pick a project to see its tests run by run',
    minDays: 1,
    marks: { row: 'test', column: null },
  },
  suite_environment: {
    option: 'Environment',
    title: 'Suite pass rate by environment',
    nouns: { rows: ['suite', 'suites'], columns: ['environment', 'environments'] },
    rowAxis: 'Suite',
    columnAxis: 'Environment',
    status: false,
    allProjectsReason: null,
    minDays: 1,
    marks: { row: 'suite', column: 'environment' },
  },
  suite_release: {
    option: 'Release',
    title: 'Suite pass rate by release',
    nouns: { rows: ['suite', 'suites'], columns: ['release', 'releases'] },
    rowAxis: 'Suite',
    columnAxis: 'Release',
    status: false,
    allProjectsReason: 'a release belongs to one project; pick a project to compare its releases',
    minDays: 1,
    marks: { row: 'suite', column: 'release' },
  },
}

/** The row-order choices, in the select's order. */
export const HEATMAP_SORT_OPTIONS: readonly { value: HeatmapRowSort; label: string }[] = [
  { value: 'worst', label: 'Worst first' },
  { value: 'name', label: 'Name' },
  { value: 'volume', label: 'Most executions' },
]

export const HEATMAP_FIT_LABEL = 'Fit colour scale'
export const HEATMAP_SORT_LABEL = 'Rows'
/** The kind selector's name (a visually hidden legend): its radios name the columns. */
export const HEATMAP_KIND_LABEL = 'Heatmap columns'

/** Every heatmap cell is one or more test executions: the grain, stated as the other catalogue frames state theirs. */
export const HEATMAP_GRAIN_NOTE = 'Counted per test execution.'

/** The plot's height and the frame's chrome around it (the lazy placeholder holds both). */
export const HEATMAP_SECTION_HEIGHT = 320

/**
 * The largest row count a heatmap draws (the endpoint's `rows` cap): the
 * `top_n` a test-row rows request names, because chart-data accepts
 * `group_by=test` only inside one suite or under a `top_n`.
 */
export const HEATMAP_MAX_ROWS = 60

/**
 * The chart a heatmap cell's rows are drilled from: EVERY execution in the
 * cell (`executions`, a count, so the panel's total reconciles with the cell's
 * `n`), grouped by the cell's own dimensions.
 */
export function heatmapRowsChart(spec: HeatmapKindSpec): RowsChartSpec {
  const groupBy = spec.marks.column ? [spec.marks.row, spec.marks.column] : [spec.marks.row]
  return spec.marks.row === 'test' ? { metric: 'executions', groupBy, topN: HEATMAP_MAX_ROWS } : { metric: 'executions', groupBy }
}

/**
 * The open rows panel's selectors when they are a cell of THIS kind — the
 * row's dimension, then the column's, as `markSelectors` writes them — else
 * none. The caller has already kept only its own owner's selection
 * (`ownedRows`); this keeps a cell of another kind (the reader switched the
 * kind) from asking with the wrong `group_by`.
 */
export function heatmapOwnRows(spec: HeatmapKindSpec, rows: readonly DrillLevel[]): readonly DrillLevel[] {
  const want = heatmapRowsChart(spec).groupBy
  return rows.length === want.length && rows.every((level, i) => level.dimension === want[i]) ? rows : []
}

/**
 * The panel's title when no click of this page view named the cell (a
 * pasted link, Forward after a reload): the cell's labels from the drawn
 * matrix, else its keys. A test x run cell's rows are the test's, so its title
 * is the test alone.
 */
export function heatmapRowsTitle(matrix: MatrixChart | null, rows: readonly DrillLevel[]): string {
  const keys = matrix ? matrixAxisKeys(matrix) : null
  const label = (value: string, axisKeys: readonly string[] | undefined, labels: readonly string[] | undefined) => {
    const at = axisKeys ? axisKeys.indexOf(value) : -1
    return at >= 0 && labels && labels[at] !== undefined ? labels[at] : value
  }
  const [row, column] = rows
  if (!row) return ''
  const rowLabel = label(row.value, keys?.y, matrix?.y_labels)
  return column ? `${rowLabel}, ${label(column.value, keys?.x, matrix?.x_labels)}` : rowLabel
}

/**
 * A clicked cell's rows panel: its title and the count it should reconcile
 * with. A kind whose columns cannot be selected by (test x run: a run is not a
 * rows dimension) lists the ROW's executions over the whole window, not the
 * cell's (R1B-2): titled by the row alone, and no expected count — the cell's
 * `n` (one run) is not what the panel lists, and comparing them would claim
 * the results changed when nothing did.
 */
export function heatmapOpenedRows(
  spec: HeatmapKindSpec,
  mark: ChartMark,
  selectors: readonly DrillLevel[],
  matrix: MatrixChart | null,
  asOf: string | null,
): { title: string; expected: RowsExpectation | null } {
  if (spec.marks.column === null) return { title: heatmapRowsTitle(matrix, selectors.slice(0, 1)) || mark.label, expected: null }
  // The rows endpoint counts every execution in the cell: the cell's n.
  return { title: mark.label, expected: { y: mark.n, n: mark.n, asOf } }
}

/** Every heatmap cell offers its rows (there is no level below a cell); the host adds the page filter where a cell can be one. */
export const HEATMAP_MARK_INTENTS = ['rows'] as const
