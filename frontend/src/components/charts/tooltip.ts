/**
 * The ONE tooltip content model every chart speaks (VIZ-601), and the ONLY way
 * to give an ECharts chart a tooltip `formatter` (ADR decision 4).
 *
 * THE MODEL. A tooltip is DATA — a title and ordered rows — never markup. The
 * same `TooltipContent` is drawn three ways, and they cannot disagree because
 * none of them decides what to say:
 *
 *   - `ChartTooltip` (React, `ChartTooltip.tsx`) for every Recharts chart;
 *   - `buildTooltipNode` (DOM, below) for every ECharts chart, through
 *     `domTooltipFormatter` — the SAME element structure, class names and
 *     inline styles as the React component, so the two engines look alike;
 *   - `tooltipText` (one line) for the keyboard cursor and the page announcer,
 *     so what a keyboard or screen-reader user hears is what the pointer shows.
 *
 * The rows carry a `kind`, and the builders below fix both the words and the
 * ORDER every chart uses: the dimension values, the exact value(s), the sample
 * size n, the share of the total, the change against the previous bucket, and
 * last any sentence-length note (a reason, a rule). A reader who has learnt
 * one chart's tooltip has learnt them all.
 *
 * SECURITY. An ECharts `formatter` that returns a STRING is an HTML sink:
 * ECharts assigns it with `innerHTML`, and a test named `<img src=x onerror=…>`
 * executed in the engine spike. Test, suite and release names come from
 * ingested CI files, so they are attacker-influenced.
 *
 * `domTooltipFormatter(render)` takes a function that returns plain DATA and
 * turns it into DOM nodes whose text is set with `textContent`. There is no
 * string path out of it, so there is nothing for markup in a label to be
 * parsed by. Attribute NAMES come only from code and are checked against a
 * `data-*` pattern before they are set; attribute values are text.
 *
 * Enforced twice: `npm run check:theme` (source, `scripts/chart-guard.mjs`)
 * fails on any engine `formatter` not written as `formatter: domTooltipFormatter(…)`,
 * and `assertSafeChartOption` (below, called by `useEChart`) refuses at runtime
 * any option whose formatter this module did not build.
 */
import { formatNumber, formatPercent } from '@/utils/formatters'

/**
 * What a row IS. It fixes the order rows are listed in (`orderRows`) and the
 * one layout difference: a `note` is a sentence, so it wraps under its label
 * instead of sitting in a label/value line.
 */
export type TooltipRowKind = 'dimension' | 'value' | 'sample' | 'share' | 'change' | 'note'

export interface TooltipRow {
  label: string
  value: string
  /** Default `'value'`. */
  kind?: TooltipRowKind
  /** Stable identity for React keys; defaults to the label. */
  key?: string
  /** A resolved colour for the row's swatch (a token, never a hex literal). */
  color?: string
  /** The swatch's shape: a square (default) or a short line, dashed like its series. */
  mark?: 'square' | 'line'
  /** The line swatch's `stroke-dasharray`, so a series is never told by colour alone. */
  dash?: string
  /**
   * More about THIS row, read after its value in parentheses: its own n, its
   * own change, "still filling". A multi-series row carries its series' facts here.
   */
  detail?: string
  /**
   * `data-*` hooks for tests and specs (`data-tip-series`, …). Names are
   * checked against `DATA_ATTRIBUTE`; values are set as text.
   */
  data?: Readonly<Record<string, string>>
}

export interface TooltipContent {
  /** The dimension value the tooltip is FOR: the day, the bar, the slice. */
  title?: string
  rows: TooltipRow[]
}

/** Marks a node as built by this module (asserted by tests and the e2e spec). */
export const TOOLTIP_NODE_ATTRIBUTE = 'data-chart-tooltip'

// ── The words every chart shares ─────────────────────────────────────────────

/** The sample size behind a value. The heatmap has said "Samples" since VIZ-103. */
export const SAMPLE_LABEL = 'Samples'
export const SHARE_LABEL = 'Share of total'
export const CHANGE_LABEL = 'Change vs previous day'
/** What a change reads when the value did not move. */
export const NO_CHANGE = 'no change'
/** The minus sign, not a hyphen: screen readers say "minus" for it. */
export const MINUS = '−'

const KIND_ORDER: Record<TooltipRowKind, number> = {
  dimension: 0,
  value: 1,
  sample: 2,
  share: 3,
  change: 4,
  note: 5,
}

