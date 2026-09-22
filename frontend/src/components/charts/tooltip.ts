/**
 * The ONLY way to give an ECharts chart a tooltip `formatter` (ADR decision 4).
 *
 * An ECharts `formatter` that returns a STRING is an HTML sink: ECharts assigns
 * it with `innerHTML`, and a test named `<img src=x onerror=…>` executed in the
 * engine spike. Test, suite and release names come from ingested CI files, so
 * they are attacker-influenced.
 *
 * `domTooltipFormatter(render)` takes a function that returns plain DATA (a
 * title and label/value rows) and turns it into DOM nodes whose text is set
 * with `textContent`. There is no string path out of it, so there is nothing
 * for markup in a label to be parsed by.
 *
 * Enforced twice: `npm run check:theme` (source, `scripts/chart-guard.mjs`)
 * fails on any engine `formatter` not written as `formatter: domTooltipFormatter(…)`,
 * and `assertSafeChartOption` (below, called by `useEChart`) refuses at runtime
 * any option whose formatter this module did not build.
 */

export interface TooltipRow {
  label: string
  value: string
  /** A resolved token colour for the row's swatch (from `readChartTokens()`). */
  color?: string
}

export interface TooltipContent {
  title?: string
  rows: TooltipRow[]
}

/** Marks a node as built by this module (asserted by tests and the e2e spec). */
export const TOOLTIP_NODE_ATTRIBUTE = 'data-chart-tooltip'

function textElement(tag: string, text: string, className: string): HTMLElement {
  const el = document.createElement(tag)
  el.className = className
  el.textContent = text
  return el
}

/** Builds tooltip content as DOM. Every string lands in `textContent`. */
export function buildTooltipNode(content: TooltipContent): HTMLElement {
  const root = document.createElement('div')
  root.setAttribute(TOOLTIP_NODE_ATTRIBUTE, '')
  root.style.display = 'grid'
  root.style.gap = '2px'
  root.style.fontSize = '12px'
  if (content.title !== undefined) {
    const title = textElement('div', content.title, 'chart-tooltip-title')
    title.style.fontWeight = '600'
    root.appendChild(title)
  }
  for (const row of content.rows) {
    const line = document.createElement('div')
    line.className = 'chart-tooltip-row'
    line.style.display = 'flex'
    line.style.alignItems = 'center'
    line.style.gap = '6px'
    if (row.color) {
      const swatch = document.createElement('span')
      swatch.setAttribute('aria-hidden', 'true')
      swatch.style.display = 'inline-block'
      swatch.style.width = '8px'
      swatch.style.height = '8px'
      swatch.style.borderRadius = '2px'
      swatch.style.backgroundColor = row.color
      line.appendChild(swatch)
    }
    line.appendChild(textElement('span', row.label, 'chart-tooltip-label'))
    const value = textElement('span', row.value, 'chart-tooltip-value')
    value.style.marginLeft = 'auto'
    value.style.fontWeight = '600'
    line.appendChild(value)
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

/**
 * The same content as one line of text, for a live region: what a keyboard or
 * screen-reader user hears is what the tooltip shows.
 */
export function tooltipText(content: TooltipContent): string {
  const rows = content.rows.map((row) => `${row.label}: ${row.value}`)
  return [content.title, ...rows].filter((part) => part !== undefined && part !== '').join('. ')
}
