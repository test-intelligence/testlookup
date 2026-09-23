/**
 * VIZ-601 — one tooltip, reachable three ways, placed where it can be read.
 *
 * The story's tests, in a real browser (jsdom cannot lay anything out):
 *
 *   - CONTENT EQUALITY: the pointer's tooltip and the keyboard's readout of the
 *     same point say exactly the same thing — for the donut, a bar chart and a
 *     time series (and the comparison, which shares one tooltip per day);
 *   - VIEWPORT COLLISION: at a narrow viewport, on a mark at the right-hand
 *     edge, the tooltip box is inside the viewport and does not cover the mark;
 *   - ESCAPE dismisses it, and it is HOVERABLE (WCAG 1.4.13): the pointer can
 *     move onto it and it stays, saying the same thing;
 *   - it never gets in the way of READING the chart (Wave 2.4 review A2/F3):
 *     a pointer sweeping across the days, or down the bars, meets every one
 *     of them in turn — a tooltip holds the pointer only when the pointer
 *     came onto it from its own mark — and at 320 px, where no tooltip fits
 *     beside its mark, none is drawn over one: it goes below the plot.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import {
  CHART_SVG,
  galleryItem,
  intersects,
  keyboardTo,
  leaveCharts,
  markCentres,
  openGallery,
  pointAt,
  squash,
  watchErrors,
  type DOMRectLike,
} from '../lib/chart-gallery-page'

/** The tooltip's title line — the dimension (a category, a day) it describes. */
async function tipTitle(tooltip: Locator): Promise<string> {
  return squash(await tooltip.locator(':scope > div').first().innerText())
}

interface Hovered {
  text: string
  title: string
  tip: DOMRectLike
  mark: DOMRectLike
  /** Where the pointer is: on the mark. */
  at: { x: number; y: number }
}

/** Point at the `index`-th drawn mark (negative counts from the end) and read the tooltip it opens. */
async function hoverMark(page: Page, item: Locator, marks: string, index: number): Promise<Hovered> {
  const centres = await markCentres(item.locator(marks))
  expect(centres.length, `no drawn marks under ${marks}`).toBeGreaterThan(0)
  const at = centres[index < 0 ? centres.length + index : index]
  await leaveCharts(page)
  await pointAt(page, at)
  const tooltip = item.locator('[data-chart-tooltip]')
  await expect(tooltip).toBeVisible()
  // Placed (it is hidden until it has measured itself), and settled.
  await expect(tooltip).toHaveAttribute('data-tip-side', /.+/)
  const tip = (await tooltip.boundingBox()) as DOMRectLike
  return { text: squash(await tooltip.innerText()), title: await tipTitle(tooltip), tip, mark: at.box, at: { x: at.x, y: at.y } }
}

/** How many points the chart has: its cursor surface's name says so ("…, 10 days. Use the arrow keys…"). */
async function pointCount(item: Locator): Promise<number> {
  const name = (await item.locator('[data-chart-cursor]').first().getAttribute('aria-label')) ?? ''
  const count = /, (\d+) [a-z]+\. Use the arrow keys/.exec(name)
  expect(count, `no point count in "${name}"`).not.toBeNull()
  return Number(count?.[1])
}

/** The title of whatever tooltip the item shows now (beside its mark or below the plot), or null. */
async function shownTitle(item: Locator): Promise<string | null> {
  return item.evaluate((el) => {
    const tip = el.querySelector('[data-chart-tooltip]')
    return tip ? (tip.querySelector('.chart-tooltip-title')?.textContent ?? '') : null
  })
}

/**
 * Sweep the pointer in a straight line across the plot, a few pixels a move
 * as a hand does, and list the tooltip titles in the order they were shown
 * (each once per run of moves that showed it).
 */
async function sweep(page: Page, item: Locator, from: { x: number; y: number }, to: { x: number; y: number }, step = 6) {
  await leaveCharts(page)
  const length = Math.hypot(to.x - from.x, to.y - from.y)
  const moves = Math.max(1, Math.ceil(length / step))
  const seen: string[] = []
  await page.mouse.move(from.x, from.y)
  for (let i = 0; i <= moves; i++) {
    await page.mouse.move(from.x + ((to.x - from.x) * i) / moves, from.y + ((to.y - from.y) * i) / moves)
    const title = await shownTitle(item)
    if (title !== null && seen[seen.length - 1] !== title) seen.push(title)
  }
  return seen
}

