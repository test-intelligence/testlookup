/**
 * Per-chart export (VIZ-606) — the pure half: provenance, file names, the CSV,
 * and the LAYOUT of an exported image. Nothing here touches the DOM, a canvas
 * or React; `chartImage.ts` turns a layout into an SVG document or PNG pixels.
 *
 * Provenance is the point of the story: an exported chart has to stand alone
 * in a deck or an audit file, so the image footer and the CSV's comment lines
 * state the same scope — project, release, test suite, window, "N of M",
 * generated-at (UTC) and the app version — and a local zoom that is not the
 * page's window is stated rather than silently exported as if it were.
 *
 * The CSV is built from the SAME normalised `ChartSeries` the renderer and the
 * "View as table" view read (`chartTableModel`), so its rows and columns are
 * the table's — with one difference, on purpose: the table prints FORMATTED
 * values ("45.2%", "1,234"), the CSV writes the RAW values the chart plots.
 * Formatting rounds (two decimals, one-decimal percentages) and groups digits
 * by locale, so a formatted CSV would not contain "exactly the plotted
 * values" and would not re-import as numbers. A gap — `y: null`, or a point
 * the server marked `measured: false` (which every renderer draws as a gap) —
 * is an EMPTY cell, never 0.
 */
import type { ChartSeries, EnvelopeMeta } from './contracts'
import { chartTableModel, NO_DATA, NO_VALUE, type ChartAxes } from '@/components/charts/chartText'
import { formatNumber } from '@/utils/formatters'
import { CSV_EOL, csvRow } from './csv'

// ── Provenance ────────────────────────────────────────────────────────────────

export interface ChartProvenance {
  /** Project names, comma-joined when several; "All projects" when the scope names none. */
  project: string
  /** `[]` = all releases. */
  releases: string[]
  /** `[]` = all suites. */
  suites: string[]
  /** e.g. "2026-08-25 – 2026-09-23 UTC (30 days)". */
  window: string
  /** "N of M runs · N of M executions" — the frame footer's line; `null` when absent. */
  totals: string | null
  /** ISO-8601 UTC, from `meta.generated_at`. */
  generatedAt: string
  appVersion: string | null
  /** Anything the reader must know that the scope does not say — a local zoom, for one. */
  note?: string
  /**
   * The chart shows only some of the window's days (a local zoom). `totals`
   * are still the WHOLE window's — the envelope has no per-day run counts —
   * so they are labelled "Window totals", never plain "Totals" beside a note
   * saying the export shows the zoomed days only.
   */
  zoomed?: boolean
}

export const ALL_PROJECTS = 'All projects'
export const ALL_RELEASES = 'All releases'
export const ALL_SUITES = 'All suites'
export const SCOPE_UNAVAILABLE = 'Scope unavailable'

/** The frame footer's "N of M" line, from `meta.totals`. `null` when there are none. */
export function totalsLine(meta: EnvelopeMeta | null | undefined): string | null {
  const totals = meta?.totals
  if (!totals) return null
  return `${formatNumber(totals.matched_runs)} of ${formatNumber(totals.total_runs)} runs · ${formatNumber(
    totals.matched_executions,
  )} of ${formatNumber(totals.total_executions)} executions`
}

/** `YYYY-MM-DD` from a date or an instant; anything else is shown as sent. */
const dayOf = (value: string) => (/^\d{4}-\d{2}-\d{2}/.test(value) ? value.slice(0, 10) : value)

/**
 * What the export states about its scope, from the envelope meta the chart was
 * drawn from. `null` when there is no meta: the export still works and says
 * "Scope unavailable" rather than inventing a scope. `zoomNote` is set only
 * while the chart is locally zoomed: it becomes the `note`, and marks the
 * provenance `zoomed`.
 */
export function provenanceFromMeta(
  meta: EnvelopeMeta | null | undefined,
  appVersion: string | null,
  zoomNote?: string,
): ChartProvenance | null {
  if (!meta?.scope) return null
  const { projects, releases, suites, window } = meta.scope
  const days = window.days
  return {
    project: projects.length ? projects.map((p) => p.name).join(', ') : ALL_PROJECTS,
    releases: releases.map((r) => r.name),
    suites: [...suites],
    window: `${dayOf(window.from)} – ${dayOf(window.to)} UTC (${days} ${days === 1 ? 'day' : 'days'})`,
    totals: totalsLine(meta),
    generatedAt: meta.generated_at,
    appVersion,
    ...(zoomNote ? { note: zoomNote, zoomed: true } : {}),
  }
}