/**
 * Rows in the one order every tooltip uses. Stable: rows of the same kind keep
 * the order the chart gave them (a multi-series tooltip's descending sort).
 */
export function orderRows(rows: readonly TooltipRow[]): TooltipRow[] {
  return rows
    .map((row, index) => ({ row, index }))
    .sort((a, b) => KIND_ORDER[a.row.kind ?? 'value'] - KIND_ORDER[b.row.kind ?? 'value'] || a.index - b.index)
    .map(({ row }) => row)
}

/** `Samples: 1,000`. */
export function sampleRow(n: number, label = SAMPLE_LABEL): TooltipRow {
  return { kind: 'sample', key: `sample:${label}`, label, value: formatNumber(n) }
}

/**
 * `Share of total: 12.5%` — or nothing at all when there is no whole to be a
 * share of (a total of zero, a negative part, a diverging chart).
 */
export function shareRow(part: number, whole: number, label = SHARE_LABEL): TooltipRow | null {
  if (!Number.isFinite(part) || !Number.isFinite(whole) || whole <= 0 || part < 0) return null
  return { kind: 'share', key: `share:${label}`, label, value: formatPercent((part / whole) * 100) }
}

/**
 * A RATE's change, in percentage POINTS: "95.0% → 96.2%" is "+1.2 pts".
 * "+1.2%" would read as a RELATIVE change (1.2 % of 95, about 1.1 points).
 * Every chart that states a rate's change uses this (Wave 2.4 review F5).
 * A no-break space keeps the number and "pts" on one line wherever the text
 * wraps (baseline review B), as `trendStats` does for its sentences.
 */
export const formatRatePoints = (points: number) =>
  `${formatNumber(points, { maximumFractionDigits: 1 })}${String.fromCharCode(0xa0)}pts`

/** `+1.2 pts`, `−340ms`, `no change`: a signed difference, magnitude formatted by the caller. */
export function formatChange(delta: number, formatMagnitude: (value: number) => string): string {
  if (delta === 0 || !Number.isFinite(delta)) return NO_CHANGE
  const magnitude = formatMagnitude(Math.abs(delta))
  // A difference that rounds to nothing at the display precision ("0.0%",
  // "0ms") is no change: "+0.0%" would claim a direction nobody can see.
  if (!/[1-9]/.test(magnitude)) return NO_CHANGE
  return `${delta > 0 ? '+' : MINUS}${magnitude}`
}

export interface ChangeInput {
  current: number | null
  /** `undefined`: there IS no previous bucket (the first day). `null`: there is one, unmeasured. */
  previous: number | null | undefined
  /** The previous bucket's name, said when it was not measured. */
  previousLabel?: string
  formatMagnitude: (value: number) => string
  label?: string
}

/**
 * The change against the previous bucket. No row when this bucket is itself
 * unmeasured (a gap has no change) or when there is no previous bucket; "—"
 * with the reason when the previous one is a gap, so a missing change is never
 * read as "no change".
 */
export function changeRow({ current, previous, previousLabel, formatMagnitude, label = CHANGE_LABEL }: ChangeInput): TooltipRow | null {
  if (current === null || previous === undefined) return null
  const key = `change:${label}`
  if (previous === null) {
    return {
      kind: 'change',
      key,
      label,
      value: '—',
      detail: previousLabel ? `${previousLabel} not measured` : 'previous not measured',
    }
  }
  return { kind: 'change', key, label, value: formatChange(current - previous, formatMagnitude) }
}

/** Drops the `null`s the conditional builders return, and puts the rows in the one order. */
export function tipContent(title: string | undefined, rows: readonly (TooltipRow | null | undefined | false)[]): TooltipContent {
  const kept = rows.filter((row): row is TooltipRow => Boolean(row))
  return title === undefined ? { rows: orderRows(kept) } : { title, rows: orderRows(kept) }
}

// ── The look, shared by both engines ─────────────────────────────────────────

type TipStyle = Readonly<Record<string, string>>

/**
 * Inline styles, camelCase, used AS IS by React's `style` and converted to
 * `style.setProperty` calls by the DOM builder: one source for how a tooltip
 * looks, whichever engine drew it. Colours are CSS custom properties only.
 */
