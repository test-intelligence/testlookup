/**
 * Per-chart export (VIZ-606) — the DOM half: turn a drawn chart into a
 * standalone SVG document or PNG, laid out by `layoutExport` (title, chart,
 * legend, provenance footer).
 *
 * Loaded with a dynamic `import()` when the reader picks an image format, so
 * none of it is in any chunk until then, and all of it runs on that click —
 * never on the render path.
 *
 * Why the work is what it is:
 *
 *   - An exported SVG has no stylesheet. Recharts draws with `var(--chart-…)`
 *     presentation attributes and Tailwind classes, which in a file on disk
 *     resolve to nothing (black fills, invisible strokes). So the chart is
 *     CLONED into an off-screen host carrying the export's theme, and every
 *     element's COMPUTED paint and text properties are written onto it as
 *     inline style; `class` goes. "Light background" is the same move with the
 *     host set to the light theme (`LIGHT_THEME`): the chart tokens are defined
 *     per `[data-theme]` block, so the clone re-resolves to the light palette
 *     without the page switching theme.
 *   - Patterns (`fill="url(#…)"`) often live in a `<defs>` elsewhere on the
 *     page; each one a clone references is copied in, or the status hatching
 *     that makes the chart readable without colour would vanish.
 *   - Every name — title, legend labels, the footer's project / release /
 *     suite — becomes an SVG `<text>` via `textContent`, and the document is
 *     written by `XMLSerializer`, which escapes it. No string of markup is ever
 *     assembled from a name, so `<img src=x onerror=…>` ends up as literal
 *     text. `on*` attributes and non-fragment links are stripped from clones
 *     as well, and so are elements that could run or fetch anything
 *     (`<script>`, `<style>`, `<foreignObject>`, animation): the file must
 *     be inert wherever it is opened.
 *   - Escaping is not enough for a file to PARSE: XML 1.0 forbids most C0
 *     control characters outright, and `XMLSerializer` writes them raw. A
 *     test name carrying an ANSI colour code (ESC, U+001B — common in CI
 *     output) made the whole `.svg` malformed and the PNG fail to draw. Every
 *     character XML cannot hold becomes U+FFFD, in the chart clone's text
 *     and attributes and in every text the export adds (`xmlSafeText`).
 *   - The PNG is drawn at `EXPORT_PIXEL_RATIO` (2×, for slides) onto a canvas,
 *     and the title, legend labels and FOOTER are drawn onto that canvas with
 *     `fillText` — part of the pixels, so the scope cannot be cropped off by
 *     accident the way an HTML caption next to an image can.
 *   - No new dependency (no html2canvas): an SVG → `<img>` → canvas round-trip
 *     is all the browser needs.
 */
import {
  EXPORT_PIXEL_RATIO,
  footerParagraphs,
  layoutExport,
  type ChartProvenance,
  type ExportLayout,
  type MeasureText,
} from './chartExport'

const SVG_NS = 'http://www.w3.org/2000/svg'
const XLINK_NS = 'http://www.w3.org/1999/xlink'

/** The light theme's id in `store/themeStore.ts` (its `[data-theme]` block in `index.css`). */
export const LIGHT_THEME = 'lab'

/** Where the chart's pixels come from. */
export type ChartSource =
  /** A Recharts chart: its `<svg>`, cloned and style-inlined. */
  | { kind: 'svg'; svg: SVGSVGElement }
  /** An engine that draws on a canvas (ECharts): its own image, at the export pixel ratio. */
  | { kind: 'raster'; dataUrl: string; width: number; height: number }

export interface ChartImageInput {
  title: string
  /** The chart body: searched for the legend. */
  body: HTMLElement
  source: ChartSource
  provenance: ChartProvenance | null
  /** Draw in the light theme on a light background instead of the current theme. */
  light?: boolean
}

/** Something `getComputedStyle` returns, narrowed: a test can pass a fake. */
export type ReadStyle = (el: Element) => Pick<CSSStyleDeclaration, 'getPropertyValue'>

const defaultReadStyle: ReadStyle = (el) => window.getComputedStyle(el)