/** The totals' label: the whole window's while zoomed (see `ChartProvenance.zoomed`). */
export const TOTALS_LABEL = 'Totals'
export const WINDOW_TOTALS_LABEL = 'Window totals'

/** `2026-09-23T14:05:09Z` → `2026-09-23 14:05 UTC`; unparseable text is shown as sent. */
export function formatGeneratedAt(iso: string): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso
  return `${at.toISOString().slice(0, 10)} ${at.toISOString().slice(11, 16)} UTC`
}

/**
 * The provenance as labelled fields, in the order the story lists them. The
 * image footer and the CSV comment lines both come from this, so the two
 * cannot state different scopes.
 */
export function provenanceFields(provenance: ChartProvenance | null): [label: string, value: string][] {
  if (!provenance) return [['Scope', SCOPE_UNAVAILABLE]]
  const fields: [string, string][] = [
    ['Project', provenance.project],
    ['Release', provenance.releases.length ? provenance.releases.join(', ') : ALL_RELEASES],
    ['Test Suite', provenance.suites.length ? provenance.suites.join(', ') : ALL_SUITES],
    ['Window', provenance.window],
  ]
  if (provenance.totals) fields.push([provenance.zoomed ? WINDOW_TOTALS_LABEL : TOTALS_LABEL, provenance.totals])
  fields.push(['Generated', formatGeneratedAt(provenance.generatedAt)])
  if (provenance.appVersion) fields.push(['App version', provenance.appVersion])
  if (provenance.note) fields.push(['Note', provenance.note])
  return fields
}

/** The image footer's paragraphs (each is wrapped to the image width by the layout). */
export function footerParagraphs(provenance: ChartProvenance | null): string[] {
  if (!provenance) return [`${SCOPE_UNAVAILABLE}.`]
  const byLabel = new Map(provenanceFields(provenance))
  const out: string[] = []
  out.push(`Project: ${byLabel.get('Project')} · Release: ${byLabel.get('Release')}`)
  out.push(`Test Suite: ${byLabel.get('Test Suite')}`)
  // Unzoomed, the totals follow the window unlabelled, as in the frame's
  // footer; zoomed, they say whose they are.
  const windowTotals = byLabel.get(WINDOW_TOTALS_LABEL)
  out.push(
    [
      `Window: ${byLabel.get('Window')}`,
      windowTotals ? `${WINDOW_TOTALS_LABEL}: ${windowTotals}` : byLabel.get(TOTALS_LABEL),
    ]
      .filter(Boolean)
      .join(' · '),
  )
  const version = byLabel.get('App version')
  out.push(`Generated ${byLabel.get('Generated')}${version ? ` · TestLookup ${version}` : ''}`)
  if (byLabel.has('Note')) out.push(`Note: ${byLabel.get('Note')}`)
  return out
}

// ── File names ────────────────────────────────────────────────────────────────

/** Longest slug segment, in code points: the whole name stays well under `download.ts`'s cap. */
export const SLUG_MAX = 48

/**
 * A file-name segment from a name that came from ingested data. Every run of
 * anything that is not a letter or a digit — `/`, `\`, `..`, spaces, control
 * and bidi characters, `:` — becomes ONE `-`, so no path, traversal or
 * reordering survives; letters in any script are kept (a Japanese project
 * name stays readable); the result is lower-cased and cut to `SLUG_MAX`.
 */
export function slugify(name: string, fallback: string): string {
  const slug = Array.from(
    name
      .normalize('NFKC')
      .toLowerCase()
      .replace(/[^\p{L}\p{N}]+/gu, '-')
      .replace(/^-+|-+$/g, ''),
  )
    .slice(0, SLUG_MAX)
    .join('')
    .replace(/-+$/, '')
  return slug || fallback
}

/** `yyyymmdd-hhmm` in UTC; the current time when `generatedAt` does not parse. */
function stamp(generatedAt: string | null | undefined, now: () => Date): string {
  const parsed = generatedAt ? new Date(generatedAt) : null
  const at = parsed && !Number.isNaN(parsed.getTime()) ? parsed : now()
  const iso = at.toISOString()
  return `${iso.slice(0, 4)}${iso.slice(5, 7)}${iso.slice(8, 10)}-${iso.slice(11, 13)}${iso.slice(14, 16)}`
}