export const TIP_STYLE = {
  body: { display: 'grid', gap: '2px', fontSize: '12px', lineHeight: '1.35', minWidth: '0' },
  title: { fontWeight: '600', overflowWrap: 'anywhere' },
  row: { display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', columnGap: '6px', rowGap: '0' },
  note: { display: 'block', maxWidth: '20rem', whiteSpace: 'normal', overflowWrap: 'anywhere' },
  swatch: { display: 'inline-block', width: '8px', height: '8px', borderRadius: '2px', flexShrink: '0', alignSelf: 'center' },
  line: { display: 'inline-block', flexShrink: '0', alignSelf: 'center' },
  label: { color: 'var(--color-text-secondary)', minWidth: '0', overflowWrap: 'anywhere' },
  // A value may wrap (a local-time range is long); a row wraps first, so a short one never does.
  value: { marginLeft: 'auto', paddingLeft: '8px', fontWeight: '600', fontVariantNumeric: 'tabular-nums', overflowWrap: 'anywhere' },
  noteValue: { fontWeight: '400' },
  detail: { color: 'var(--color-text-secondary)', whiteSpace: 'normal' },
} as const satisfies Record<string, TipStyle>

/** The class names, the same in both engines (specs and tests select on them). */
export const TIP_CLASS = {
  body: 'chart-tooltip',
  title: 'chart-tooltip-title font-semibold',
  row: 'chart-tooltip-row',
  note: 'chart-tooltip-row chart-tooltip-note',
  swatch: 'chart-tooltip-swatch',
  label: 'chart-tooltip-label',
  value: 'chart-tooltip-value',
  detail: 'chart-tooltip-detail',
} as const

/** The line swatch's length: longer than every dash period in the kit, so each pattern shows whole. */
export const TIP_LINE_SWATCH = 24

/** Only `data-*` names built from code; anything else is dropped rather than set. */
export const DATA_ATTRIBUTE = /^data-[a-z0-9]+(?:-[a-z0-9]+)*$/

/** Keeps only the `data-*` hooks that are safe to set as attributes. */
export function safeDataAttributes(data: TooltipRow['data']): [string, string][] {
  if (!data) return []
  return Object.entries(data).filter(([name, value]) => DATA_ATTRIBUTE.test(name) && typeof value === 'string')
}

/** The label a row is shown with: a note with no label is just its sentence. */
export const rowLabelText = (row: TooltipRow) => (row.kind === 'note' && row.label ? `${row.label}: ` : row.label)

const kebab = (name: string) => name.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)

function applyStyle(el: Element, style: TipStyle) {
  const target = (el as HTMLElement | SVGElement).style
  for (const [name, value] of Object.entries(style)) target.setProperty(kebab(name), value)
}

function textElement(tag: string, text: string, className: string, style: TipStyle): HTMLElement {
  const el = document.createElement(tag)
  el.className = className
  el.textContent = text
  applyStyle(el, style)
  return el
}

const SVG_NS = 'http://www.w3.org/2000/svg'

function swatchNode(row: TooltipRow): Element | null {
  if (!row.color) return null
  if (row.mark === 'line') {
    const svg = document.createElementNS(SVG_NS, 'svg')
    svg.setAttribute('aria-hidden', 'true')
    svg.setAttribute('focusable', 'false')
    svg.setAttribute('width', String(TIP_LINE_SWATCH))
    svg.setAttribute('height', '10')
    svg.setAttribute('class', TIP_CLASS.swatch)
    applyStyle(svg, TIP_STYLE.line)
    const line = document.createElementNS(SVG_NS, 'line')
    line.setAttribute('x1', '1')
    line.setAttribute('y1', '5')
    line.setAttribute('x2', String(TIP_LINE_SWATCH - 1))
    line.setAttribute('y2', '5')
    line.setAttribute('stroke', row.color)
    line.setAttribute('stroke-width', '2')
    if (row.dash) line.setAttribute('stroke-dasharray', row.dash)
    svg.appendChild(line)
    return svg
  }
  const swatch = document.createElement('span')
  swatch.setAttribute('aria-hidden', 'true')
  swatch.className = TIP_CLASS.swatch
  applyStyle(swatch, TIP_STYLE.swatch)
  swatch.style.backgroundColor = row.color
  return swatch
}

/**
 * Builds tooltip content as DOM — the same structure `ChartTooltipBody`
 * renders in React. Every string lands in `textContent`.
 */
