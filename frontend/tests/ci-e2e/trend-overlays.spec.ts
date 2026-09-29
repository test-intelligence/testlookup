/**
 * VIZ-405 — the trend overlays, driven in a real browser from the chart
 * gallery (`/__charts`): `timeseries-trend-analysis` (27 days with runs, both
 * overlays on, one flagged day) and `timeseries-trend-insufficient` (6 days
 * with runs, under the 7 the overlays need).
 *
 * What only a browser can show: the overlays are really PAINTED dashed (not
 * just handed to Recharts) on a card-coloured halo that keeps them readable
 * over the bars, the anomaly triangle is really under the pointer and its
 * tooltip states the rule, an explanation really survives the pointer's trip
 * onto it and really closes on Escape however it was opened, it really stays
 * inside the chart (flipped above when there is no room below, inside the
 * scroller at 320 px), the pressed state is really visible in every theme,
 * the keyboard cursor really reads the rule, and the frames really fit the
 * boxes their baselines are taken in.
 *
 * The user-facing sentences are spelt out here rather than imported: they are
 * the specification, and `trendStats` value-imports `@/…`, which Playwright's
 * plain-Node transform does not resolve.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { GALLERY_ITEMS, galleryCanvasSize } from '../../src/pages/dev/chartGalleryFixtures'
import { ALL_THEMES, expectNoBlockingViolations } from '../lib/axe-gate'
import { textEscapes } from '../lib/chart-text-escapes'

const GALLERY = '/__charts'
const ANALYSIS_ID = 'timeseries-trend-analysis'
const SPARSE_ID = 'timeseries-trend-insufficient'
const ANALYSIS = `[data-gallery-item="${ANALYSIS_ID}"]`
const SPARSE = `[data-gallery-item="${SPARSE_ID}"]`
const CHART_SVG = '.recharts-wrapper > svg.recharts-surface'
const FLAGGED_DAY = '2026-03-23'
const RULE =
  'Rule: more than 3 MADs below the median of the same weekday in the previous 4 weeks, and below each of them; days under 50 executions are not judged.'
const FLAGGED_DETAIL = '79.0% is 15.1 MADs below 94.1%, the median of the 3 previous Mondays'
const MOVING_AVERAGE = /7-day moving average/
const TREND_LINE = /Trend line/

/**
 * The UNZOOMED gallery items that turn the overlays on — asserted to be exactly
 * the two above. The VIZ-407 item that opens zoomed with the overlays on
 * (`timeseries-zoom-trend`) is a different subject — a slice of a longer window
 * — and `chart-zoom.spec.ts` owns it; its marks and its box are checked with
 * every other item by `chart-gallery.spec.ts`.
 */
const TREND_ITEMS = GALLERY_ITEMS.filter(
  (item) => item.chart === 'time-series' && item.trendOverlays && item.zoom === undefined,
)