/** `testlookup_<chart>_<project>_<yyyymmdd-hhmm>Z.<ext>` (VIZ-606). */
export function exportFilename(
  chartSlug: string,
  projectName: string | null | undefined,
  generatedAt: string | null | undefined,
  ext: 'png' | 'svg' | 'csv',
  now: () => Date = () => new Date(),
): string {
  const chart = slugify(chartSlug, 'chart')
  const project = projectName ? slugify(projectName, 'project') : 'all-projects'
  return `testlookup_${chart}_${project}_${stamp(generatedAt, now)}Z.${ext}`
}

// ── CSV ───────────────────────────────────────────────────────────────────────

/** A point the server marked `measured: false` is drawn as a gap; so it is exported as one. */
function withGaps(series: ChartSeries): ChartSeries {
  if (series.kind !== 'series') return series
  return {
    ...series,
    series: series.series.map((s) => ({
      ...s,
      points: s.points.map((p) => (p.measured === false && p.y !== null ? { ...p, y: null } : p)),
    })),
  }
}

/** Raw, locale-free, full precision: what the chart plots, as a spreadsheet re-reads it. */
const rawValue = (value: number) => String(value)

/** "No value" in the table model is an empty CSV cell (never 0, never a dash). */
const emptyWhenMissing = (cell: string) => (cell === NO_VALUE || cell === NO_DATA ? '' : cell)

/**
 * The chart's data as CSV text (no BOM — `csvBlob` adds it): the provenance as
 * `# Label,value` comment lines, then exactly the rows and columns of the
 * table view, with raw values.
 */
export function buildChartCsv(
  title: string,
  series: ChartSeries,
  provenance: ChartProvenance | null,
  axes: ChartAxes = {},
): string {
  const model = chartTableModel(withGaps(series), axes, rawValue)
  const lines: string[] = []
  lines.push(`# TestLookup chart export,${csvRow([title])}`)
  for (const [label, value] of provenanceFields(provenance)) lines.push(`# ${label},${csvRow([value])}`)
  for (const warning of model.warnings) lines.push(`# Warning,${csvRow([warning])}`)
  lines.push(csvRow(model.columns))
  for (const row of model.rows) lines.push(csvRow([row.header, ...row.cells.map(emptyWhenMissing)]))
  return lines.join(CSV_EOL) + CSV_EOL
}

// ── Image layout (pure) ───────────────────────────────────────────────────────

/** Width of `text` drawn in `font` (a CSS font shorthand), in CSS px. */
export type MeasureText = (text: string, font: string) => number

export interface TextLine {
  text: string
  x: number
  /** Alphabetic baseline. */
  y: number
  /** CSS font shorthand, for a canvas. */
  font: string
  /** The same font as SVG attributes. */
  size: number
  weight: number
  role: 'title' | 'legend' | 'footer'
}

export interface Box {
  x: number
  y: number
  width: number
  height: number
}

export interface LegendItemInput {
  label: string
  swatch: { width: number; height: number }
}

export interface ExportLayoutInput {
  title: string
  chart: { width: number; height: number }
  legend: readonly LegendItemInput[]
  footer: readonly string[]
  measure: MeasureText
  /** CSS font-family for every text; the page's own. */
  fontFamily?: string
}

export interface ExportLayout {
  /** In CSS px; the PNG is this times the pixel ratio. */
  width: number
  height: number
  fontFamily: string
  text: TextLine[]
  chart: Box
  swatches: Box[]
  /** The footer band (separator at its top edge); every footer line lies inside it. */
  footer: Box
}

export const EXPORT_PAD = 16
export const EXPORT_MIN_WIDTH = 480
export const EXPORT_PIXEL_RATIO = 2
const TITLE_SIZE = 16
const LEGEND_SIZE = 12
const FOOTER_SIZE = 11
const LINE = 1.4
const LEGEND_GAP = 16
const SWATCH_GAP = 6

/**
 * Greedy word wrap to `maxWidth`. A word wider than the line on its own (a
 * 200-character suite name) is broken by characters, so nothing runs off the
 * edge of the image — the story's "very long suite lists wrap".
 */
export function wrapText(text: string, maxWidth: number, font: string, measure: MeasureText): string[] {
  const lines: string[] = []
  let current = ''
  const push = () => {
    if (current) lines.push(current)
    current = ''
  }
  for (const word of text.split(/\s+/).filter(Boolean)) {
    const candidate = current ? `${current} ${word}` : word
    if (measure(candidate, font) <= maxWidth) {
      current = candidate
      continue
    }
    push()
    if (measure(word, font) <= maxWidth) {
      current = word
      continue
    }
    // Break the over-long word by code points.
    for (const ch of Array.from(word)) {
      if (current && measure(current + ch, font) > maxWidth) push()
      current += ch
    }
  }
  push()
  return lines.length ? lines : ['']
}