/** The paint and text properties an exported SVG needs to look like the screen. */
export const INLINED_PROPERTIES = [
  'fill',
  'fill-opacity',
  'fill-rule',
  'stroke',
  'stroke-width',
  'stroke-opacity',
  'stroke-dasharray',
  'stroke-linecap',
  'stroke-linejoin',
  'opacity',
  'font-family',
  'font-size',
  'font-weight',
  'font-style',
  'text-anchor',
  'dominant-baseline',
] as const

/**
 * Paint properties that exist only on some elements: inlined there alone, or
 * every element of the clone would carry a `stop-color` it has no use for. A
 * gradient stop coloured by a token would otherwise lose its `var()`
 * attribute below and export black.
 */
const INLINED_BY_ELEMENT: Record<string, readonly string[]> = {
  stop: ['stop-color', 'stop-opacity'],
  feFlood: ['flood-color', 'flood-opacity'],
  feDropShadow: ['flood-color', 'flood-opacity'],
}

/**
 * Transient marks that describe the READER'S pointer, not the data: the hover
 * cursor band and the enlarged "active" dot. An export of a hovered chart
 * must not freeze them in.
 */
const TRANSIENT_SELECTOR = '.recharts-tooltip-cursor, .recharts-active-dot'

/**
 * Elements a standalone file must not carry: anything that runs, styles from
 * outside the inlined values, embeds foreign markup or animates. The chart
 * kit draws none of them; a `<defs>` copied in from elsewhere on the page
 * could.
 */
const UNSAFE_SELECTOR = 'script, style, foreignObject, iframe, animate, animateMotion, animateTransform, set'

/**
 * Every character XML 1.0's `Char` production excludes: C0 controls other
 * than TAB, LF and CR, U+FFFE / U+FFFF, and unpaired surrogates.
 */
const XML_ILLEGAL = /[^\t\n\r\u0020-\uD7FF\uE000-\uFFFD\u{10000}-\u{10FFFF}]/gu

/** `text` with every character XML cannot hold replaced by U+FFFD — still readable, and the file still parses. */
export function xmlSafeText(text: string): string {
  return text.replace(XML_ILLEGAL, '\uFFFD')
}

/** `xmlSafeText` over every text node and attribute value of `root` (itself included). */
function makeXmlSafe(root: Element): void {
  const walker = root.ownerDocument.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.nodeValue ?? ''
    const safe = xmlSafeText(text)
    if (safe !== text) node.nodeValue = safe
  }
  for (const el of [root, ...Array.from(root.querySelectorAll('*'))]) {
    for (const attribute of Array.from(el.attributes)) {
      const safe = xmlSafeText(attribute.value)
      if (safe !== attribute.value) attribute.value = safe
    }
  }
}

/** `url("http://host/page#id")` (how some browsers compute it) → `url(#id)`, which a standalone file resolves. */
function localUrl(value: string): string {
  return value.replace(/url\((["']?)[^#)"']*#([^)"']+)\1\)/g, 'url(#$2)')
}

/**
 * The resolved values of `INLINED_PROPERTIES` written onto each element of
 * `root` (itself included) as inline style; `class`, `on*` handlers and any
 * link that is not a same-document fragment or an embedded image removed.
 */
