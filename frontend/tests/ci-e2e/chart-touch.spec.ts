/**
 * Touch, at a phone viewport with touch emulation (Chromium):
 *
 *   - VIZ-407 "brush on touch devices uses handles, not drag-to-pan": a finger
 *     dragging a HANDLE moves it, and a vertical swipe over the STRIP scrolls
 *     the page instead of zooming (`touch-action: pan-y` on the strip, `none`
 *     on the handles);
 *   - VIZ-601 "hover, focus or tap": a tap on a donut slice opens its tooltip.
 *
 * The finger is driven through the Chrome DevTools Protocol
 * (`Input.dispatchTouchEvent`), which goes through the browser's real input
 * pipeline — touch-action, gesture scrolling and the pointer events a finger
 * produces — rather than synthesising DOM events the page could tell apart.
 * The gallery is opened with `?canvas=fluid` so the charts are phone-wide,
 * not a 640 px canvas scrolled inside its box.
 */
import { expect, test, type CDPSession, type Page } from '@playwright/test'
import { galleryItem, markCentres, openGallery } from '../lib/chart-gallery-page'

test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true })

type Point = { x: number; y: number }

async function finger(page: Page): Promise<CDPSession> {
  return page.context().newCDPSession(page)
}

/** A one-finger gesture from `from` to `to`, in `steps` moves. */
async function swipe(cdp: CDPSession, from: Point, to: Point, steps = 12) {
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: from.x, y: from.y }] })
  for (let i = 1; i <= steps; i++) {
    const x = from.x + ((to.x - from.x) * i) / steps
    const y = from.y + ((to.y - from.y) * i) / steps
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x, y }] })
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
}

const centreOf = (box: { x: number; y: number; width: number; height: number }): Point => ({
  x: box.x + box.width / 2,
  y: box.y + box.height / 2,
})

test.describe('touch', () => {
  test('a finger drags a brush handle; a vertical swipe over the strip scrolls the page, not the zoom', async ({ page }) => {
    await openGallery(page, '?canvas=fluid')
    const item = galleryItem(page, 'timeseries-zoom-apply-disabled')
    const start = item.getByRole('slider', { name: 'Start of zoom range' })
    const end = item.getByRole('slider', { name: 'End of zoom range' })
    await start.scrollIntoViewIfNeeded()
    // The strip hands vertical pans to the page; the handles keep every touch.
    const track = item.locator('[data-chart-brush-track]')
    expect(await track.evaluate((el) => getComputedStyle(el).touchAction)).toBe('pan-y')
    expect(await start.evaluate((el) => getComputedStyle(el).touchAction)).toBe('none')
    await expect(start).toHaveAttribute('aria-valuenow', '1')
    await expect(end).toHaveAttribute('aria-valuenow', '7')
    const cdp = await finger(page)

    // Drag the END handle two days to the right with a finger.
    const trackBox = (await track.boundingBox()) as { x: number; y: number; width: number; height: number }
    const day = trackBox.width / 10
    const from = centreOf((await end.boundingBox()) as { x: number; y: number; width: number; height: number })
    await swipe(cdp, from, { x: from.x + 2 * day, y: from.y })
    await expect(end).toHaveAttribute('aria-valuenow', '9')
    await expect(start).toHaveAttribute('aria-valuenow', '1')

    // A vertical swipe over the strip (away from both handles) scrolls the page…
    const before = await page.evaluate(() => window.scrollY)
    const startBox = (await start.boundingBox()) as { x: number; y: number; width: number; height: number }
    const onStrip = { x: startBox.x + startBox.width + day * 2.5, y: trackBox.y + trackBox.height / 2 }
    await swipe(cdp, onStrip, { x: onStrip.x, y: onStrip.y - 240 })
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(before + 100)
    // …and the zoom did not move.
    await expect(start).toHaveAttribute('aria-valuenow', '1')
    await expect(end).toHaveAttribute('aria-valuenow', '9')
  })

  test('a tap on a donut slice opens its tooltip', async ({ page }) => {
    await openGallery(page, '?canvas=fluid')
    const item = galleryItem(page, 'donut-status')
    await item.scrollIntoViewIfNeeded()
    const slices = await markCentres(item.locator('.recharts-pie-sector path'))
    expect(slices.length).toBe(4)
    await page.touchscreen.tap(slices[1].x, slices[1].y)
    const tooltip = item.locator('[data-chart-tooltip]')
    await expect(tooltip).toBeVisible()
    await expect(tooltip).toContainText('Failed')
    await expect(tooltip).toContainText('Share of total')
  })
})
