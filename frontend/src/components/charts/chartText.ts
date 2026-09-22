/**
 * Text alternatives for a chart (VIZ-105), generated from the SAME normalised
 * `ChartSeries` object the renderer receives — so the summary, the table view
 * and the plot cannot disagree.
 *
 *   - `formatChartValue`  — one formatter for table cells and summary numbers
 *   - `summarizeChart`    — the screen-reader description: chart type, axes,
 *                           scope, and min / max / latest
 *   - `chartTableModel`   — header cells and rows for the "View as table" view
 *
 * `null` is "no data" everywhere: it prints as "—" and is never counted as a
 * zero in a min or a max.
 */
import type { ChartSeries, MatrixChart, SeriesChart } from '@/lib/viz/contracts'
import { formatNumber, formatPercent, NO_VALUE } from '@/utils/formatters'

export { NO_VALUE }

/** A matrix cell the server sent with no value: drawn hatched, read as this. */
export const NO_DATA = 'No data'

export type ValueFormatter = (value: number) => string

/** Up to two decimals, grouped: `0.1 + 0.2` reads "0.3", never "0.30000000000000004". */
export const formatPlainValue: ValueFormatter = (v) => formatNumber(v, { maximumFractionDigits: 2 })
/** A 0..1 ratio as a one-decimal percentage. */
export const formatRateValue: ValueFormatter = (v) => formatPercent(v, { from: 'ratio' })

/** Rate matrices are 0..1 and read as a percentage; everything else is a plain number. */
export function defaultFormatter(series: ChartSeries): ValueFormatter {
  if (series.kind === 'matrix' && series.value_type === 'rate') return formatRateValue
  return formatPlainValue
}

export function formatChartValue(value: number | string | null | undefined, format: ValueFormatter): string {
  if (value === null || value === undefined) return NO_VALUE
  if (typeof value === 'string') return value
  return Number.isFinite(value) ? format(value) : NO_VALUE
}

export interface ChartAxes {
  x?: string
  y?: string
}

export interface SummaryInput {
  /** Human chart type, e.g. "Heatmap", "Line chart". */
  chartType: string
  series: ChartSeries
  axes?: ChartAxes
  /** The scope the data covers, e.g. "Project payments, last 7 days". */
  scope?: string
  format?: ValueFormatter
}

interface Extreme {
  value: number
  where: string
}