export function inlineComputedStyles(root: Element, readStyle: ReadStyle = defaultReadStyle): void {
  const elements = [root, ...Array.from(root.querySelectorAll('*'))]
  // Read every style BEFORE writing any: interleaving reads and writes forces
  // a style recalculation per element, which is the difference between a few
  // milliseconds and seconds on a chart with thousands of marks.
  const resolved = elements.map((el) => {
    const style = readStyle(el)
    const declarations: string[] = []
    for (const property of [...INLINED_PROPERTIES, ...(INLINED_BY_ELEMENT[el.localName] ?? [])]) {
      const value = style.getPropertyValue(property).trim()
      if (value && !value.includes('var(')) declarations.push(`${property}:${localUrl(value)}`)
    }
    if (style.getPropertyValue('display').trim() === 'none') declarations.push('display:none')
    return declarations.join(';')
  })
  elements.forEach((el, index) => {
    el.removeAttribute('class')
    for (const attribute of Array.from(el.attributes)) {
      const name = attribute.name.toLowerCase()
      if (name.startsWith('on')) el.removeAttribute(attribute.name)
      else if ((name === 'href' || name === 'xlink:href') && !/^(#|data:image\/)/.test(attribute.value.trim()))
        el.removeAttribute(attribute.name)
      // A presentation attribute still holding var() would resolve to nothing in a file.
      else if (attribute.value.includes('var(')) el.removeAttribute(attribute.name)
    }
    if (resolved[index]) el.setAttribute('style', resolved[index])
    else el.removeAttribute('style')
  })
}

/** The ids every `url(#id)` / `href="#id"` inside `root` points at. */
function referencedIds(root: Element): Set<string> {
  const ids = new Set<string>()
  for (const el of [root, ...Array.from(root.querySelectorAll('*'))]) {
    for (const attribute of Array.from(el.attributes)) {
      for (const match of attribute.value.matchAll(/url\(\s*["']?#([^)"'\s]+)/g)) ids.add(match[1])
      if ((attribute.name === 'href' || attribute.name === 'xlink:href') && attribute.value.startsWith('#'))
        ids.add(attribute.value.slice(1))
    }
  }
  return ids
}

/**
 * Copies into `clone` every definition (pattern, gradient, clip path, marker)
 * it references but does not contain, looked up in `source` — the page — and
 * the definitions THOSE reference, transitively.
 */
export function copyReferencedDefs(clone: SVGSVGElement, source: ParentNode = document): void {
  let defs: Element | null = null
  const pending = [...referencedIds(clone)]
  const seen = new Set<string>()
  while (pending.length) {
    const id = pending.pop() as string
    if (seen.has(id)) continue
    seen.add(id)
    const inside = Array.from(clone.querySelectorAll('[id]')).some((el) => el.id === id)
    if (inside) continue
    const original = Array.from(source.querySelectorAll('[id]')).find((el) => el.id === id)
    if (!original || !(original instanceof SVGElement)) continue
    if (!defs) {
      defs = clone.ownerDocument.createElementNS(SVG_NS, 'defs')
      clone.insertBefore(defs, clone.firstChild)
    }
    const copy = original.cloneNode(true) as Element
    defs.appendChild(copy)
    pending.push(...referencedIds(copy))
  }
}

/** Width and height in CSS px: the attributes Recharts writes, else the laid-out box. */
export function svgSize(svg: SVGSVGElement): { width: number; height: number } {
  const attr = (name: string) => {
    const value = Number.parseFloat(svg.getAttribute(name) ?? '')
    return Number.isFinite(value) && value > 0 && !/%$/.test(svg.getAttribute(name) ?? '') ? value : 0
  }
  const box = svg.getBoundingClientRect()
  return { width: attr('width') || box.width, height: attr('height') || box.height }
}

/**
 * The chart's main `<svg>` inside a body: the LARGEST one, because a legend's
 * swatches are `<svg>`s too (and so is a hidden `<defs>` holder of size 0).
 */
export function findChartSvg(body: ParentNode): SVGSVGElement | null {
  let best: SVGSVGElement | null = null
  let bestArea = 0
  for (const svg of Array.from(body.querySelectorAll('svg'))) {
    if (svg.closest('[data-chart-legend], [data-multi-series-legend]')) continue
    const { width, height } = svgSize(svg)
    if (width * height > bestArea) {
      best = svg
      bestArea = width * height
    }
  }
  return best
}

export interface LegendItem {
  label: string
  /** The swatch as drawn on the page (not yet cloned); `null` for a text-only entry. */
  swatch: SVGSVGElement | null
}

/**
 * The legend as the page draws it — `ChartLegend` (`[data-chart-legend]`) or
 * the multi-series toggles (`[data-multi-series-legend]`) — so the exported
 * legend is the chart's own, swatch for swatch. A series the reader hid is
 * listed as "(hidden)": it is not in the picture, and the legend says so.
 */
export function extractLegend(body: ParentNode): LegendItem[] {
  const items: LegendItem[] = []
  for (const li of Array.from(body.querySelectorAll('[data-chart-legend] > li, [data-multi-series-legend] li'))) {
    const label = (li.textContent ?? '').replace(/\s+/g, ' ').trim()
    if (!label) continue
    const hidden = li.querySelector('[data-legend-hidden="true"]') !== null
    items.push({ label: hidden ? `${label} (hidden)` : label, swatch: li.querySelector('svg') })
  }
  return items
}

/** A text-width measure: a canvas's `measureText`, or an estimate where there is no canvas (tests). */
export function createMeasure(): MeasureText {
  let ctx: CanvasRenderingContext2D | null
  try {
    ctx = document.createElement('canvas').getContext('2d')
  } catch {
    ctx = null
  }
  if (ctx) {
    const context = ctx
    return (text, font) => {
      context.font = font
      return context.measureText(text).width
    }
  }
  return (text, font) => {
    const size = Number.parseFloat(/(\d+(?:\.\d+)?)px/.exec(font)?.[1] ?? '12')
    return Array.from(text).length * size * 0.6
  }
}

export interface ExportPalette {
  background: string
  text: string
  muted: string
  rule: string
}

/** A CSS colour expression (`var(--…)` included) resolved to a concrete colour inside `host`. */
function resolveColor(host: HTMLElement, expression: string, readStyle: ReadStyle): string {
  const probe = host.ownerDocument.createElement('span')
  probe.style.color = expression
  host.appendChild(probe)
  const value = readStyle(probe).getPropertyValue('color').trim()
  probe.remove()
  return value && !value.includes('var(') ? value : ''
}

/** The frame's own colours, in the export's theme. The fallbacks only matter where nothing resolves (tests). */
export function readPalette(host: HTMLElement, light: boolean, readStyle: ReadStyle = defaultReadStyle): ExportPalette {
  return {
    background: resolveColor(host, 'var(--color-bg-card)', readStyle) || (light ? 'white' : 'black'),
    text: resolveColor(host, 'var(--color-text)', readStyle) || (light ? 'black' : 'white'),
    muted: resolveColor(host, 'var(--color-text-secondary)', readStyle) || 'gray',
    rule: resolveColor(host, 'var(--color-border)', readStyle) || 'gray',
  }
}

/**
 * An off-screen element carrying the export's theme. Clones are styled
 * inside it, so their computed values are that theme's. Not `display: none`
 * (nothing inside would compute) and not `visibility: hidden` (every child
 * would inherit it into the export).
 */
function createHost(theme: string | null): HTMLDivElement {
  const host = document.createElement('div')
  if (theme) host.setAttribute('data-theme', theme)
  host.setAttribute('aria-hidden', 'true')
  host.style.cssText =
    'position:fixed;left:-100000px;top:0;pointer-events:none;color:var(--color-text);font-family:var(--font-sans, system-ui, sans-serif)'
  document.body.appendChild(host)
  return host
}

/** A clone of `svg`, styled in `host`'s theme and made standalone. */
function standaloneClone(svg: SVGSVGElement, host: HTMLElement, readStyle: ReadStyle, size = svgSize(svg)): SVGSVGElement {
  const clone = svg.cloneNode(true) as SVGSVGElement
  for (const transient of Array.from(clone.querySelectorAll(TRANSIENT_SELECTOR))) transient.remove()
  copyReferencedDefs(clone, document)
  for (const unsafe of Array.from(clone.querySelectorAll(UNSAFE_SELECTOR))) unsafe.remove()
  makeXmlSafe(clone)
  host.appendChild(clone)
  inlineComputedStyles(clone, readStyle)
  clone.setAttribute('xmlns', SVG_NS)
  clone.setAttribute('width', String(size.width))
  clone.setAttribute('height', String(size.height))
  if (!clone.getAttribute('viewBox')) clone.setAttribute('viewBox', `0 0 ${size.width} ${size.height}`)
  clone.removeAttribute('aria-hidden')
  return clone
}

/** Everything the two writers draw, resolved in the export's theme. */
export interface ExportScene {
  layout: ExportLayout
  palette: ExportPalette
  /** The styled chart clone, or the engine's image. */
  chart: { kind: 'svg'; svg: SVGSVGElement } | { kind: 'raster'; dataUrl: string }
  /** Styled swatch clones, one per legend entry (`null` for a text-only entry). */
  swatches: (SVGSVGElement | null)[]
}

/** Builds the scene: clones, styles, lays out. Removes its off-screen host before returning. */
export function buildScene(
  { title, body, source, provenance, light = false }: ChartImageInput,
  readStyle: ReadStyle = defaultReadStyle,
  measure: MeasureText = createMeasure(),
): ExportScene {
  const theme = light ? LIGHT_THEME : document.documentElement.getAttribute('data-theme')
  const host = createHost(theme)
  try {
    const palette = readPalette(host, light, readStyle)
    const chartSize = source.kind === 'svg' ? svgSize(source.svg) : { width: source.width, height: source.height }
    const chart: ExportScene['chart'] =
      source.kind === 'svg'
        ? { kind: 'svg', svg: standaloneClone(source.svg, host, readStyle, chartSize) }
        : { kind: 'raster', dataUrl: source.dataUrl }
    const legend = extractLegend(body)
    const swatches = legend.map((item) => (item.swatch ? standaloneClone(item.swatch, host, readStyle) : null))
    const fontFamily = readStyle(host).getPropertyValue('font-family').trim() || 'system-ui, sans-serif'
    // Every text the export adds is laid out — and so drawn, in both files — XML-safe.
    const layout = layoutExport({
      title: xmlSafeText(title),
      chart: chartSize,
      legend: legend.map((item, index) => {
        const swatch = swatches[index]
        return { label: xmlSafeText(item.label), swatch: swatch ? svgSize(swatch) : { width: 0, height: 0 } }
      }),
      footer: footerParagraphs(provenance).map(xmlSafeText),
      measure,
      fontFamily,
    })
    return { layout, palette, chart, swatches }
  } finally {
    host.remove()
  }
}

// ── SVG writer ────────────────────────────────────────────────────────────────

function place(el: Element, box: { x: number; y: number; width: number; height: number }) {
  el.setAttribute('x', String(box.x))
  el.setAttribute('y', String(box.y))
  el.setAttribute('width', String(box.width))
  el.setAttribute('height', String(box.height))
}

/** The scene as one standalone SVG element (not attached to any document). */
export function sceneToSvg(scene: ExportScene, title: string): SVGSVGElement {
  const { layout, palette } = scene
  const doc = document.implementation.createDocument(SVG_NS, 'svg', null)
  const root = doc.documentElement as unknown as SVGSVGElement
  root.setAttribute('xmlns:xlink', XLINK_NS)
  root.setAttribute('width', String(layout.width))
  root.setAttribute('height', String(layout.height))
  root.setAttribute('viewBox', `0 0 ${layout.width} ${layout.height}`)
  root.setAttribute('font-family', layout.fontFamily)
  const heading = doc.createElementNS(SVG_NS, 'title')
  heading.textContent = xmlSafeText(title)
  root.appendChild(heading)

  const background = doc.createElementNS(SVG_NS, 'rect')
  place(background, { x: 0, y: 0, width: layout.width, height: layout.height })
  background.setAttribute('fill', palette.background)
  root.appendChild(background)

  if (scene.chart.kind === 'svg') {
    const chart = doc.importNode(scene.chart.svg, true)
    place(chart, layout.chart)
    root.appendChild(chart)
  } else {
    const image = doc.createElementNS(SVG_NS, 'image')
    place(image, layout.chart)
    // Both spellings: SVG 2 readers take `href`, older ones only `xlink:href`.
    image.setAttribute('href', scene.chart.dataUrl)
    image.setAttributeNS(XLINK_NS, 'xlink:href', scene.chart.dataUrl)
    root.appendChild(image)
  }

  scene.swatches.forEach((swatch, index) => {
    if (!swatch || !layout.swatches[index]) return
    const copy = doc.importNode(swatch, true)
    place(copy, layout.swatches[index])
    root.appendChild(copy)
  })

  const rule = doc.createElementNS(SVG_NS, 'line')
  rule.setAttribute('x1', '16')
  rule.setAttribute('x2', String(layout.width - 16))
  rule.setAttribute('y1', String(layout.footer.y))
  rule.setAttribute('y2', String(layout.footer.y))
  rule.setAttribute('stroke', palette.rule)
  root.appendChild(rule)

  for (const line of layout.text) {
    const text = doc.createElementNS(SVG_NS, 'text')
    text.setAttribute('x', String(line.x))
    text.setAttribute('y', String(line.y))
    text.setAttribute('font-size', String(line.size))
    text.setAttribute('font-weight', String(line.weight))
    text.setAttribute('fill', line.role === 'title' ? palette.text : palette.muted)
    text.setAttribute('data-export-role', line.role)
    // textContent, never markup: a hostile name stays literal text.
    text.textContent = xmlSafeText(line.text)
    root.appendChild(text)
  }
  return root
}

/** The scene as an `image/svg+xml` file body. */
export function sceneToSvgText(scene: ExportScene, title: string): string {
  return `<?xml version="1.0" encoding="UTF-8"?>\n${new XMLSerializer().serializeToString(sceneToSvg(scene, title))}`
}

// ── PNG writer ────────────────────────────────────────────────────────────────

/** The slice of a 2D context the painter uses — a test passes a recorder. */
export type PaintContext = Pick<
  CanvasRenderingContext2D,
  'scale' | 'fillRect' | 'drawImage' | 'fillText' | 'beginPath' | 'moveTo' | 'lineTo' | 'stroke'
> & { fillStyle: unknown; strokeStyle: unknown; font: string; textBaseline: CanvasTextBaseline; lineWidth: number }

export interface PaintImages {
  chart: CanvasImageSource
  swatches: (CanvasImageSource | null)[]
}

/** The canvas size for a layout: `EXPORT_PIXEL_RATIO` times its CSS size. */
export function canvasSize(layout: ExportLayout, pixelRatio = EXPORT_PIXEL_RATIO): { width: number; height: number } {
  return { width: Math.round(layout.width * pixelRatio), height: Math.round(layout.height * pixelRatio) }
}

/**
 * Paints a scene: background, chart image, swatches, the rule over the
 * footer, then every text line — title, legend labels, footer — with
 * `fillText`, INTO the pixels.
 */
export function paintScene(
  ctx: PaintContext,
  scene: ExportScene,
  images: PaintImages,
  pixelRatio = EXPORT_PIXEL_RATIO,
): void {
  const { layout, palette } = scene
  ctx.scale(pixelRatio, pixelRatio)
  ctx.fillStyle = palette.background
  ctx.fillRect(0, 0, layout.width, layout.height)
  const { chart } = layout
  ctx.drawImage(images.chart, chart.x, chart.y, chart.width, chart.height)
  images.swatches.forEach((image, index) => {
    const box = layout.swatches[index]
    if (image && box) ctx.drawImage(image, box.x, box.y, box.width, box.height)
  })
  ctx.strokeStyle = palette.rule
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.moveTo(16, layout.footer.y + 0.5)
  ctx.lineTo(layout.width - 16, layout.footer.y + 0.5)
  ctx.stroke()
  ctx.textBaseline = 'alphabetic'
  for (const line of layout.text) {
    ctx.font = line.font
    ctx.fillStyle = line.role === 'title' ? palette.text : palette.muted
    ctx.fillText(line.text, line.x, line.y)
  }
}

/** An `<img>` for a URL, decoded. */
function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image()
    image.onload = () => resolve(image)
    image.onerror = () => reject(new Error('The chart image could not be drawn.'))
    image.src = src
  })
}

const svgDataUrl = (svg: SVGSVGElement) =>
  `data:image/svg+xml;charset=utf-8,${encodeURIComponent(new XMLSerializer().serializeToString(svg))}`

/** The scene as a PNG blob, at `EXPORT_PIXEL_RATIO`. */
export async function sceneToPng(scene: ExportScene): Promise<Blob> {
  const size = canvasSize(scene.layout)
  const canvas = document.createElement('canvas')
  canvas.width = size.width
  canvas.height = size.height
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('This browser cannot draw images for export.')
  const [chart, ...swatches] = await Promise.all([
    loadImage(scene.chart.kind === 'svg' ? svgDataUrl(scene.chart.svg) : scene.chart.dataUrl),
    ...scene.swatches.map((swatch) => (swatch ? loadImage(svgDataUrl(swatch)) : Promise.resolve(null))),
  ])
  paintScene(ctx, scene, { chart, swatches })
  return new Promise((resolve, reject) =>
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('The PNG could not be encoded.'))), 'image/png'),
  )
}

/** Export entry point: the chart as a PNG or SVG file body. */
export async function exportChartImage(input: ChartImageInput, format: 'png' | 'svg'): Promise<Blob> {
  const scene = buildScene(input)
  if (format === 'svg') return new Blob([sceneToSvgText(scene, input.title)], { type: 'image/svg+xml;charset=utf-8' })
  return sceneToPng(scene)
}