export function buildTooltipNode(content: TooltipContent): HTMLElement {
  const root = document.createElement('div')
  root.className = TIP_CLASS.body
  root.setAttribute(TOOLTIP_NODE_ATTRIBUTE, '')
  applyStyle(root, TIP_STYLE.body)
  if (content.title !== undefined) root.appendChild(textElement('div', content.title, TIP_CLASS.title, TIP_STYLE.title))
  for (const row of content.rows) {
    const note = row.kind === 'note'
    const line = document.createElement('div')
    line.className = note ? TIP_CLASS.note : TIP_CLASS.row
    line.setAttribute('data-tip-kind', row.kind ?? 'value')
    for (const [name, value] of safeDataAttributes(row.data)) line.setAttribute(name, value)
    applyStyle(line, note ? TIP_STYLE.note : TIP_STYLE.row)
    const swatch = note ? null : swatchNode(row)
    if (swatch) line.appendChild(swatch)
    if (rowLabelText(row)) line.appendChild(textElement('span', rowLabelText(row), TIP_CLASS.label, TIP_STYLE.label))
    const value = textElement('span', row.value, TIP_CLASS.value, note ? TIP_STYLE.noteValue : TIP_STYLE.value)
    value.setAttribute('data-tip-value', '')
    line.appendChild(value)
    if (row.detail) line.appendChild(textElement('span', `(${row.detail})`, TIP_CLASS.detail, TIP_STYLE.detail))
    root.appendChild(line)
  }
  return root
}

/** Every formatter this module has built; nothing else may reach ECharts. */
const approvedFormatters = new WeakSet<object>()

/** Wraps a data-returning render function as an ECharts `formatter` that returns DOM. */
export function domTooltipFormatter<P>(render: (params: P) => TooltipContent): (params: P) => HTMLElement {
  const formatter = (params: P) => buildTooltipNode(render(params))
  approvedFormatters.add(formatter)
  return formatter
}

const MAX_OPTION_DEPTH = 32

/**
 * The runtime half of the formatter rule. `check:theme` reads source, so an
 * option assembled across files from innocently named variables can slip past
 * it; this walks the option actually handed to ECharts. Any `*formatter` key
 * (any case) whose value is not a function built by `domTooltipFormatter` is
 * refused -- a string included, since ECharts renders a string tooltip as HTML
 * with the data substituted in. Returns the offending path, or `null`.
 */
export function findUnsafeFormatter(option: unknown): string | null {
  const seen = new WeakSet<object>()
  const walk = (value: unknown, path: string, depth: number): string | null => {
    if (value === null || typeof value !== 'object') return null
    if (seen.has(value)) return null
    seen.add(value)
    if (depth > MAX_OPTION_DEPTH) return `${path} (nested deeper than ${MAX_OPTION_DEPTH})`
    // A DOM node or typed array carries no option keys worth walking.
    if (typeof Node !== 'undefined' && value instanceof Node) return null
    if (ArrayBuffer.isView(value)) return null
    for (const key of Reflect.ownKeys(value)) {
      const name = typeof key === 'symbol' ? key.toString() : key
      const child = (value as Record<PropertyKey, unknown>)[key]
      const childPath = Array.isArray(value) ? `${path}[${name}]` : `${path}.${name}`
      if (/formatter$/i.test(name) && child !== undefined && child !== null) {
        if (typeof child !== 'function' || !approvedFormatters.has(child)) return childPath
        continue
      }
      const found = walk(child, childPath, depth + 1)
      if (found) return found
    }
    return null
  }
  return walk(option, 'option', 0)
}

/** Throws when `findUnsafeFormatter` finds anything; the engine hook turns it into the error state. */
export function assertSafeChartOption(option: unknown): void {
  const path = findUnsafeFormatter(option)
  if (path) throw new Error(`Refused chart option: ${path} is not a domTooltipFormatter (ADR decision 4)`)
}

/** One row as the cursor says it: `Label: value (detail)`; a label-less note is its sentence. */
export function rowText(row: TooltipRow): string {
  const head = row.label ? `${row.label}: ${row.value}` : row.value
  return row.detail ? `${head} (${row.detail})` : head
}

/**
 * The same content as one line of text, for the page announcer and the
 * keyboard cursor: what a keyboard or screen-reader user hears is what the
 * tooltip shows, in the same order. Parts are joined with ". " — a pause a
 * screen reader honours — unless a part already ends a sentence.
 */
export function tooltipText(content: TooltipContent): string {
  const parts = [content.title, ...content.rows.map(rowText)].filter(
    (part): part is string => part !== undefined && part.trim() !== '',
  )
  return parts.reduce((text, part) => (text === '' ? part : /[.!?]$/.test(text) ? `${text} ${part}` : `${text}. ${part}`), '')
}
