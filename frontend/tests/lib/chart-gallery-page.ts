/**
 * What the Wave 2.4 chart specs (tooltip, hostile names, export, full screen,
 * zoom) share about driving the DEV gallery (`/__charts`): opening it, the
 * console watch, a gallery item, hovering a mark the way a pointer does,
 * walking the keyboard cursor, and exporting a file.
 *
 * The user-facing words are spelt out here rather than imported from the
 * components: they are the specification, and the modules that hold them
 * value-import `@/…`, which Playwright's plain-Node transform cannot resolve.
 */
import { readFileSync } from 'node:fs'
import { expect, type Locator, type Page } from '@playwright/test'

export const GALLERY = '/__charts'
export const CHART_SVG = '.recharts-wrapper > svg.recharts-surface'

/** `EXPORT_LABELS` in `ChartExportMenu.tsx`. */
export const EXPORT = {
  trigger: 'Export',
  png: 'PNG image',
  svg: 'SVG image',
  csv: 'CSV data',
} as const

/** The story's file name: `testlookup_<chart>_<project>_<yyyymmdd-hhmm>Z.<ext>`. */
export const exportFileName = (ext: 'png' | 'svg' | 'csv') =>
  new RegExp(`^testlookup_[^_]+_[^_]+_\\d{8}-\\d{4}Z\\.${ext}$`)