/** The plot area (the cartesian grid's box) in viewport coordinates. */
async function plotBox(item: Locator): Promise<DOMRectLike> {
  const box = await item.locator('.recharts-cartesian-grid').first().boundingBox()
  expect(box, 'no plot drawn').not.toBeNull()
  return box as DOMRectLike
}

/** The pointer's content and the keyboard's, for the same point. */
async function pointerAndKeyboard(page: Page, id: string, marks: string, index: number) {
  const item = galleryItem(page, id)
  await item.scrollIntoViewIfNeeded()
  const hovered = await hoverMark(page, item, marks, index)
  await page.mouse.move(1, 1)
  const readout = await keyboardTo(page, item, (text) => text.startsWith(hovered.title))
  await page.keyboard.press('Escape')
  return { pointer: hovered.text, title: hovered.title, keyboard: readout }
}

test.describe('VIZ-601 tooltip', () => {
  test('content equality: the pointer and the keyboard read the SAME content for the same point', async ({ page }) => {
    const errors = watchErrors(page)
    await openGallery(page)
    const cases: [string, string, number][] = [
      // The donut: its second slice (failed), so equality is not an accident of the first point.
      ['donut-status', '.recharts-pie-sector path', 1],
      // A ranked bar and a stacked one.
      ['bar-ranked', '.recharts-bar-rectangle path', 2],
      ['bar-stacked', '.recharts-bar-rectangle path', 0],
      // A time series: a release day, so the release row is compared too.
      ['timeseries-trend-releases', '.recharts-bar-rectangle path', 2],
      // The comparison's shared tooltip: every series for the day.
      ['multi-series-three-suites', '.recharts-cartesian-grid-horizontal line', 1],
    ]
    for (const [id, marks, index] of cases) {
      const { pointer, title, keyboard } = await pointerAndKeyboard(page, id, marks, index)
      // Not vacuous: rows after the title, with at least one number in them.
      expect(pointer.length, id).toBeGreaterThan(title.length)
      expect(pointer, id).toMatch(/\d/)
      expect(keyboard, `${id}: keyboard readout differs from the pointer tooltip`).toBe(pointer)
    }
    expect(errors).toEqual([])
  })

  test('viewport collision: at a narrow viewport, a right-edge mark keeps its tooltip on screen and off the mark', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 420, height: 760 })
    await openGallery(page)
    const cases: [string, string, number][] = [
      // The longest ranked bar reaches the plot's right edge.
      ['bar-ranked', '.recharts-bar-rectangle path', 0],
      // The last day of a time series is the right-most column.
      ['timeseries-trend-releases', '.recharts-bar-rectangle path', -1],
      // A slice on the donut's right-hand side (the first, clockwise from 12 o'clock).
      ['donut-status', '.recharts-pie-sector path', 0],
    ]
    for (const [id, marks, index] of cases) {
      const item = galleryItem(page, id)
      await item.scrollIntoViewIfNeeded()
      // The canvas is 640 px in a 420 px window: bring the mark itself into view.
      const all = item.locator(marks)
      const count = await all.count()
      await all.nth(index < 0 ? count + index : index).scrollIntoViewIfNeeded()
      const hovered = await hoverMark(page, item, marks, index)
      const viewport = page.viewportSize() as { width: number; height: number }
      const placement = await item.locator('[data-chart-tooltip]').getAttribute('data-tip-placement')
      const where = `${id}: ${placement} tooltip ${JSON.stringify(hovered.tip)}, mark ${JSON.stringify(hovered.mark)}`
      expect(hovered.tip.x, where).toBeGreaterThanOrEqual(-0.5)
      expect(hovered.tip.x + hovered.tip.width, where).toBeLessThanOrEqual(viewport.width + 0.5)
      if (placement === 'slot') {
        // Beside the mark on no side: in the flow BELOW the plot, where the
        // page scrolls to it like any other content.
        const svg = (await item.locator(CHART_SVG).boundingBox()) as DOMRectLike
        expect(hovered.tip.y, where).toBeGreaterThanOrEqual(svg.y + svg.height - 0.5)
      } else {
        expect(placement, where).toBe('beside')
        expect(hovered.tip.y, where).toBeGreaterThanOrEqual(-0.5)
        expect(hovered.tip.y + hovered.tip.height, where).toBeLessThanOrEqual(viewport.height + 0.5)
      }
      expect(intersects(hovered.tip, hovered.mark), `${where}: the tooltip covers its mark`).toBe(false)
      await page.keyboard.press('Escape')
    }
  })

  test('a sideways sweep across the days meets every day in turn — no tooltip holds it back', async ({ page }) => {
    test.setTimeout(120_000)
    await page.setViewportSize({ width: 1280, height: 800 })
    await openGallery(page)
    for (const id of ['timeseries-hostile-release', 'timeseries-trend-releases', 'multi-series-three-suites', 'duration-histogram']) {
      const item = galleryItem(page, id)
      await item.scrollIntoViewIfNeeded()
      const days = await pointCount(item)
      const plot = await plotBox(item)
      // Heights through the tooltip's own (it starts at the plot's top) and
      // through the bars: the sweep must not stick at any of them.
      for (const at of [0.25, 0.5, 0.75]) {
        const y = plot.y + plot.height * at
        const seen = await sweep(page, item, { x: plot.x + 1, y }, { x: plot.x + plot.width - 1, y })
        expect(seen.length, `${id} at ${at * 100}% of the plot: ${seen.join(' → ')}`).toBe(days)
        expect(new Set(seen).size, `${id}: a day came back`).toBe(days)
      }
      // And back again, right to left.
      const y = plot.y + plot.height * 0.5
      const back = await sweep(page, item, { x: plot.x + plot.width - 1, y }, { x: plot.x + 1, y })
      expect(back.length, `${id} right to left: ${back.join(' → ')}`).toBe(days)
    }
  })

  test('a sweep down the bars meets every bar in turn — no tooltip holds it back', async ({ page }) => {
    test.setTimeout(120_000)
    await page.setViewportSize({ width: 1280, height: 800 })
    await openGallery(page)
    for (const id of ['bar-ranked', 'bar-stacked', 'bar-long-names']) {
      const item = galleryItem(page, id)
      await item.scrollIntoViewIfNeeded()
      const bars = await pointCount(item)
      const plot = await plotBox(item)
      // On the bars, at their ends, and through the empty space right of them.
      for (const at of [0.1, 0.4, 0.7, 0.95]) {
        const x = plot.x + plot.width * at
        const seen = await sweep(page, item, { x, y: plot.y + 1 }, { x, y: plot.y + plot.height - 1 })
        expect(seen.length, `${id} at ${at * 100}% across: ${seen.join(' → ')}`).toBe(bars)
        expect(new Set(seen).size, `${id}: a bar came back`).toBe(bars)
      }
    }
  })

  test('at 320 px no tooltip is drawn over its mark: one that fits nowhere beside it goes below the plot', async ({ page }) => {
    test.setTimeout(90_000)
    await page.setViewportSize({ width: 320, height: 720 })
    await openGallery(page, '?canvas=fluid')
    // How the pointer leaves the plot without passing over another mark: a
    // day's column straight down; a bar's row, or a slice, out to its side.
    const cases: [string, string, 'down' | 'side'][] = [
      ['timeseries-trend-releases', '.recharts-bar-rectangle path', 'down'],
      ['timeseries-hostile-release', '.recharts-bar-rectangle path', 'down'],
      ['bar-ranked', '.recharts-bar-rectangle path', 'side'],
      ['bar-stacked', '.recharts-bar-rectangle path', 'side'],
      ['duration-histogram', '.recharts-bar-rectangle path', 'down'],
      ['donut-status', '.recharts-pie-sector path', 'side'],
    ]
    for (const [id, marks, exit] of cases) {
      const item = galleryItem(page, id)
      await item.scrollIntoViewIfNeeded()
      const count = await item.locator(marks).count()
      // The first, a middle and the last mark.
      for (const index of [...new Set([0, Math.floor(count / 2), count - 1])]) {
        await item.locator(marks).nth(index).scrollIntoViewIfNeeded()
        const hovered = await hoverMark(page, item, marks, index)
        const tooltip = item.locator('[data-chart-tooltip]')
        const placement = await tooltip.getAttribute('data-tip-placement')
        const where = `${id} #${index}: ${placement} tooltip ${JSON.stringify(hovered.tip)}, mark ${JSON.stringify(hovered.mark)}`
        expect(intersects(hovered.tip, hovered.mark), `${where}: the tooltip covers its mark`).toBe(false)
        // Over the plot only where it FITS beside the mark.
        if (placement !== 'slot') await expect(tooltip, where).toHaveAttribute('data-tip-fits', 'true')
        expect(hovered.tip.x, where).toBeGreaterThanOrEqual(-0.5)
        expect(hovered.tip.x + hovered.tip.width, where).toBeLessThanOrEqual(320.5)
        // Reachable, and the same content when reached: the pointer leaves
        // the plot, crosses to the tooltip, and it stays (SC 1.4.13).
        const target = { x: hovered.tip.x + Math.min(hovered.tip.width / 2, 40), y: hovered.tip.y + Math.min(12, hovered.tip.height / 2) }
        if (placement === 'slot') {
          const svg = (await item.locator(CHART_SVG).boundingBox()) as DOMRectLike
          const start = hovered.at
          const right = start.x >= svg.x + svg.width / 2
          const out =
            exit === 'down'
              ? { x: start.x, y: svg.y + svg.height - 2 }
              : { x: right ? Math.min(svg.x + svg.width - 2, 318) : Math.max(svg.x + 2, 2), y: start.y }
          await page.mouse.move(out.x, out.y, { steps: 6 })
          await page.mouse.move(out.x, target.y, { steps: 6 })
        }
        await page.mouse.move(target.x, target.y, { steps: 8 })
        await page.waitForTimeout(400)
        await expect(tooltip, where).toBeVisible()
        expect(squash(await tooltip.innerText()), `${where}: it changed on the way onto it`).toBe(hovered.text)
      }
    }
  })

  test('Escape dismisses a pointer tooltip; pointing again brings it back', async ({ page }) => {
    await openGallery(page)
    for (const [id, marks] of [
      ['donut-status', '.recharts-pie-sector path'],
      ['timeseries-zoomed-axis', '.recharts-bar-rectangle path'],
    ] as const) {
      const item = galleryItem(page, id)
      await item.scrollIntoViewIfNeeded()
      await hoverMark(page, item, marks, 0)
      await page.keyboard.press('Escape')
      await expect(item.locator('[data-chart-tooltip]')).toHaveCount(0)
      await page.mouse.move(1, 1)
      await hoverMark(page, item, marks, 0)
      await expect(item.locator('[data-chart-tooltip]')).toBeVisible()
      await page.keyboard.press('Escape')
    }
  })

  test('hoverable (SC 1.4.13): the pointer moves onto the tooltip and it stays, unchanged', async ({ page }) => {
    await openGallery(page)
    for (const [id, marks, index] of [
      ['bar-stacked', '.recharts-bar-rectangle path', 0],
      ['timeseries-trend-releases', '.recharts-bar-rectangle path', 1],
      ['donut-status', '.recharts-pie-sector path', 0],
    ] as const) {
      const item = galleryItem(page, id)
      await item.scrollIntoViewIfNeeded()
      const hovered = await hoverMark(page, item, marks, index)
      const tooltip = item.locator('[data-chart-tooltip]')
      // Walk from the mark's centre to the tooltip's, as a hand does.
      const from = { x: hovered.mark.x + hovered.mark.width / 2, y: hovered.mark.y + hovered.mark.height / 2 }
      const to = { x: hovered.tip.x + hovered.tip.width / 2, y: hovered.tip.y + hovered.tip.height / 2 }
      await page.mouse.move(from.x, from.y)
      await page.mouse.move(to.x, to.y, { steps: 12 })
      await expect(tooltip, `${id}: the tooltip vanished on the way onto it`).toBeVisible()
      // Held while the pointer rests on it — past the 300 ms linger.
      await page.waitForTimeout(600)
      await expect(tooltip).toBeVisible()
      expect(squash(await tooltip.innerText()), `${id}: the tooltip changed under the pointer`).toBe(hovered.text)
      const box = (await tooltip.boundingBox()) as DOMRectLike
      expect(to.x).toBeGreaterThanOrEqual(box.x)
      expect(to.x).toBeLessThanOrEqual(box.x + box.width)
      await page.mouse.move(1, 1)
      await page.keyboard.press('Escape')
    }
  })

  test('every Recharts tooltip in the gallery lives inside its chart, never portalled to <body>', async ({ page }) => {
    await openGallery(page)
    const item = galleryItem(page, 'bar-ranked')
    await item.scrollIntoViewIfNeeded()
    await hoverMark(page, item, '.recharts-bar-rectangle path', 0)
    // Inside the chart's own wrapper — which is what keeps it visible in full screen.
    await expect(item.locator(`.recharts-wrapper [data-chart-tooltip]`)).toHaveCount(1)
    await expect(page.locator('body > [data-chart-tooltip]')).toHaveCount(0)
    await expect(item.locator(CHART_SVG)).toBeVisible()
  })
})
