/**
 * VIZ-501 (Wave 3): a `/analytics/heatmap` matrix as the heatmap's model — the
 * ONE adapter between the server's C3 `matrix` and the renderer.
 *
 *   - **Units.** The endpoint sends a pass rate in percentage POINTS with an
 *     explicit `unit: "percent"` (contract OD-7: an ABSENT unit means percent
 *     too); the renderer, its ramp ends, tooltip and table read a rate matrix
 *     as a RATIO (0..1, `formatRateValue`). Divided here, once, and the result
 *     says `unit: "ratio"`, so no later reader can divide it again. A matrix
 *     that already says `ratio` passes through.
 *   - **`null` stays `null`**: a cell nobody ran (`n: 0`) or one that ran but
 *     evaluated nothing (only skips, `n > 0`) is a no-data cell, drawn hatched;
 *     the second is "Nothing evaluated" in the tooltip and the table, never 0%.
 *   - **Row order is a permutation**, never a rebuild: the cells, the labels
 *     AND the keys (`y_keys`, what a drill or a rows request sends back) move
 *     together, so re-sorting can never open the wrong suite.
 *     `worst` (the default) is the lowest pass rate over the window first,
 *     from the cells' status counts (skipped and unknown outside the
 *     denominator, as the server's own rate); `name` and `volume` (most
 *     executions first) are the other two. A status matrix's `worst` is the
 *     server's order: it already ranked tests by failures over the window.
 *   - **Truncation** is the server's (`meta.truncated_axes`): rows beyond the
 *     cap were dropped and counted, never folded into an "Other" row (a rate
 *     over a hundred unrelated suites means nothing).
 *   - **The partial day**: the current UTC day's column (`meta.partial_day`)
 *     is labelled "(today, partial)" in the tooltip, the announcement and the
 *     table; the axis keeps its short day.
 */
import type { EnvelopeMeta, MatrixChart, StatusCounts, VizDimension, VizStatus } from '@/lib/viz/contracts'
import { formatNumber } from '@/utils/formatters'
import { formatRateValue, formatPlainValue, matrixAxisKeys } from './chartText'
import { middleTruncate } from './labelTruncate'
import type { ChartMark } from './marks'
import type { HeatmapMatrix, NumericMatrix, StatusMatrix } from './engines/echarts/heatmapOption'

/** How the rows are ordered on screen. */
export const HEATMAP_ROW_SORTS = ['worst', 'name', 'volume'] as const
export type HeatmapRowSort = (typeof HEATMAP_ROW_SORTS)[number]

/** One axis' cut: how many are drawn, how many there were. */
export interface HeatmapAxisCut {
  shown: number
  total: number
  /** The server's name for what the axis holds (`suite`, `test`, `environment`, `release`, `run`), when it said. */
  dimension: string | null
}

export interface HeatmapModel {
  /** The drawable matrix: rows in display order, a rate in 0..1 (`unit: "ratio"`), keys permuted with the rows. */
  matrix: HeatmapMatrix
  rows: HeatmapAxisCut
  columns: HeatmapAxisCut
  /** The partial day's column index (`meta.partial_day`), or `null`. */
  partialColumn: number | null
}

/** What a partial day's column says in the tooltip, the announcement and the table. */
export const PARTIAL_DAY_SUFFIX = ' (today, partial)'

/** A YYYY-MM-DD day key: the columns of a day axis. */
const DAY_KEY = /^\d{4}-\d{2}-\d{2}$/

/** Whether every column is a UTC day (`suite_day`): the axis then prints the kit's short day. */
export function columnsAreDays(keys: readonly string[]): boolean {
  return keys.length > 0 && keys.every((key) => DAY_KEY.test(key))
}

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)

/** A rate as 0..1: the one division by 100 (`percent` or no unit); `ratio` as is. */
function asRatio(value: number, unit: MatrixChart['unit']): number {
  return unit === 'ratio' ? value : value / 100
}

interface RowFacts {
  index: number
  label: string
  key: string
  /** The window's pass rate for a rate row (0..1), the window's sum for a count row; `null` = nothing measured. */
  score: number | null
  executions: number
}