/** Console errors and uncaught exceptions, collected from before navigation. */
export function watchErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(`console.error: ${message.text()}`)
  })
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`))
  return errors
}

export async function openGallery(page: Page, search = '') {
  await page.goto(`${GALLERY}${search}`)
  // Fail, never skip: a 404 or an auth bounce lands on /overview → /login.
  expect(new URL(page.url()).pathname, 'the gallery route redirected').toBe(GALLERY)
  await expect(page.getByTestId('chart-gallery')).toBeVisible()
}

export const galleryItem = (page: Page, id: string): Locator => page.locator(`[data-gallery-item="${id}"]`)

/** `window.__xss`, which `HOSTILE_LABEL`'s `onerror` would set if it ever became an element. */
export const xssFlag = (page: Page) => page.evaluate(() => (window as unknown as { __xss?: unknown }).__xss)

/**
 * A point ON every drawn mark among `marks` (in viewport coordinates), with
 * the mark's box. For a filled shape the point is inside its fill: the centre
 * of a donut slice's BOX is usually the hole in the middle of the ring, and a
 * pointer there is over nothing. A line (a grid line, zero pixels tall) gives
 * its centre.
 */
export async function markCentres(marks: Locator): Promise<{ x: number; y: number; box: DOMRectLike }[]> {
  return marks.evaluateAll((nodes) =>
    nodes
      .filter((node) => {
        const box = node.getBoundingClientRect()
        return box.width > 1 || box.height > 1
      })
      .map((node) => {
        const box = node.getBoundingClientRect()
        let at = { x: box.x + box.width / 2, y: box.y + box.height / 2 }
        const shape = node as SVGGeometryElement
        const ctm = typeof shape.getScreenCTM === 'function' ? shape.getScreenCTM() : null
        if (ctm && typeof shape.isPointInFill === 'function' && node.localName !== 'line') {
          const bb = shape.getBBox()
          const centre = { x: bb.x + bb.width / 2, y: bb.y + bb.height / 2 }
          let best: { x: number; y: number } | null = shape.isPointInFill(new DOMPoint(centre.x, centre.y)) ? centre : null
          if (!best) {
            let bestDistance = Infinity
            const step = { x: bb.width / 20, y: bb.height / 20 }
            const inFill = (x: number, y: number) => shape.isPointInFill(new DOMPoint(x, y))
            for (let i = 1; i < 20; i++) {
              for (let j = 1; j < 20; j++) {
                const p = { x: bb.x + step.x * i, y: bb.y + step.y * j }
                // Well inside, not on an edge: the grid neighbours are in the fill too.
                if (
                  !inFill(p.x, p.y) ||
                  !inFill(p.x - step.x, p.y) ||
                  !inFill(p.x + step.x, p.y) ||
                  !inFill(p.x, p.y - step.y) ||
                  !inFill(p.x, p.y + step.y)
                ) {
                  continue
                }
                const d = (p.x - centre.x) ** 2 + (p.y - centre.y) ** 2
                if (d < bestDistance) {
                  bestDistance = d
                  best = p
                }
              }
            }
          }
          if (best) {
            const screen = new DOMPoint(best.x, best.y).matrixTransform(ctm)
            at = { x: screen.x, y: screen.y }
          }
        }
        return { ...at, box: { x: box.x, y: box.y, width: box.width, height: box.height } }
      }),
  )
}

export interface DOMRectLike {
  x: number
  y: number
  width: number
  height: number
}

/**
 * Point at `at` the way a person does: from a little way off, then onto it.
 * Recharts reacts to movement INTO a mark, not to a pointer that is simply
 * placed there.
 */
export async function pointAt(page: Page, at: { x: number; y: number }) {
  await page.mouse.move(at.x - 12, at.y - 12)
  await page.mouse.move(at.x, at.y, { steps: 3 })
}

/**
 * Take the pointer off every chart and let any tooltip go: it LINGERS for
 * 300 ms after its chart lets go (`TIP_LINGER_MS`), so it can be reached.
 */
export async function leaveCharts(page: Page) {
  await page.mouse.move(1, 1)
  await expect(page.locator('[data-chart-tooltip]')).toHaveCount(0)
}

/** Whitespace-normalised visible text. */
export const squash = (text: string | null | undefined) => (text ?? '').replace(/\s+/g, ' ').trim()

/** Two boxes overlap (by more than a hair: anti-aliasing puts edges on shared pixels). */
export function intersects(a: DOMRectLike, b: DOMRectLike, slack = 0.5): boolean {
  return (
    a.x + slack < b.x + b.width &&
    b.x + slack < a.x + a.width &&
    a.y + slack < b.y + b.height &&
    b.y + slack < a.y + a.height
  )
}

/**
 * Walk a chart's keyboard cursor from the first point until the readout
 * satisfies `found`, and return the readout's text. Fails if no point does.
 */
export async function keyboardTo(
  page: Page,
  item: Locator,
  found: (readout: string) => boolean,
  maxSteps = 60,
): Promise<string> {
  const surface = item.locator('[data-chart-cursor]').first()
  await surface.focus()
  await page.keyboard.press('Home')
  const readout = item.locator('[data-chart-readout]')
  for (let step = 0; step <= maxSteps; step++) {
    await expect(readout).toBeVisible()
    const text = squash(await readout.innerText())
    if (found(text)) return text
    await page.keyboard.press('ArrowRight')
  }
  throw new Error('the keyboard cursor never reached the point asked for')
}

export interface ExportedFile {
  name: string
  bytes: Buffer
  /** From the click on the format to the browser's download event, ms. */
  ms: number
}

/** Open the item's Export menu, choose `format`, and read the file the browser saves. */
export async function exportFile(page: Page, item: Locator, format: string): Promise<ExportedFile> {
  await item.getByRole('button', { name: EXPORT.trigger, exact: true }).click()
  const choice = item.getByRole('menuitem', { name: format, exact: true })
  await expect(choice).toBeVisible()
  const download = page.waitForEvent('download')
  const started = Date.now()
  await choice.click()
  const file = await download
  const ms = Date.now() - started
  const path = await file.path()
  return { name: file.suggestedFilename(), bytes: readFileSync(path), ms }
}

/** RFC 4180 records (quoted fields, doubled quotes, CRLF), after an optional UTF-8 BOM. */
export function parseCsv(text: string): string[][] {
  const body = text.charCodeAt(0) === 0xfeff ? text.slice(1) : text
  const rows: string[][] = []
  let row: string[] = []
  let cell = ''
  let quoted = false
  for (let i = 0; i < body.length; i++) {
    const c = body[i]
    if (quoted) {
      if (c === '"' && body[i + 1] === '"') {
        cell += '"'
        i++
      } else if (c === '"') quoted = false
      else cell += c
    } else if (c === '"') quoted = true
    else if (c === ',') {
      row.push(cell)
      cell = ''
    } else if (c === '\n' || c === '\r') {
      if (c === '\r' && body[i + 1] === '\n') i++
      row.push(cell)
      rows.push(row)
      row = []
      cell = ''
    } else cell += c
  }
  if (cell !== '' || row.length) {
    row.push(cell)
    rows.push(row)
  }
  return rows
}

export interface ParsedSvg {
  ok: boolean
  /** The text of every `<text>` element, in document order. */
  texts: string[]
  /** `data-export-role` of each `<text>` that carries one. */
  roles: string[]
  /** Element names that should never be in an exported chart. */
  foreign: string[]
  /** Attributes whose name starts with "on". */
  handlers: string[]
  width: number
  height: number
}

/** Parse an exported SVG in the page's own XML parser — the one a browser opening the file would use. */
export async function parseSvg(page: Page, svg: string): Promise<ParsedSvg> {
  return page.evaluate((source) => {
    const doc = new DOMParser().parseFromString(source, 'image/svg+xml')
    const root = doc.documentElement
    const ok = root.nodeName === 'svg' && doc.getElementsByTagName('parsererror').length === 0
    const all = Array.from(doc.getElementsByTagName('*'))
    return {
      ok,
      texts: Array.from(doc.getElementsByTagName('text'), (node) => node.textContent ?? ''),
      roles: Array.from(doc.querySelectorAll('text[data-export-role]'), (node) => node.getAttribute('data-export-role') ?? ''),
      foreign: all.filter((node) => ['img', 'script', 'iframe', 'foreignObject'].includes(node.localName)).map((node) => node.localName),
      handlers: all.flatMap((node) => Array.from(node.attributes, (attr) => attr.name).filter((name) => /^on/i.test(name))),
      width: Number.parseFloat(root.getAttribute('width') ?? 'NaN'),
      height: Number.parseFloat(root.getAttribute('height') ?? 'NaN'),
    }
  }, svg)
}