function extremes(points: { value: number | null; where: string }[]): { min: Extreme; max: Extreme } | null {
  let min: Extreme | null = null
  let max: Extreme | null = null
  for (const point of points) {
    if (point.value === null || !Number.isFinite(point.value)) continue
    if (!min || point.value < min.value) min = { value: point.value, where: point.where }
    if (!max || point.value > max.value) max = { value: point.value, where: point.where }
  }
  return min && max ? { min, max } : null
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

function seriesSentences(chart: SeriesChart, format: ValueFormatter): string[] {
  const out: string[] = []
  for (const s of chart.series) {
    const found = extremes(s.points.map((p) => ({ value: p.y, where: p.x })))
    if (!found) {
      out.push(`${s.label}: no data.`)
      continue
    }
    const measured = s.points.filter((p) => p.y !== null)
    const latest = measured[measured.length - 1]
    out.push(
      `${s.label}: min ${format(found.min.value)} (${found.min.where}), max ${format(found.max.value)} (${found.max.where}), latest ${format(latest.y as number)} (${latest.x}).`,
    )
  }
  return out
}

function matrixSentences(chart: MatrixChart, format: ValueFormatter): string[] {
  const numeric = chart.cells.map((cell) => ({
    value: typeof cell.value === 'number' ? cell.value : null,
    where: `${chart.y_labels[cell.y] ?? ''}, ${chart.x_labels[cell.x] ?? ''}`,
    x: cell.x,
    y: cell.y,
  }))
  const out: string[] = []
  if (chart.value_type === 'status') {
    const counts = new Map<string, number>()
    for (const cell of chart.cells) if (typeof cell.value === 'string') counts.set(cell.value, (counts.get(cell.value) ?? 0) + 1)
    out.push(
      counts.size
        ? `Cells by status: ${[...counts].map(([status, n]) => `${status} ${n}`).join(', ')}.`
        : 'No cell has a status.',
    )
  } else {
    const found = extremes(numeric)
    if (found) {
      // Latest: the right-most column that has a measured cell.
      const measured = numeric.filter((c) => c.value !== null)
      // A loop, not Math.max(...spread): a spread past ~100k arguments overflows the stack.
      let lastX = -Infinity
      for (const c of measured) if (c.x > lastX) lastX = c.x
      const column = measured.filter((c) => c.x === lastX)
      const latestParts = column.map((c) => `${chart.y_labels[c.y] ?? ''} ${format(c.value as number)}`)
      out.push(
        `Min ${format(found.min.value)} (${found.min.where}), max ${format(found.max.value)} (${found.max.where}).`,
        `Latest (${chart.x_labels[lastX] ?? ''}): ${latestParts.join(', ')}.`,
      )
    } else {
      out.push('No cell has a measured value.')
    }
  }
  const missing = chart.cells.filter((cell) => cell.value === null).length
  if (missing) out.push(`${plural(missing, 'cell')} with no data.`)
  return out
}

/** The generated description a chart's `aria-describedby` points at. */
export function summarizeChart({ chartType, series, axes = {}, scope, format }: SummaryInput): string {
  const fmt = format ?? defaultFormatter(series)
  const parts: string[] = []
  switch (series.kind) {
    case 'series': {
      const xs = new Set(series.series.flatMap((s) => s.points.map((p) => p.x)))
      parts.push(`${chartType} of ${plural(series.series.length, 'series', 'series')}.`)
      parts.push(`X axis: ${axes.x ?? series.dimensions[0] ?? (series.x_type === 'time' ? 'time' : 'category')} (${plural(xs.size, 'value')}).`)
      if (axes.y) parts.push(`Y axis: ${axes.y}.`)
      break
    }
    case 'matrix':
      parts.push(`${chartType}.`)
      parts.push(`X axis: ${axes.x ?? 'column'} (${plural(series.x_labels.length, 'value')}).`)
      parts.push(`Y axis: ${axes.y ?? 'row'} (${plural(series.y_labels.length, 'value')}).`)
      break
    case 'tree':
      parts.push(`${chartType} of ${plural(series.nodes.length, 'node')}.`)
      break
    case 'graph':
      parts.push(`${chartType} of ${plural(series.nodes.length, 'node')} and ${plural(series.edges.length, 'link')}.`)
      break
  }
  if (scope) parts.push(`Scope: ${scope}.`)
  if (series.kind === 'series') parts.push(...seriesSentences(series, fmt))
  if (series.kind === 'matrix') parts.push(...matrixSentences(series, fmt))
  if (series.kind === 'tree') {
    const found = extremes(series.nodes.map((n) => ({ value: n.value, where: n.label })))
    if (found) parts.push(`Largest ${found.max.where} ${fmt(found.max.value)}, smallest ${found.min.where} ${fmt(found.min.value)}.`)
  }
  return parts.join(' ')
}

// ── Table view ────────────────────────────────────────────────────────────────

export interface ChartTableRow {
  /** Row header cell text. */
  header: string
  /** Data cells, in column order (after the row-header column). */
  cells: string[]
  /** A second (or later) value at the same x in one series: kept, and marked. */
  duplicate?: true
}

export interface ChartTableModel {
  /** Every column header, the row-header column first. */
  columns: string[]
  rows: ChartTableRow[]
  /** Data problems the reader should know about (e.g. a duplicate x). Empty when none. */
  warnings: string[]
}

export const DUPLICATE_SUFFIX = ' (duplicate)'

/**
 * The x values of a series chart, in axis order: chronological for a time
 * axis (by parsed instant when every x parses, else by string — ISO dates sort
 * correctly either way), first appearance for a category axis (stable).
 */
function orderedXs(chart: SeriesChart): string[] {
  const xs: string[] = []
  const seen = new Set<string>()
  for (const s of chart.series)
    for (const p of s.points)
      if (!seen.has(p.x)) {
        seen.add(p.x)
        xs.push(p.x)
      }
  if (chart.x_type !== 'time') return xs
  const instants = new Map(xs.map((x) => [x, Date.parse(x)]))
  const allParse = xs.every((x) => !Number.isNaN(instants.get(x)))
  return [...xs].sort((a, b) =>
    allParse ? (instants.get(a) as number) - (instants.get(b) as number) : a < b ? -1 : a > b ? 1 : 0,
  )
}

function seriesTable(chart: SeriesChart, axes: ChartAxes, fmt: ValueFormatter): ChartTableModel {
  // Every value per (series, x), in the order given: a duplicate x is KEPT.
  const values = chart.series.map((s) => {
    const byX = new Map<string, (number | null)[]>()
    for (const p of s.points) {
      const list = byX.get(p.x)
      if (list) list.push(p.y)
      else byX.set(p.x, [p.y])
    }
    return byX
  })
  const warnings: string[] = []
  const rows: ChartTableRow[] = []
  for (const x of orderedXs(chart)) {
    let depth = 1
    values.forEach((byX, s) => {
      const count = byX.get(x)?.length ?? 0
      if (count > 1) warnings.push(`${chart.series[s].label} has more than one value at ${x}.`)
      if (count > depth) depth = count
    })
    for (let k = 0; k < depth; k++) {
      const cells = values.map((byX) => {
        const list = byX.get(x)
        return list && k < list.length ? formatChartValue(list[k], fmt) : NO_VALUE
      })
      rows.push(k === 0 ? { header: x, cells } : { header: `${x}${DUPLICATE_SUFFIX}`, cells, duplicate: true })
    }
  }
  return { columns: [axes.x ?? chart.dimensions[0] ?? 'x', ...chart.series.map((s) => s.label)], rows, warnings }
}

/** The table for "View as table": exactly the plotted values, formatted once. */
export function chartTableModel(series: ChartSeries, axes: ChartAxes = {}, format?: ValueFormatter): ChartTableModel {
  const fmt = format ?? defaultFormatter(series)
  switch (series.kind) {
    case 'series':
      return seriesTable(series, axes, fmt)
    case 'matrix': {
      const byCell = new Map(series.cells.map((cell) => [`${cell.x}:${cell.y}`, cell.value]))
      return {
        columns: [axes.y ?? 'Row', ...series.x_labels],
        rows: series.y_labels.map((label, y) => ({
          header: label,
          cells: series.x_labels.map((_, x) => {
            const key = `${x}:${y}`
            // A cell the server sent as null is "No data" (drawn hatched); a cell it never sent is "—".
            return byCell.has(key) && byCell.get(key) === null ? NO_DATA : formatChartValue(byCell.get(key), fmt)
          }),
        })),
        warnings: [],
      }
    }
    case 'tree': {
      const labels = new Map(series.nodes.map((n) => [n.id, n.label]))
      return {
        columns: ['Node', 'Parent', 'Value', 'Measure'],
        rows: series.nodes.map((n) => ({
          header: n.label,
          cells: [
            n.parent_id === null ? NO_VALUE : (labels.get(n.parent_id) ?? n.parent_id),
            formatChartValue(n.value, fmt),
            formatChartValue(n.measure, fmt),
          ],
        })),
        warnings: [],
      }
    }
    case 'graph': {
      const labels = new Map(series.nodes.map((n) => [n.id, n.label]))
      return {
        columns: ['Source', 'Target', 'Weight'],
        rows: series.edges.map((e) => ({
          header: labels.get(e.source) ?? e.source,
          cells: [labels.get(e.target) ?? e.target, formatChartValue(e.weight, fmt)],
        })),
        warnings: [],
      }
    }
  }
}
