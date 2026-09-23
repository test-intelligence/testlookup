/**
 * Wave 2.4 review A5 — a chart in full screen is readable across a room AND
 * nothing of it is cut off.
 *
 * The first full-screen build enlarged the SVG text alone, from CSS, inside a
 * drawing laid out for 11 px: ranked-bar names lost 17-30 px off the svg's
 * left edge, release, bucket and axis labels were cut. Now the drawing is
 * laid out at page text size and scaled up as a whole (`ChartResponsive`).
 * So, for every Recharts chart kind, in full screen:
 *
 *   - no `<text>` of the chart extends outside its `<svg>` (the same check
 *     holds on the page, where it caught a histogram axis title cut by 4 px);
 *   - the axis ticks read at 15 px (11 px on the page), and no text of the
 *     chart at under 13.5 px;
 *   - the pointer tooltip still sits BESIDE the mark it names, in the scaled
 *     drawing (its placement is measured on screen and converted back).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { CHART_SVG, galleryItem, intersects, leaveCharts, markCentres, openGallery, pointAt, type DOMRectLike } from '../lib/chart-gallery-page'

/** Every item kind drawn by Recharts, with a selector for its marks. */
const ITEMS: [string, string][] = [
  ['timeseries-trend-releases', '.recharts-bar-rectangle path'],
  ['bar-long-names', '.recharts-bar-rectangle path'],
  ['bar-ranked', '.recharts-bar-rectangle path'],
  ['bar-stacked', '.recharts-bar-rectangle path'],
  ['duration-histogram', '.recharts-bar-rectangle path'],
  ['duration-band', '.recharts-line-curve'],
  ['multi-series-three-suites', '.recharts-line-curve'],
  ['donut-status', '.recharts-pie-sector path'],
]

interface TextReport {
  /** Text that reaches outside the svg, with how far on each side (negative = outside). */
  cut: string[]
  /** The smallest on-screen font size of any of the chart's text, px. */
  smallest: number
  /** The smallest on-screen font size of an axis tick label, px (`null`: the chart has no axis). */
  smallestTick: number | null
  texts: number
}

/** Every `<text>` of the item's chart against its svg's box. */
function readTexts(item: Locator): Promise<TextReport> {
  return item.locator(CHART_SVG).first().evaluate((svg) => {
    const box = svg.getBoundingClientRect()
    const layoutWidth = (svg as SVGSVGElement).width.baseVal.value || box.width
    const scale = box.width / layoutWidth
    const cut: string[] = []
    let smallest = Infinity
    let smallestTick: number | null = null
    let texts = 0
    for (const text of Array.from(svg.querySelectorAll('text'))) {
      const r = text.getBoundingClientRect()
      if (r.width === 0 || !text.textContent?.trim()) continue
      texts += 1
      const size = parseFloat(getComputedStyle(text).fontSize) * scale
      smallest = Math.min(smallest, size)
      if (text.closest('.recharts-cartesian-axis-tick')) smallestTick = Math.min(smallestTick ?? Infinity, size)
      const sides = { left: r.left - box.left, right: box.right - r.right, top: r.top - box.top, bottom: box.bottom - r.bottom }
      if (Object.values(sides).some((gap) => gap < -0.5)) {
        cut.push(`"${text.textContent.slice(0, 32)}" ${JSON.stringify(Object.fromEntries(Object.entries(sides).map(([k, v]) => [k, Math.round(v)])))}`)
      }
    }
    return { cut, smallest, smallestTick, texts }
  })
}

async function enterFullscreen(page: Page, item: Locator) {
  await item.scrollIntoViewIfNeeded()
  await item.getByRole('button', { name: 'Full screen', exact: true }).click()
  await expect(item.locator('[data-chart-fullscreen]')).toHaveCount(1)
  // The body is measured, then the chart re-lays out at that size: wait for
  // the drawing's size to settle.
  const svg = item.locator(CHART_SVG).first()
  await page.waitForTimeout(200)
  let last = -1
  await expect
    .poll(async () => {
      const height = (await svg.boundingBox())?.height ?? 0
      const settled = Math.abs(height - last) < 0.5
      last = height
      return settled
    })
    .toBe(true)
  await page.waitForTimeout(100)
}

async function exitFullscreen(item: Locator) {
  await item.getByRole('button', { name: 'Exit full screen' }).click()
  await expect(item.locator('[data-chart-fullscreen]')).toHaveCount(0)
}

for (const viewport of [
  { width: 1280, height: 800 },
  { width: 640, height: 480 },
]) {
  test.describe(`full-screen chart text at ${viewport.width}×${viewport.height} (review A5)`, () => {
    test.use({ viewport })

    test('on the page: no chart text is cut off by its svg', async ({ page }) => {
      await openGallery(page)
      for (const [id] of ITEMS) {
        const item = galleryItem(page, id)
        await item.scrollIntoViewIfNeeded()
        const report = await readTexts(item)
        expect(report.texts, `${id}: no text drawn`).toBeGreaterThan(0)
        expect(report.cut, `${id}: text outside the svg on the page`).toEqual([])
      }
    })

    test('in full screen: no chart text is cut off, and it reads across a room', async ({ page }) => {
      test.setTimeout(90_000)
      await openGallery(page)
      for (const [id] of ITEMS) {
        const item = galleryItem(page, id)
        await enterFullscreen(page, item)
        const report = await readTexts(item)
        expect(report.texts, `${id}: no text drawn`).toBeGreaterThan(0)
        expect(report.cut, `${id}: text outside the svg in full screen`).toEqual([])
        // Axis ticks are 11 px on the page: 15 px here. The smallest text of all
        // (a release name over the plot, 10 px on the page) reads at 13.6 px.
        if (report.smallestTick !== null) expect(report.smallestTick, `${id}: the smallest axis tick in full screen`).toBeGreaterThanOrEqual(14.5)
        expect(report.smallest, `${id}: the smallest chart text in full screen`).toBeGreaterThanOrEqual(13.5)
        await exitFullscreen(item)
      }
    })

    test('in full screen: the pointer tooltip sits beside its mark in the scaled drawing', async ({ page }) => {
      test.setTimeout(90_000)
      await openGallery(page)
      for (const [id, marks] of ITEMS.filter(([id]) => id !== 'duration-band' && id !== 'multi-series-three-suites')) {
        const item = galleryItem(page, id)
        await enterFullscreen(page, item)
        const [first] = await markCentres(item.locator(marks))
        expect(first, `${id}: no mark`).toBeTruthy()
        await leaveCharts(page)
        await pointAt(page, first)
        const tooltip = item.locator('[data-chart-tooltip]')
        await expect(tooltip, id).toBeVisible()
        await expect(tooltip).toHaveAttribute('data-tip-side', /.+/)
        const tip = (await tooltip.boundingBox()) as DOMRectLike
        expect(intersects(tip, first.box), `${id}: the tooltip ${JSON.stringify(tip)} covers its mark ${JSON.stringify(first.box)}`).toBe(false)
        const frame = (await item.locator('[data-chart-frame]').boundingBox()) as DOMRectLike
        expect(tip.x, `${id}: tooltip outside the frame`).toBeGreaterThanOrEqual(frame.x - 0.5)
        expect(tip.x + tip.width, `${id}: tooltip outside the frame`).toBeLessThanOrEqual(frame.x + frame.width + 0.5)
        await page.mouse.move(1, 1)
        await exitFullscreen(item)
      }
    })
  })
}