/** The pass rate a row's counts give over the window, else the n-weighted mean of its measured cells. */
function rowRate(cells: readonly { value: number | null; n: number; counts?: StatusCounts }[]): number | null {
  let passed = 0
  let evaluated = 0
  let counted = false
  for (const cell of cells) {
    if (!cell.counts) continue
    counted = true
    passed += cell.counts.passed
    evaluated += cell.counts.passed + cell.counts.failed + cell.counts.broken
  }
  if (counted) return evaluated > 0 ? passed / evaluated : null
  let weighted = 0
  let samples = 0
  let plain = 0
  let measured = 0
  for (const cell of cells) {
    if (!finite(cell.value)) continue
    measured += 1
    plain += cell.value
    if (cell.n > 0) {
      weighted += cell.value * cell.n
      samples += cell.n
    }
  }
  if (measured === 0) return null
  return samples > 0 ? weighted / samples : plain / measured
}

/** Code-unit order (never `localeCompare`: the order must not move with the reader's locale), then the key. */
function byName(a: RowFacts, b: RowFacts): number {
  if (a.label !== b.label) return a.label < b.label ? -1 : 1
  if (a.key !== b.key) return a.key < b.key ? -1 : 1
  return a.index - b.index
}

/** `score` ascending (a rate: worst = lowest) or descending (a count: worst = highest); unmeasured rows last. */
function byScore(direction: 1 | -1) {
  return (a: RowFacts, b: RowFacts): number => {
    if (a.score === null || b.score === null) {
      if (a.score === b.score) return byName(a, b)
      return a.score === null ? 1 : -1
    }
    return (a.score - b.score) * direction || byName(a, b)
  }
}

/**
 * The display order of a matrix's rows, as a permutation: entry `i` is the
 * SOURCE row drawn at position `i`.
 */
export function heatmapRowOrder(matrix: HeatmapMatrix, sort: HeatmapRowSort): number[] {
  const keys = matrix.y_keys ?? matrix.y_labels
  const byRow = matrix.y_labels.map(() => [] as HeatmapMatrix['cells'][number][])
  for (const cell of matrix.cells) byRow[cell.y]?.push(cell)
  const facts: RowFacts[] = matrix.y_labels.map((label, index) => {
    const cells = byRow[index]
    let executions = 0
    for (const cell of cells) executions += cell.n
    let score: number | null = null
    if (matrix.value_type === 'rate') score = rowRate(cells as NumericMatrix['cells'])
    else if (matrix.value_type === 'count') {
      const measured = (cells as NumericMatrix['cells']).filter((cell) => finite(cell.value))
      score = measured.length ? measured.reduce((sum, cell) => sum + (cell.value as number), 0) : null
    }
    return { index, label, key: keys[index] ?? label, score, executions }
  })
  if (sort === 'name') return [...facts].sort(byName).map((row) => row.index)
  if (sort === 'volume') {
    return [...facts].sort((a, b) => b.executions - a.executions || byName(a, b)).map((row) => row.index)
  }
  // worst: a status matrix keeps the server's ranking (failures over the window).
  if (matrix.value_type === 'status') return facts.map((row) => row.index)
  return [...facts].sort(byScore(matrix.value_type === 'rate' ? 1 : -1)).map((row) => row.index)
}

/** Apply a row permutation to cells, labels and keys together. */
function permuteRows<M extends HeatmapMatrix>(matrix: M, order: readonly number[]): M {
  const position = new Map(order.map((source, drawn) => [source, drawn]))
  const keys = matrix.y_keys
  const cells = matrix.cells
    .map((cell) => ({ ...cell, y: position.get(cell.y) ?? cell.y }))
    .sort((a, b) => a.y - b.y || a.x - b.x)
  return {
    ...matrix,
    y_labels: order.map((source) => matrix.y_labels[source]),
    ...(keys ? { y_keys: order.map((source) => keys[source]) } : {}),
    cells,
  }
}