/** `text` cut (with "…") to fit `maxWidth` on one line; unchanged when it already fits. */
export function fitText(text: string, maxWidth: number, font: string, measure: MeasureText): string {
  if (measure(text, font) <= maxWidth) return text
  const chars = Array.from(text)
  let keep = chars.length
  while (keep > 0 && measure(`${chars.slice(0, keep).join('')}…`, font) > maxWidth) keep--
  return `${chars.slice(0, keep).join('')}…`
}

/**
 * Where everything goes in an exported image: title (wrapped), the chart at
 * its on-screen size, the legend (items flow into rows), and the provenance
 * footer (wrapped) in its own band at the bottom. The PNG and the SVG exports
 * both draw THIS layout, so the two files show the same thing.
 */
export function layoutExport({ title, chart, legend, footer, measure, fontFamily = 'system-ui, sans-serif' }: ExportLayoutInput): ExportLayout {
  const width = Math.ceil(Math.max(EXPORT_MIN_WIDTH, chart.width + 2 * EXPORT_PAD))
  const inner = width - 2 * EXPORT_PAD
  const titleFont = `600 ${TITLE_SIZE}px ${fontFamily}`
  const legendFont = `${LEGEND_SIZE}px ${fontFamily}`
  const footerFont = `${FOOTER_SIZE}px ${fontFamily}`
  const text: TextLine[] = []
  let y = EXPORT_PAD

  for (const line of wrapText(title, inner, titleFont, measure)) {
    y += TITLE_SIZE * LINE
    text.push({ text: line, x: EXPORT_PAD, y: y - TITLE_SIZE * (LINE - 1), font: titleFont, size: TITLE_SIZE, weight: 600, role: 'title' })
  }
  y += 8

  const chartBox: Box = { x: EXPORT_PAD + (inner - chart.width) / 2, y, width: chart.width, height: chart.height }
  y += chart.height

  // Legend: items flow left to right and wrap; each row is centred.
  const swatches: Box[] = []
  if (legend.length) {
    y += 12
    type Placed = { width: number; item: LegendItemInput; label: string }
    const rows: Placed[][] = [[]]
    let rowWidth = 0
    for (const item of legend) {
      // A label longer than the whole row is shortened to fit (the full name is in the CSV).
      const room = inner - item.swatch.width - SWATCH_GAP
      const label = fitText(item.label, room, legendFont, measure)
      const itemWidth = item.swatch.width + SWATCH_GAP + measure(label, legendFont)
      const needed = rowWidth ? rowWidth + LEGEND_GAP + itemWidth : itemWidth
      if (needed > inner && rowWidth) {
        rows.push([])
        rowWidth = itemWidth
      } else rowWidth = needed
      rows[rows.length - 1].push({ width: itemWidth, item, label })
    }
    const rowHeight = Math.max(LEGEND_SIZE * LINE, ...legend.map((i) => i.swatch.height))
    for (const row of rows) {
      const total = row.reduce((sum, p, i) => sum + p.width + (i ? LEGEND_GAP : 0), 0)
      let x = EXPORT_PAD + (inner - total) / 2
      for (const placed of row) {
        const { swatch } = placed.item
        swatches.push({ x, y: y + (rowHeight - swatch.height) / 2, width: swatch.width, height: swatch.height })
        text.push({
          text: placed.label,
          x: x + swatch.width + SWATCH_GAP,
          y: y + rowHeight / 2 + LEGEND_SIZE * 0.35,
          font: legendFont,
          size: LEGEND_SIZE,
          weight: 400,
          role: 'legend',
        })
        x += placed.width + LEGEND_GAP
      }
      y += rowHeight + 4
    }
  }

  y += 12
  const footerTop = y
  y += 8
  for (const paragraph of footer) {
    for (const line of wrapText(paragraph, inner, footerFont, measure)) {
      y += FOOTER_SIZE * LINE
      text.push({ text: line, x: EXPORT_PAD, y: y - FOOTER_SIZE * (LINE - 1), font: footerFont, size: FOOTER_SIZE, weight: 400, role: 'footer' })
    }
  }
  y += EXPORT_PAD
  const height = Math.ceil(y)
  return {
    width,
    height,
    fontFamily,
    text,
    chart: chartBox,
    swatches,
    footer: { x: 0, y: footerTop, width, height: height - footerTop },
  }
}