/** Console errors and uncaught exceptions, collected from before navigation. */
function watchErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(`console.error: ${message.text()}`)
  })
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`))
  return errors
}

async function openGallery(page: Page, search = '') {
  await page.goto(`${GALLERY}${search}`)
  // Fail, never skip: a 404 or an auth bounce lands on /overview → /login.
  expect(new URL(page.url()).pathname, 'the gallery route redirected').toBe(GALLERY)
  await expect(page.getByTestId('chart-gallery')).toBeVisible()
  // Both charts have measured and drawn: Recharts paints after ResponsiveContainer's first layout.
  await expect(page.locator(`${ANALYSIS} .trend-overlay-trend-line path.recharts-curve`).first()).toBeVisible()
  await expect(page.locator(`${SPARSE} ${CHART_SVG} path`).first()).toBeVisible()
}

/** Drawn marks inside an item's svg: path/rect/circle with a real box, outside <defs>. */
async function drawnMarks(page: Page, itemId: string): Promise<number> {
  return page.locator(`[data-gallery-item="${itemId}"] ${CHART_SVG}`).first().evaluate((svg) =>
    Array.from(svg.querySelectorAll('path, rect, circle')).filter((mark) => {
      if (mark.closest('defs')) return false
      const box = mark.getBoundingClientRect()
      return box.width > 0 && box.height > 0
    }).length,
  )
}

/**
 * No text escapes the frame, its svg or its own box. The rotated "Pass rate %"
 * axis title used to overhang the svg by 3.1 px and was tolerated here by
 * name; fix round B insets it (`offset: 14`), so nothing is tolerated now.
 */
async function expectNoTextEscapes(frame: Locator, label: string) {
  expect(await frame.evaluate(textEscapes), label).toEqual([])
}

/**
 * The flagged day's centre on screen, from the triangle Recharts drew there.
 * Two moves: Recharts reacts to movement INTO the plot, not to a pointer that
 * is simply placed there.
 */
async function pointAtFlaggedDay(page: Page) {
  const marker = page.locator(`${ANALYSIS} [data-trend-anomaly="${FLAGGED_DAY}"]`)
  await marker.scrollIntoViewIfNeeded()
  const box = await marker.boundingBox()
  if (!box) throw new Error('the anomaly marker has no box')
  const at = { x: box.x + box.width / 2, y: box.y + box.height / 2 }
  await page.mouse.move(at.x - 20, at.y + 20)
  await page.mouse.move(at.x, at.y)
}

/** The explanation a control is described by (see `Explained`: its next sibling). */
const explanationOf = (control: Locator) => control.locator('xpath=following-sibling::*[@role="tooltip"]')

/** WCAG contrast of two computed `rgb()` colours. */
const contrastOf = (a: string, b: string) => {
  const lum = (rgb: string) => {
    const [r, g, bl] = (rgb.match(/[\d.]+/g) ?? []).slice(0, 3).map((v) => {
      const s = Number(v) / 255
      return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
    })
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl
  }
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x)
  return Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100
}

/** A box, rounded to the pixel, for messages. */
type Rect = { left: number; top: number; right: number; bottom: number }
const rectOf = (locator: Locator): Promise<Rect> =>
  locator.evaluate((el) => {
    const r = el.getBoundingClientRect()
    return { left: r.left, top: r.top, right: r.right, bottom: r.bottom }
  })
const inside = (inner: Rect, outer: Rect) =>
  inner.left >= outer.left - 0.5 && inner.right <= outer.right + 0.5 && inner.top >= outer.top - 0.5 && inner.bottom <= outer.bottom + 0.5

test.describe('trend overlays (VIZ-405)', () => {
  test('both items are in the gallery, draw their marks, and fit their measured boxes', async ({ page }) => {
    const errors = watchErrors(page)
    expect(TREND_ITEMS.map((item) => item.id)).toEqual([ANALYSIS_ID, SPARSE_ID])
    await openGallery(page)
    for (const item of TREND_ITEMS) {
      const section = page.locator(`[data-gallery-item="${item.id}"]`)
      await expect(section.getByRole('heading', { level: 2, name: item.title })).toBeVisible()
      await expect
        .poll(() => drawnMarks(page, item.id), { message: `${item.id}: expected at least ${item.minMarks} drawn marks` })
        .toBeGreaterThanOrEqual(item.minMarks)

      // The frame fits the box its baseline is taken in: a frame that spills
      // over the next item corrupts that item's screenshot.
      const canvas = page.locator(`[data-gallery-canvas="${item.id}"]`)
      const canvasBox = await canvas.boundingBox()
      const frame = section.locator('[data-chart-frame]')
      const frameBox = await frame.boundingBox()
      if (!canvasBox || !frameBox) throw new Error(`${item.id}: no canvas or frame box`)
      expect(Math.round(canvasBox.height), `${item.id}: canvas height`).toBe(galleryCanvasSize(item).height)
      expect(frameBox.y + frameBox.height, `${item.id}: the frame spills out of its canvas`).toBeLessThanOrEqual(
        canvasBox.y + canvasBox.height + 1,
      )
      expect(frameBox.x + frameBox.width, `${item.id}: the frame is wider than its canvas`).toBeLessThanOrEqual(
        canvasBox.x + canvasBox.width + 1,
      )

      // No text escapes its frame, its svg or its own box.
      await expectNoTextEscapes(frame, item.id)
    }
    expect(errors).toEqual([])
  })

  test('the rotated rate-axis title is whole inside its svg on every time-series item', async ({ page }) => {
    await openGallery(page)
    const overhangs = await page.evaluate(() =>
      Array.from(document.querySelectorAll('[data-gallery-item^="timeseries"]')).map((item) => {
        const title = Array.from(item.querySelectorAll('.recharts-label')).find((label) => label.textContent === 'Pass rate %')
        const owner = title ? (title as SVGElement).ownerSVGElement : null
        if (!title || !owner) return { id: item.getAttribute('data-gallery-item'), overhang: null }
        const svg = owner.getBoundingClientRect()
        const range = document.createRange()
        range.selectNodeContents(title)
        return { id: item.getAttribute('data-gallery-item'), overhang: Math.round((svg.left - range.getBoundingClientRect().left) * 10) / 10 }
      }),
    )
    // Every time-series item, counted from the fixtures (five before Wave 2.4
    // added the zoomed and hostile-release ones), and every one of them found by
    // its id prefix — so a new item cannot slip past this check.
    const timeSeries = GALLERY_ITEMS.filter((item) => item.chart === 'time-series')
    expect(timeSeries.every((item) => item.id.startsWith('timeseries'))).toBe(true)
    expect(overhangs.map((entry) => entry.id), 'time-series items in the gallery').toEqual(timeSeries.map((item) => item.id))
    for (const { id, overhang } of overhangs) {
      expect(overhang, `${id}: no "Pass rate %" title found`).not.toBeNull()
      // Was 3.1 px on all five VIZ-403/405 items.
      expect(overhang as number, `${id}: the title overhangs the svg's left edge`).toBeLessThanOrEqual(0)
    }
  })

  test('draws both overlays dashed, and the takeaway says what the trend is and on what sample', async ({ page }) => {
    await openGallery(page)
    const item = page.locator(ANALYSIS)
    await expect(item.locator('[data-chart-takeaway]')).toHaveText(
      'Pass rate is falling 0.8 pts per week (30 days, 27 days with runs) · Last 7 days 93.2% vs 91.4% the previous 7 (up 1.8 pts; 1,485 vs 1,276 executions)',
    )
    const average = item.getByRole('button', { name: MOVING_AVERAGE })
    const trend = item.getByRole('button', { name: TREND_LINE })
    await expect(average).toHaveAttribute('aria-pressed', 'true')
    await expect(trend).toHaveAttribute('aria-pressed', 'true')
    const averagePath = item.locator('.trend-overlay-moving-average path.recharts-curve')
    const trendPath = item.locator('.trend-overlay-trend-line path.recharts-curve')
    await expect(averagePath.first()).toBeVisible()
    await expect(trendPath.first()).toBeVisible()
    // Told apart from the solid rate line, and from each other, by DASH.
    expect(await averagePath.first().getAttribute('stroke-dasharray')).toBe('8 4')
    expect(await trendPath.first().getAttribute('stroke-dasharray')).toBe('2 4')

    // Turning one off removes its layer AND its halo, and keeps the other; turning it back on restores it.
    await average.click()
    await expect(average).toHaveAttribute('aria-pressed', 'false')
    await expect(item.locator('.trend-overlay-moving-average')).toHaveCount(0)
    await expect(item.locator('.trend-overlay-halo')).toHaveCount(1)
    await expect(trendPath.first()).toBeVisible()
    await average.click()
    await expect(average).toHaveAttribute('aria-pressed', 'true')
    await expect(averagePath.first()).toBeVisible()
    await expect(item.locator('.trend-overlay-halo')).toHaveCount(2)

    // The toggles are real buttons from the keyboard too.
    await trend.focus()
    await page.keyboard.press('Enter')
    await expect(trend).toHaveAttribute('aria-pressed', 'false')
    await expect(item.locator('.trend-overlay-trend-line')).toHaveCount(0)
    await page.keyboard.press('Space')
    await expect(trend).toHaveAttribute('aria-pressed', 'true')
  })

  test('the flagged day is marked with a shape whose tooltip states the rule, and Escape dismisses it', async ({
    page,
  }) => {
    await openGallery(page)
    const item = page.locator(ANALYSIS)
    await expect(item.locator('[data-trend-anomaly]')).toHaveCount(1)
    await pointAtFlaggedDay(page)
    const tooltip = item.locator('[data-chart-tooltip]')
    await expect(tooltip).toBeVisible()
    await expect(tooltip).toContainText(FLAGGED_DAY)
    await expect(tooltip.locator('[data-trend-tooltip-row="anomaly"]')).toContainText(FLAGGED_DETAIL)
    await expect(tooltip.locator('[data-trend-tooltip-row="anomaly"]')).toContainText(RULE)
    // A fitted value is marked as one, and never shown past 100 %.
    await expect(tooltip.locator('[data-trend-tooltip-row="trendLine"]')).toContainText(/\d+\.\d% \(fit\)/)

    // SC 1.4.13: dismissible without moving the pointer…
    await page.keyboard.press('Escape')
    await expect(tooltip).toBeHidden()
    // …and moving it again asks for the tooltip back.
    await pointAtFlaggedDay(page)
    await expect(tooltip).toBeVisible()
  })

  test('the keyboard cursor reads the flagged day and its rule, through the ONE announcer, and shows all of it', async ({
    page,
  }) => {
    await openGallery(page)
    const item = page.locator(ANALYSIS)
    const surface = item.locator('[data-time-series-plot]')
    await expect(surface).toHaveAttribute('role', 'group')
    await expect(surface).toHaveAttribute('aria-label', /Use the arrow keys/)
    await surface.focus()
    await page.keyboard.press('Home')
    // 2026-03-01 is day 0 of the 30 on the axis (the run-free days included).
    for (let i = 0; i < 22; i++) await page.keyboard.press('ArrowRight')
    const announcer = page.locator('[data-chart-announcer="assertive"]')
    await expect(announcer).toContainText(FLAGGED_DAY)
    await expect(announcer).toContainText(`Flagged as unusual: ${FLAGGED_DETAIL}`)
    await expect(announcer).toContainText(RULE)
    const readout = item.locator('[data-chart-readout]')
    await expect(readout).toBeVisible()
    await expect(readout).toContainText(FLAGGED_DAY)
    await expect(readout).toContainText(RULE)
    // The readout WRAPS now (`ChartCursor`): the rule is on screen for the
    // sighted keyboard reader, not cut off after a third of the line.
    expect(await readout.evaluate((el) => el.scrollWidth <= el.clientWidth), 'the readout is cut off').toBe(true)

    // Escape clears the cursor, and focus stays where the reader put it.
    await page.keyboard.press('Escape')
    await expect(readout).toHaveCount(0)
    await expect(surface).toBeFocused()
  })

  test('the overlay toggles explain themselves on focus; the statistics are toggletips', async ({ page }) => {
    await openGallery(page)
    const item = page.locator(ANALYSIS)
    for (const [control, expected] of [
      [item.locator('[data-trend-toggle="movingAverage"]'), ['Window: 7 calendar days', 'Sample: 21 days averaged from 27 days with runs (5,511 executions)']],
      [item.locator('[data-trend-toggle="trendLine"]'), ['least-squares line', 'Slope: -0.8 ± 0.4 pts per week']],
    ] as const) {
      const explanation = explanationOf(control)
      await expect(explanation).toBeHidden()
      await control.focus()
      await expect(explanation).toBeVisible()
      for (const text of expected) await expect(explanation).toContainText(text)
      // The explanation is the control's DESCRIPTION, so a screen reader hears it on focus too.
      expect(((await control.getAttribute('aria-describedby')) ?? '').split(' ')).toContain(await explanation.getAttribute('id'))
      await page.keyboard.press('Escape')
      await expect(explanation).toBeHidden()
      await expect(control).toBeFocused()
    }
    for (const [stat, expected] of [
      ['trend', 'flat if under 0.25 or within its standard error'],
      ['period', 'each week needs at least 100 executions on 3 days with runs'],
      ['anomalies', 'Sample: 13 days judged, 1 flagged'],
    ] as const) {
      const control = item.locator(`[data-trend-stat="${stat}"] button`)
      const explanation = explanationOf(control)
      await control.focus()
      // Described on focus, SHOWN on activation — and Enter / Space toggle it.
      await expect(control).toHaveAttribute('aria-expanded', 'false')
      await expect(explanation).toBeHidden()
      await page.keyboard.press('Enter')
      await expect(control).toHaveAttribute('aria-expanded', 'true')
      await expect(explanation).toContainText(expected)
      await page.keyboard.press('Escape')
      await expect(explanation).toBeHidden()
      await expect(control).toHaveAttribute('aria-expanded', 'false')
      // Reopenable after Escape (it was not), and Space closes it again.
      await page.keyboard.press('Enter')
      await expect(explanation).toBeVisible()
      await page.keyboard.press('Space')
      await expect(explanation).toBeHidden()
      await expect(control).toBeFocused()
    }
  })

  test('SC 1.4.13: an explanation stays open while the pointer travels onto it, and Escape closes one opened by hover', async ({
    page,
  }) => {
    await openGallery(page)
    const item = page.locator(ANALYSIS)
    // A toggle's explanation opens BELOW it; the anomaly statistic's, at the
    // bottom of the frame, opens ABOVE it. Walk the pointer, one pixel at a
    // time, from inside the control across the gap and into the box.
    for (const [selector, side] of [
      ['[data-trend-toggle="movingAverage"]', 'below'],
      ['[data-trend-stat="anomalies"] button', 'above'],
    ] as const) {
      const control = item.locator(selector)
      const explanation = explanationOf(control)
      await control.scrollIntoViewIfNeeded()
      const box = await control.boundingBox()
      if (!box) throw new Error(`${selector}: no box`)
      const x = box.x + 10
      await page.mouse.move(x, box.y + box.height / 2)
      await expect(explanation).toBeVisible()
      await expect(explanation).toHaveAttribute('data-placement', side)
      const tip = await rectOf(explanation.locator('span').first())
      const closedAt: number[] = []
      if (side === 'below') {
        for (let y = Math.floor(box.y + box.height - 2); y <= tip.top + 12; y++) {
          await page.mouse.move(x, y)
          if (!(await explanation.isVisible())) closedAt.push(Math.round(y - (box.y + box.height)))
        }
      } else {
        for (let y = Math.ceil(box.y + 2); y >= tip.bottom - 12; y--) {
          await page.mouse.move(x, y)
          if (!(await explanation.isVisible())) closedAt.push(Math.round(box.y - y))
        }
      }
      // Before: it closed at every pixel of the 4 px gap and never came back.
      expect(closedAt, `${selector}: closed on the way onto it, px past the control`).toEqual([])

      // Escape with focus nowhere near the control (the body) still closes it.
      await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
      await page.keyboard.press('Escape')
      await expect(explanation).toBeHidden()
      // …and pointing at the control again brings it back.
      await page.mouse.move(0, 0)
      await page.mouse.move(x, box.y + box.height / 2)
      await expect(explanation).toBeVisible()
      await page.mouse.move(0, 0)
    }
  })

  test('every explanation stays inside the chart frame; the anomaly one flips above', async ({ page }) => {
    await openGallery(page)
    const item = page.locator(ANALYSIS)
    for (const selector of [
      '[data-trend-toggle="movingAverage"]',
      '[data-trend-toggle="trendLine"]',
      '[data-trend-stat="trend"] button',
      '[data-trend-stat="period"] button',
      '[data-trend-stat="anomalies"] button',
    ]) {
      const control = item.locator(selector)
      await control.focus()
      if (selector.includes('data-trend-stat')) await page.keyboard.press('Enter')
      const explanation = explanationOf(control)
      await expect(explanation).toBeVisible()
      // Both boxes measured now: focusing may have scrolled the page.
      const frame = await rectOf(item.locator('[data-chart-frame]'))
      const box = await rectOf(explanation.locator('span').first())
      // Before: the anomaly explanation ran 132 px past the frame's bottom.
      expect(inside(box, frame), `${selector}: ${JSON.stringify(box)} outside the frame ${JSON.stringify(frame)}`).toBe(true)
      // The statistics sit at the frame's foot: no room below, so above.
      if (selector.includes('anomalies')) await expect(explanation).toHaveAttribute('data-placement', 'above')
      await page.keyboard.press('Escape')
    }
  })

  test('at 320 px the statistics explanations stay inside the scroller that clips them', async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 640 })
    await openGallery(page, '?canvas=fluid')
    const item = page.locator(ANALYSIS)
    for (const stat of ['trend', 'period', 'anomalies']) {
      const control = item.locator(`[data-trend-stat="${stat}"] button`)
      await control.scrollIntoViewIfNeeded()
      await control.focus()
      await page.keyboard.press('Enter')
      const explanation = explanationOf(control)
      await expect(explanation).toBeVisible()
      const scroller = await rectOf(page.locator(`[data-gallery-scroller="${ANALYSIS_ID}"]`))
      const frame = await rectOf(item.locator('[data-chart-frame]'))
      const box = await rectOf(explanation.locator('span').first())
      // Before: 53-148 px past the scroller's right edge.
      expect(inside(box, scroller), `${stat}: ${JSON.stringify(box)} outside the scroller ${JSON.stringify(scroller)}`).toBe(true)
      expect(inside(box, frame), `${stat}: outside the frame`).toBe(true)
      await page.keyboard.press('Escape')
    }
    // Still no sideways page scroll.
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  })

  test('the table view lists the moving average, the trend line and the flagged day', async ({ page }) => {
    await openGallery(page)
    const item = page.locator(ANALYSIS)
    await item.getByRole('button', { name: 'View as table' }).click()
    const table = item.getByRole('table', { name: 'Trend analysis by day' })
    await expect(table).toBeVisible()
    await expect(table.getByRole('columnheader')).toHaveText(['Day (UTC)', '7-day moving average', 'Trend line', 'Flagged as unusual'])
    // One row per day on the axis, run-free days included.
    await expect(table.locator('tbody tr')).toHaveCount(30)
    const flagged = table.getByRole('row', { name: new RegExp(FLAGGED_DAY) })
    await expect(flagged).toContainText(RULE)
    await expect(flagged).toContainText(FLAGGED_DETAIL)
    // Every row carries a value or "—": the moving average and the trend line
    // are listed for the flagged day, and exactly one day is flagged.
    const cells = await table.locator('tbody tr').evaluateAll((rows) =>
      rows.map((row) => Array.from(row.children, (cell) => (cell.textContent ?? '').trim())),
    )
    const flaggedRow = cells.find((row) => row[0] === FLAGGED_DAY)
    expect(flaggedRow?.[1], 'moving average on the flagged day').toMatch(/^\d+\.\d%$/)
    expect(flaggedRow?.[2], 'trend line on the flagged day').toMatch(/^\d+\.\d% \(fit\)$/)
    expect(cells.filter((row) => row[3] !== '—').map((row) => row[0])).toEqual([FLAGGED_DAY])
    // The statistics themselves are stated in words above the table.
    await expect(item.locator('[data-trend-table-summary]')).toContainText('Pass rate is falling 0.8 pts per week')
    // Nothing in the opened table escapes its box either.
    await expectNoTextEscapes(item.locator('[data-chart-frame]'), `${ANALYSIS_ID}, table open`)
  })

  test('below 7 days with runs the toggles are disabled with the reason, and the weeks are still compared', async ({
    page,
  }) => {
    await openGallery(page)
    const item = page.locator(SPARSE)
    const reason = item.locator('[data-trend-disabled-reason]')
    await expect(reason).toHaveText('Needs at least 7 days with runs')
    const reasonId = await reason.getAttribute('id')
    for (const name of [MOVING_AVERAGE, TREND_LINE]) {
      const toggle = item.getByRole('button', { name })
      await expect(toggle).toHaveAttribute('aria-disabled', 'true')
      // Described by the visible reason, ONCE: no tooltip repeating it.
      await expect(toggle).toHaveAttribute('aria-describedby', reasonId ?? '')
      await expect(explanationOf(toggle)).toHaveCount(0)
      // `aria-disabled`, not `disabled`: it stays focusable so the reason is
      // heard, and so it has to REFUSE activation itself — by keyboard and by
      // pointer (forced: Playwright will not click an aria-disabled button).
      await toggle.focus()
      await page.keyboard.press('Enter')
      await page.keyboard.press('Space')
      await toggle.click({ force: true })
      await expect(toggle).toHaveAttribute('aria-pressed', 'false')
    }
    await expect(item.locator('.trend-overlay-moving-average, .trend-overlay-trend-line, .trend-overlay-halo')).toHaveCount(0)
    await expect(item.locator('[data-trend-anomaly]')).toHaveCount(0)
    await expect(item.locator('[data-chart-takeaway]')).toHaveText(
      'Last 7 days 73.0% vs 94.8% the previous 7 (down 21.8 pts; 600 vs 600 executions)',
    )
    await item.getByRole('button', { name: 'View as table' }).click()
    await expect(item.locator('[data-trend-table-summary]')).toContainText('Overlays unavailable: Needs at least 7 days with runs.')
  })

  for (const theme of ALL_THEMES) {
    test(`the pressed state and the overlays read at 3:1 or better (${theme})`, async ({ page }, testInfo) => {
      await openGallery(page, `?theme=${theme}`)
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
      const item = page.locator(ANALYSIS)
      const card = await item.locator('[data-chart-frame]').evaluate((el) => getComputedStyle(el).backgroundColor)

      // ── The toggles: pressed is a check mark and an accent border, not only a tint.
      const average = item.locator('[data-trend-toggle="movingAverage"]')
      const trend = item.locator('[data-trend-toggle="trendLine"]')
      await trend.click()
      await page.mouse.move(0, 0)
      await expect(trend).toHaveAttribute('aria-pressed', 'false')
      const toggles = await Promise.all(
        [average, trend].map((toggle) =>
          toggle.evaluate((el) => {
            const check = el.querySelector('[data-trend-toggle-check] path')
            return {
              border: getComputedStyle(el).borderTopColor,
              background: getComputedStyle(el).backgroundColor,
              check: check ? getComputedStyle(check).stroke : null,
              strike: el.querySelector('[data-trend-swatch-strike]') !== null,
            }
          }),
        ),
      )
      const [pressed, off] = toggles
      // The tint the pressed state used to rely on alone measured 1.07-1.22:1.
      const measured = {
        pressedBorderVsCard: contrastOf(pressed.border, card),
        checkVsToggle: contrastOf(pressed.check ?? card, pressed.background === 'rgba(0, 0, 0, 0)' ? card : pressed.background),
        tintVsCard: contrastOf(pressed.background === 'rgba(0, 0, 0, 0)' ? card : pressed.background, card),
      }
      expect(measured.pressedBorderVsCard, `pressed border vs card (${theme})`).toBeGreaterThanOrEqual(3)
      expect(pressed.check, 'a pressed toggle carries a check mark').not.toBeNull()
      expect(measured.checkVsToggle, `check mark vs the pressed toggle (${theme})`).toBeGreaterThanOrEqual(3)
      expect(off.check, 'an off toggle carries no check mark').toBeNull()
      expect(off.strike, "an off toggle's swatch is struck through").toBe(true)
      await trend.click()
      await page.mouse.move(0, 0)

      // ── The overlays: each on a card-coloured halo of its own geometry, under the rate line.
      const overlays = await item.locator(CHART_SVG).evaluate((svg) => {
        const layers = Array.from(svg.querySelectorAll('.recharts-layer.recharts-bar, .recharts-layer.recharts-line'))
        const order = layers.map((layer) =>
          layer.classList.contains('recharts-bar')
            ? 'bars'
            : layer.classList.contains('trend-overlay-halo')
              ? 'halo'
              : layer.classList.contains('trend-overlay-moving-average')
                ? 'movingAverage'
                : layer.classList.contains('trend-overlay-trend-line')
                  ? 'trendLine'
                  : 'rate',
        )
        const halos = Array.from(svg.querySelectorAll('.trend-overlay-halo path.recharts-curve')) as SVGPathElement[]
        const bar = svg.querySelector('.recharts-bar-rectangle path, .recharts-bar-rectangle rect')
        const measure = (selector: string) => {
          const path = svg.querySelector(`${selector} path.recharts-curve`) as SVGPathElement
          const halo = halos.find((candidate) => candidate.getAttribute('d') === path.getAttribute('d')) ?? null
          return {
            stroke: getComputedStyle(path).stroke,
            width: Number.parseFloat(getComputedStyle(path).strokeWidth),
            halo: halo ? { stroke: getComputedStyle(halo).stroke, width: Number.parseFloat(getComputedStyle(halo).strokeWidth), dash: halo.getAttribute('stroke-dasharray') } : null,
          }
        }
        return {
          order,
          bar: bar ? getComputedStyle(bar).fill : null,
          movingAverage: measure('.trend-overlay-moving-average'),
          trendLine: measure('.trend-overlay-trend-line'),
        }
      })
      // Bars, then both halos, then the rate line, then the two overlays (in
      // whichever order they were last switched on) — after a toggle off and
      // on, which used to put the re-shown halo over the rate line.
      expect(overlays.order.slice(0, 4), 'paint order').toEqual(['bars', 'halo', 'halo', 'rate'])
      expect([...overlays.order.slice(4)].sort(), 'paint order').toEqual(['movingAverage', 'trendLine'])
      const report: Record<string, number> = { ...measured }
      for (const key of ['movingAverage', 'trendLine'] as const) {
        const overlay = overlays[key]
        expect(overlay.halo, `${key}: no halo drawn along the same path`).not.toBeNull()
        if (!overlay.halo || !overlays.bar) continue
        expect(overlay.halo.stroke, `${key}: the halo is the card colour`).toBe(card)
        expect(overlay.halo.dash, `${key}: the halo is solid`).toBeNull()
        expect(overlay.halo.width - overlay.width, `${key}: halo margin`).toBeGreaterThanOrEqual(4)
        // What the eye compares: the overlay against what is IMMEDIATELY beside it — the halo, not the bar under it.
        report[`${key}VsBarRaw`] = contrastOf(overlay.stroke, overlays.bar)
        report[`${key}VsHalo`] = contrastOf(overlay.stroke, overlay.halo.stroke)
        expect(report[`${key}VsHalo`], `${key} vs its halo (${theme})`).toBeGreaterThanOrEqual(3)
      }
      testInfo.annotations.push({ type: `contrast-${theme}`, description: JSON.stringify(report) })
    })
  }

  for (const theme of ALL_THEMES) {
    test(`has no accessibility violations at any impact, overlays on, explained and tabled (${theme})`, async ({
      page,
    }) => {
      await openGallery(page, `?theme=${theme}`)
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
      const scope = [ANALYSIS, SPARSE]
      // Drawn, overlays on, the sparse item's disabled toggles and reason.
      await expectNoBlockingViolations(page, theme, [], scope)
      // An explanation open (the page-wide audit never sees one), and a toggletip open.
      await page.locator(`${ANALYSIS} [data-trend-toggle="movingAverage"]`).focus()
      await expect(page.locator(`${ANALYSIS} [data-trend-toggle="movingAverage"] ~ [role="tooltip"]`)).toBeVisible()
      await expectNoBlockingViolations(page, theme, [], scope)
      await page.locator(`${ANALYSIS} [data-trend-stat="anomalies"] button`).focus()
      await page.keyboard.press('Enter')
      await expect(page.locator(`${ANALYSIS} [data-trend-stat="anomalies"] [role="tooltip"]`)).toBeVisible()
      await expectNoBlockingViolations(page, theme, [], scope)
      await page.keyboard.press('Escape')
      // The flagged day's tooltip open.
      await pointAtFlaggedDay(page)
      await expect(page.locator(`${ANALYSIS} [data-chart-tooltip]`)).toBeVisible()
      await expectNoBlockingViolations(page, theme, [], scope)
      // Both table views open. `ChartTable`'s scroll box is a focusable named
      // region now, so nothing is allow-listed (it was `scrollable-region-focusable`).
      for (const id of scope) await page.locator(id).getByRole('button', { name: 'View as table' }).click()
      await expect(page.locator(`${ANALYSIS} [data-chart-trend-table] table`)).toBeVisible()
      await expect(page.locator(`${SPARSE} [data-chart-trend-table]`)).toBeVisible()
      await expectNoBlockingViolations(page, theme, [], scope)
    })
  }
})