/** The C3 matrix as the renderer's matrix, unpermuted: a rate in 0..1, null kept. */
function drawable(chart: MatrixChart): HeatmapMatrix {
  const base = {
    kind: 'matrix' as const,
    x_labels: [...chart.x_labels],
    y_labels: [...chart.y_labels],
    ...(chart.x_keys ? { x_keys: [...chart.x_keys] } : {}),
    ...(chart.y_keys ? { y_keys: [...chart.y_keys] } : {}),
  }
  if (chart.value_type === 'status') {
    const matrix: StatusMatrix = {
      ...base,
      value_type: 'status',
      cells: chart.cells.map((cell) => ({
        x: cell.x,
        y: cell.y,
        value: typeof cell.value === 'string' ? (cell.value as VizStatus) : null,
        n: cell.n,
        ...(cell.counts ? { counts: cell.counts } : {}),
      })),
    }
    return matrix
  }
  const rate = chart.value_type === 'rate'
  const matrix: NumericMatrix = {
    ...base,
    value_type: chart.value_type,
    // The output's unit, stated: whatever reads it next reads 0..1.
    ...(rate ? { unit: 'ratio' as const } : {}),
    cells: chart.cells.map((cell) => ({
      x: cell.x,
      y: cell.y,
      // Unmeasured stays null; a rate is divided once.
      value: finite(cell.value) ? (rate ? asRatio(cell.value, chart.unit) : cell.value) : null,
      n: cell.n,
      ...(cell.counts ? { counts: cell.counts } : {}),
    })),
  }
  return matrix
}

/** One axis' cut from the envelope's `truncated_axes`, else everything is shown. */
function axisCut(shown: number, axis: { dimension: string; total: number } | undefined): HeatmapAxisCut {
  const total = axis && finite(axis.total) && axis.total > shown ? axis.total : shown
  return { shown, total, dimension: axis?.dimension ?? null }
}

/**
 * The word a run column's label is read with (F-04, F-19): the endpoint
 * labels a run by its build number alone (BE1: `lr.build_number`), so "218"
 * in a tooltip or an announcement says nothing about what 218 is.
 */
export const RUN_COLUMN_WORD = 'Build'

/** The run axis' title on the canvas (F-19): what the printed numbers are, and their order. */
export const RUN_AXIS_TITLE = 'Build (oldest to newest)'

/** A run column's label read with its word: "218" -> "Build 218" (a label that already says it is kept). */
export function runColumnLabel(label: string): string {
  return label.startsWith(`${RUN_COLUMN_WORD} `) ? label : `${RUN_COLUMN_WORD} ${label}`.trim()
}

/**
 * Column labels that repeat, told apart by their place among the equals,
 * oldest first: two runs of build 228 are "Build 228 (1)" and "Build 228 (2)"
 * (F-04: a build number can repeat across branches, and two columns that read
 * the same cannot be told apart on the axis, in the tooltip or in the table).
 * A label that does not repeat is unchanged.
 */
export function disambiguatedLabels(labels: readonly string[]): string[] {
  const total = new Map<string, number>()
  for (const label of labels) total.set(label, (total.get(label) ?? 0) + 1)
  const seen = new Map<string, number>()
  return labels.map((label) => {
    if ((total.get(label) ?? 0) < 2) return label
    const place = (seen.get(label) ?? 0) + 1
    seen.set(label, place)
    return `${label} (${place})`
  })
}

/**
 * What a run axis PRINTS under each column (F-04): the label without the
 * word every column shares ("Build 228 (2)" -> "228 (2)"); the axis title
 * names it once (`RUN_AXIS_TITLE`). Eight columns of "Build …" was all a
 * 640 px frame could print.
 */
export function printedRunLabels(labels: readonly string[]): string[] {
  const word = `${RUN_COLUMN_WORD} `
  return labels.map((label) => (label.startsWith(word) && label.length > word.length ? label.slice(word.length) : label))
}

/** Options of `heatmapFromMatrix`. */
export interface HeatmapModelOptions {
  /** The columns are runs (test x run): each label is read as a build ("Build 218"). */
  runColumns?: boolean
}

export function heatmapFromMatrix(
  chart: MatrixChart,
  meta: EnvelopeMeta | null,
  sort: HeatmapRowSort = 'worst',
  { runColumns = false }: HeatmapModelOptions = {},
): HeatmapModel {
  const drawn = drawable(chart)
  // Every reader of the model (tooltip, announcement, table, axis) reads the same column words.
  const named = runColumns ? drawn.x_labels.map(runColumnLabel) : drawn.x_labels
  const xLabels = disambiguatedLabels(named)
  const source = xLabels.every((label, x) => label === drawn.x_labels[x]) ? drawn : { ...drawn, x_labels: xLabels }
  const sorted = permuteRows(source, heatmapRowOrder(source, sort))
  // The partial day, by KEY (the label is display text), labelled for every text reader.
  const { x: xKeys } = matrixAxisKeys(chart)
  const partial = meta?.partial_day ?? null
  const partialColumn = partial === null ? -1 : xKeys.indexOf(partial)
  const matrix =
    partialColumn >= 0
      ? {
          ...sorted,
          x_labels: sorted.x_labels.map((label, x) => (x === partialColumn ? `${label}${PARTIAL_DAY_SUFFIX}` : label)),
        }
      : sorted
  return {
    matrix,
    rows: axisCut(chart.y_labels.length, meta?.truncated_axes?.series),
    columns: axisCut(chart.x_labels.length, meta?.truncated_axes?.x),
    partialColumn: partialColumn >= 0 ? partialColumn : null,
  }
}

/**
 * "Fit to data": the ramp's range as the lowest and highest measured value,
 * or `null` when nothing is measured (or the matrix is a status matrix). A
 * flat matrix (every cell the same) is widened a little so the ramp has two
 * ends: one point of a rate, one unit of a count.
 */
export function fittedDomain(matrix: HeatmapMatrix): [number, number] | null {
  if (matrix.value_type === 'status') return null
  let lo = Infinity
  let hi = -Infinity
  for (const cell of matrix.cells) {
    if (!finite(cell.value)) continue
    if (cell.value < lo) lo = cell.value
    if (cell.value > hi) hi = cell.value
  }
  if (lo > hi) return null
  if (lo < hi) return [lo, hi]
  if (matrix.value_type === 'rate') {
    const step = 0.01
    return hi >= 1 ? [1 - step, 1] : [Math.max(0, lo - step), lo + step]
  }
  // A count is never negative.
  return [Math.max(0, lo - 1), hi + 1]
}

/** The fitted range in words: "Colour scale fitted to the data: 54.0% to 98.0%." */
export function fittedDomainNote(matrix: HeatmapMatrix, domain: readonly [number, number]): string {
  const format = matrix.value_type === 'rate' ? formatRateValue : formatPlainValue
  return `Colour scale fitted to the data: ${format(domain[0])} to ${format(domain[1])}.`
}

/** Longest row label the axis prints, in characters, before it is middle-truncated (a 120 px gutter). */
export const HEATMAP_ROW_LABEL_CHARS = 16

/** The row labels the axis prints: long names cut in the MIDDLE, so `payments-…-eu-west` and `payments-…-us-east` stay apart. */
export function printedRowLabels(labels: readonly string[], max = HEATMAP_ROW_LABEL_CHARS): string[] {
  return labels.map((label) => ([...label].length - max < HEATMAP_MIN_CUT ? label : middleTruncate(label, max)))
}

/**
 * The fewest characters a middle cut must save (F-12): a 17-character name cut
 * to 16 hides one letter behind an ellipsis about as wide as it was.
 */
export const HEATMAP_MIN_CUT = 3

/** The order rule, in words: without it a reader takes the row order for alphabetical or by volume. */
export function heatmapOrderNote(valueType: HeatmapMatrix['value_type'], sort: HeatmapRowSort): string {
  if (sort === 'name') return 'Rows: by name.'
  if (sort === 'volume') return 'Rows: most executions first.'
  if (valueType === 'rate') return 'Rows: lowest pass rate first.'
  if (valueType === 'status') return 'Rows: most failures first.'
  return 'Rows: highest first.'
}

/** What the server picked the shown rows by, per dimension (BE1: failed + broken over the window). */
const ROW_PICK = 'by failures'

/** What a row and a column are, singular and plural: `{ rows: ['suite', 'suites'], columns: ['day', 'days'] }`. */
export interface HeatmapNouns {
  rows: readonly [string, string]
  columns: readonly [string, string]
}

/** The suite x day nouns: the Trends heatmap's, and the default. */
export const SUITE_DAY_NOUNS: HeatmapNouns = { rows: ['suite', 'suites'], columns: ['day', 'days'] }

const counted = (n: number, [one, many]: readonly [string, string]) => `${formatNumber(n)} ${n === 1 ? one : many}`

/** The row cut in words: "Top 40 of 200 suites by failures." ('' when nothing was cut). */
export function heatmapRowsNote(rows: HeatmapAxisCut, nouns: HeatmapNouns): string {
  if (rows.total <= rows.shown) return ''
  return `Top ${formatNumber(rows.shown)} of ${counted(rows.total, nouns.rows)} ${ROW_PICK}.`
}

/** The column cut in words, by what the columns are ('' when nothing was cut). */
export function heatmapColumnsNote(columns: HeatmapAxisCut): string {
  if (columns.total <= columns.shown) return ''
  const shown = formatNumber(columns.shown)
  const total = formatNumber(columns.total)
  switch (columns.dimension) {
    case 'environment':
      return `The ${shown} busiest of ${total} environments.`
    case 'release':
      return `The ${shown} most recent of ${total} releases.`
    case 'run':
      return `The last ${shown} of ${total} runs.`
    default:
      return `${shown} of ${total} columns shown.`
  }
}

/** One sentence for assistive tech, read by the heatmap wrapper. */
export function heatmapDescription(title: string, matrix: HeatmapMatrix, nouns: HeatmapNouns, sort: HeatmapRowSort): string {
  const what = matrix.value_type === 'status' ? 'results' : matrix.value_type === 'rate' ? 'pass rate' : 'counts'
  const order = heatmapOrderNote(matrix.value_type, sort).replace(/^Rows: /, '').replace(/\.$/, '')
  return `${title}: ${what} for ${counted(matrix.y_labels.length, nouns.rows)} over ${counted(matrix.x_labels.length, nouns.columns)}, ${order}.`
}

/**
 * What a cell's row and column ARE, as C5 dimensions, for a drill or a rows
 * request: suite x day is `{row: 'suite', column: 'day'}`. `column: null` when
 * the columns are not a dimension the server can select by (a run).
 */
export interface HeatmapMarkDimensions {
  row: VizDimension
  column: VizDimension | null
}

/**
 * The mark a cell stands for (Wave 3, the VIZ-602 seam): the row's KEY under
 * the row dimension, the column's KEY as its context, the cell's drawn value
 * (a rate in percentage points, as the server sent it) and its executions. A
 * cell nobody ran (`n` 0) has nothing to list and is not a mark. Keys, never
 * labels: a label is display text and may repeat.
 */
export function heatmapCellMark(matrix: HeatmapMatrix, index: number, dimensions: HeatmapMarkDimensions): ChartMark | null {
  const cell = matrix.cells[index]
  if (!cell || cell.n <= 0) return null
  const keys = matrixAxisKeys(matrix as MatrixChart)
  const rowKey = keys.y[cell.y]
  const columnKey = keys.x[cell.x]
  if (rowKey === undefined || columnKey === undefined) return null
  const y = typeof cell.value === 'number' ? (matrix.value_type === 'rate' ? cell.value * 100 : cell.value) : null
  return {
    dimension: dimensions.row,
    value: rowKey,
    // The column is part of what the cell IS, even when it cannot be selected by.
    label: `${matrix.y_labels[cell.y]}, ${matrix.x_labels[cell.x]}`,
    y,
    n: cell.n,
    ...(dimensions.column ? { context: [{ dimension: dimensions.column, value: columnKey }] } : {}),
  }
}
