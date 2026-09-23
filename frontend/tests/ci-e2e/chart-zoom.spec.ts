/**
 * VIZ-407 — the range brush, in a real browser: a mouse drag on the strip,
 * every keyboard key on both handles, handles that meet but never cross,
 * Reset zoom and where focus goes, handle targets of at least 24×24, and
 * "Apply as time filter" — disabled with its reason on screen, and enabled
 * for a range the page window can hold.
 *
 * The gallery items (`chartGalleryFixtures.ts`) open ALREADY zoomed:
 *   - `timeseries-zoom-trend`: 42 days (2026-02-17 … 2026-03-30), zoomed to
 *     2026-03-15 … 2026-03-28, i.e. positions 26 … 39 of 0 … 41;
 *   - `timeseries-zoom-apply-disabled`: 10 days (03-01 … 03-10), zoomed to
 *     03-02 … 03-08 (1 … 7), with `applyAsWindow` over the report pages'
 *     window options (1, 7, 14, 30, 90).
 *
 * WHAT IS NOT COVERED HERE, plainly: the story's "promote-to-filter round trip
 * with URL". No route mounts a zoomable frame yet (the charts are
 * gallery-only until VIZ-408), and the gallery sits outside `AppLayout`, so
 * nothing syncs its scope to `?window=` and there is no window chip to see.
 * This spec covers the half the gallery CAN show — Apply writes the page
 * window through the report filter bar's own store, that choice survives a
 * reload, and the zoom clears — and `src/components/charts/zoom/applyAsWindow.url.test.tsx`
 * covers store → `?window=` → store through the real `useScopeUrlSync`. The
 * chip, reload-from-URL and Back belong to the first report page that mounts
 * a zoomable chart (VIZ-408).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { galleryItem, openGallery, watchErrors } from '../lib/chart-gallery-page'

const TREND = 'timeseries-zoom-trend'
const APPLY = 'timeseries-zoom-apply-disabled'
const RESET = 'Reset zoom'
const APPLY_LABEL = 'Apply as time filter'
/** `PROMOTE_NOT_LATEST_REASON` in `zoomModel.ts`. */
const NOT_LATEST = 'Only a range that ends on the latest day can become the page window'
const WINDOW_STORE = 'testlookup-time-window'

const startHandle = (item: Locator) => item.getByRole('slider', { name: 'Start of zoom range' })
const endHandle = (item: Locator) => item.getByRole('slider', { name: 'End of zoom range' })
const selectionLabel = (item: Locator) => item.locator('[data-chart-brush-selection-label]')
const valueOf = async (handle: Locator) => Number(await handle.getAttribute('aria-valuenow'))
/** The page window the report filter bar keeps (persisted by `timeWindowStore`). */
const pageWindow = (page: Page) =>
  page.evaluate((key) => JSON.parse(localStorage.getItem(key) ?? '{"state":{}}').state?.days ?? null, WINDOW_STORE)
/** Execution bars actually drawn: one per day with runs in view. */
const drawnBars = (item: Locator) =>
  item.locator('.recharts-bar-rectangle path').evaluateAll((nodes) => nodes.filter((n) => n.getBoundingClientRect().width > 0).length)

async function press(handle: Locator, key: string, expected: number) {
  await handle.press(key)
  await expect(handle, `${key} → ${expected}`).toHaveAttribute('aria-valuenow', String(expected))
}

test.describe('VIZ-407 zoom and brush', () => {
  test('opens zoomed: Reset zoom, the selection named, the footer note, and markers outside the view in the table', async ({
    page,
  }) => {
    const errors = watchErrors(page)
    await openGallery(page)
    const item = galleryItem(page, TREND)
    await item.scrollIntoViewIfNeeded()
    await expect(item.getByRole('button', { name: RESET })).toBeVisible()
    await expect(startHandle(item)).toHaveAttribute('aria-valuenow', '26')
    await expect(endHandle(item)).toHaveAttribute('aria-valuenow', '39')
    await expect(selectionLabel(item)).toContainText('(14 of 42 days)')
    await expect(item.locator('[data-chart-zoom-note]')).toContainText('Zoomed to')
    await expect(item.locator('[data-chart-zoom-note]')).toContainText('42 days')
    // The flagged day and the in-view release are drawn; the table still lists every release.
    await expect(item.locator('[data-trend-anomaly="2026-03-23"]')).toHaveCount(1)
    await item.getByRole('button', { name: 'View as table' }).click()
    await expect(item.locator('[data-chart-release-outside-zoom]')).toHaveCount(2)
    expect(errors).toEqual([])
  })

  test('every handle key moves by its step; the handles meet but never cross', async ({ page }) => {
    await openGallery(page)
    const item = galleryItem(page, TREND)
    await item.scrollIntoViewIfNeeded()
    const start = startHandle(item)
    const end = endHandle(item)
    await start.focus()
    // A visible focus ring on the focused handle (focus-visible: keyboard focus).
    expect(await start.evaluate((el) => getComputedStyle(el).boxShadow)).not.toBe('none')

    await press(start, 'ArrowRight', 27)
    await press(start, 'ArrowLeft', 26)
    await press(start, 'ArrowUp', 27)
    await press(start, 'ArrowDown', 26)
    await press(start, 'PageUp', 33)
    await press(start, 'PageDown', 26)
    await press(start, 'Home', 0)
    await expect(selectionLabel(item)).toContainText('(40 of 42 days)')
    // The chart follows the keys: every day with runs in 0 … 39 is drawn now.
    await expect.poll(() => drawnBars(item)).toBeGreaterThan(30)
    // End on the START handle takes it to the end handle: they meet (one day)…
    await press(start, 'End', 39)
    await expect(selectionLabel(item)).toContainText('(1 of 42 days)')
    // …and never cross: the start handle cannot pass the end one.
    await press(start, 'ArrowRight', 39)
    await press(start, 'PageUp', 39)
    // The end handle cannot pass the start one either.
    await end.focus()
    await press(end, 'ArrowLeft', 39)
    await press(end, 'PageDown', 39)
    await press(end, 'Home', 39)
    await press(end, 'End', 41)
    await press(end, 'ArrowDown', 40)
    await press(end, 'ArrowUp', 41)
    // Each handle's range is bounded by the other.
    await expect(start).toHaveAttribute('aria-valuemax', '41')
    await expect(end).toHaveAttribute('aria-valuemin', '39')
    // Dragging the start handle past the end one stops at the end one.
    const endBox = (await end.boundingBox()) as { x: number; y: number; width: number; height: number }
    await press(start, 'Home', 0)
    const startBox = (await start.boundingBox()) as { x: number; y: number; width: number; height: number }
    await page.mouse.move(startBox.x + startBox.width / 2, startBox.y + startBox.height / 2)
    await page.mouse.down()
    await page.mouse.move(endBox.x + endBox.width + 80, startBox.y + startBox.height / 2, { steps: 10 })
    await page.mouse.up()
    const [s, e] = [await valueOf(start), await valueOf(end)]
    // It stops ON the end handle (they meet); the end handle does not move.
    expect(e).toBe(41)
    expect(s, `start ${s} did not stop at end ${e}`).toBe(e)
  })

  test('a mouse drag across the strip zooms to it; a single click only picks a day; the page window is untouched', async ({
    page,
  }) => {
    await openGallery(page)
    const windowBefore = await pageWindow(page)
    const item = galleryItem(page, TREND)
    await item.scrollIntoViewIfNeeded()
    await item.getByRole('button', { name: RESET }).click()
    await expect(selectionLabel(item)).toHaveText('Showing all 42 days')
    await expect(item.getByRole('button', { name: RESET })).toHaveCount(0)
    const barsAll = await drawnBars(item)

    const track = item.locator('[data-chart-brush-track]')
    const box = (await track.boundingBox()) as { x: number; y: number; width: number; height: number }
    const y = box.y + box.height / 2
    // One click is not a zoom: it picks a day and waits for the other end (SC 2.5.7).
    await page.mouse.click(box.x + box.width * 0.5, y)
    await expect(item.getByRole('button', { name: RESET })).toHaveCount(0)
    await expect(selectionLabel(item)).toContainText('click the day the range ends')
    await expect(item.locator('[data-chart-brush-picked]')).toBeVisible()
    // A press with a 2 px wobble in it — which a real hand makes — is still a
    // CLICK (the strip needs 3 px of travel before it drags): the second end.
    await page.mouse.move(box.x + box.width * 0.6, y)
    await page.mouse.down()
    await page.mouse.move(box.x + box.width * 0.6 + 2, y, { steps: 2 })
    await page.mouse.up()
    await expect(startHandle(item)).toHaveAttribute('aria-valuenow', String(Math.floor(0.5 * 42)))
    await expect(endHandle(item)).toHaveAttribute('aria-valuenow', String(Math.floor(0.6 * 42)))
    await expect(item.locator('[data-chart-brush-picked]')).toHaveCount(0)
    await item.getByRole('button', { name: RESET }).click()

    // Drag from 25 % to 50 % of the strip: days 10 … 21 (each day owns 1/42 of it).
    await page.mouse.move(box.x + box.width * 0.25, y)
    await page.mouse.down()
    await page.mouse.move(box.x + box.width * 0.5, y, { steps: 8 })
    // Previewed while dragging, committed on release.
    await page.mouse.up()
    await expect(item.getByRole('button', { name: RESET })).toBeVisible()
    const [s, e] = [await valueOf(startHandle(item)), await valueOf(endHandle(item))]
    expect(s).toBe(Math.floor(0.25 * 42))
    expect(e).toBe(Math.floor(0.5 * 42))
    await expect(selectionLabel(item)).toContainText(`(${e - s + 1} of 42 days)`)
    await expect.poll(() => drawnBars(item)).toBeLessThan(barsAll)
    // Local: the global window did not move.
    expect(await pageWindow(page)).toBe(windowBefore)
  })

  test('Reset zoom restores every day and hands focus to the start handle', async ({ page }) => {
    await openGallery(page)
    const item = galleryItem(page, TREND)
    await item.scrollIntoViewIfNeeded()
    const reset = item.getByRole('button', { name: RESET })
    await reset.focus()
    await page.keyboard.press('Enter')
    await expect(reset).toHaveCount(0)
    await expect(startHandle(item)).toBeFocused()
    await expect(startHandle(item)).toHaveAttribute('aria-valuenow', '0')
    await expect(endHandle(item)).toHaveAttribute('aria-valuenow', '41')
    await expect(selectionLabel(item)).toHaveText('Showing all 42 days')
    await expect(item.locator('[data-chart-zoom-note]')).toHaveCount(0)
  })

  test('both handles are targets of at least 24 × 24 px', async ({ page }) => {
    await openGallery(page)
    for (const id of [TREND, APPLY, 'multi-series-zoom-hidden', 'duration-band-zoomed']) {
      const item = galleryItem(page, id)
      await item.scrollIntoViewIfNeeded()
      for (const handle of [startHandle(item), endHandle(item)]) {
        const box = (await handle.boundingBox()) as { width: number; height: number }
        expect(box.width, `${id}: handle width`).toBeGreaterThanOrEqual(24)
        expect(box.height, `${id}: handle height`).toBeGreaterThanOrEqual(24)
      }
    }
  })

  /**
   * SC 2.5.8 (review A8): centred on their edges, two handles one day apart
   * overlapped by ~10 px and the start handle kept ~14 px of its own. Now
   * each sits outside its edge: a one-day zoom, at the widest and on a phone,
   * leaves both whole and apart, and the handle itself is what a pointer at
   * its centre hits.
   */
  for (const [label, viewport] of [
    ['desktop', { width: 1280, height: 800 }],
    ['phone', { width: 390, height: 844 }],
  ] as const) {
    test(`a one-day zoom leaves both handles whole and apart (${label})`, async ({ page }) => {
      await page.setViewportSize(viewport)
      await openGallery(page, '?canvas=fluid')
      const item = galleryItem(page, TREND)
      await item.scrollIntoViewIfNeeded()
      await press(startHandle(item), 'End', 39)
      await expect(selectionLabel(item)).toContainText('(1 of 42 days)')
      const boxes = []
      for (const handle of [startHandle(item), endHandle(item)]) {
        await handle.scrollIntoViewIfNeeded()
        const box = (await handle.boundingBox()) as { x: number; y: number; width: number; height: number }
        expect(box.width).toBeGreaterThanOrEqual(24)
        expect(box.height).toBeGreaterThanOrEqual(24)
        const hit = await page.evaluate(
          ({ x, y }) => document.elementFromPoint(x, y)?.closest('[data-chart-brush-handle]')?.getAttribute('data-chart-brush-handle'),
          { x: box.x + box.width / 2, y: box.y + box.height / 2 },
        )
        expect(hit, 'the other handle covers this one').toBe(await handle.getAttribute('data-chart-brush-handle'))
        boxes.push(box)
      }
      const [start, end] = boxes
      expect(start.x + start.width, 'the handles overlap').toBeLessThanOrEqual(end.x + 0.5)
    })
  }

  test('two clicks on the strip zoom to any range — no drag needed (SC 2.5.7)', async ({ page }) => {
    await openGallery(page)
    const item = galleryItem(page, TREND)
    await item.scrollIntoViewIfNeeded()
    // Zoomed to 26 … 39; two clicks inside that range, near its end.
    const box = (await item.locator('[data-chart-brush-track]').boundingBox()) as { x: number; y: number; width: number; height: number }
    const at = (day: number) => ({ x: box.x + ((day + 0.5) / 42) * box.width, y: box.y + box.height / 2 })
    await page.mouse.click(at(36).x, at(36).y)
    await page.mouse.click(at(37).x, at(37).y)
    await expect(startHandle(item)).toHaveAttribute('aria-valuenow', '36')
    await expect(endHandle(item)).toHaveAttribute('aria-valuenow', '37')
    await expect(selectionLabel(item)).toContainText('(2 of 42 days)')
  })

  test('Apply as time filter: disabled with its reason on screen, then enabled for a range the window can hold', async ({
    page,
  }) => {
    await openGallery(page)
    const item = galleryItem(page, APPLY)
    await item.scrollIntoViewIfNeeded()
    const apply = item.locator('[data-chart-zoom-apply]')
    const reason = item.locator('[data-chart-zoom-apply-reason]')
    // Seven days, but not ending on the latest: disabled, and SAYS why.
    await expect(apply).toHaveText(APPLY_LABEL)
    await expect(apply).toHaveAttribute('aria-disabled', 'true')
    await expect(reason).toHaveText(NOT_LATEST)
    await expect(reason).toBeVisible()
    await expect(apply).toHaveAttribute('aria-describedby', (await reason.getAttribute('id')) ?? 'missing')
    // Activating it does nothing. (`aria-disabled`, not `disabled`, so it stays
    // focusable and its reason is read; Playwright calls it not enabled, so force.)
    await apply.click({ force: true })
    await expect(item.getByRole('button', { name: RESET })).toBeVisible()
    await expect(startHandle(item)).toHaveAttribute('aria-valuenow', '1')

    // To the latest day: nine days, which no page window option is.
    await press(endHandle(item), 'End', 9)
    await expect(reason).toHaveText('The page window can be 1, 7, 14, 30 or 90 days; this range is 9 days')
    // The latest day alone: named as the filter bar and its chip name that
    // window — "last 24 hours", never "last 1 day" (review F8).
    await press(startHandle(item), 'End', 9)
    await expect(apply).toHaveText('Apply as time filter: last 24 hours')
    // Seven days ending on the latest: offered, with the value in its name.
    await press(startHandle(item), 'PageDown', 2)
    await press(startHandle(item), 'ArrowRight', 3)
    await expect(apply).toHaveText('Apply as time filter: last 7 days')
    await expect(apply).not.toHaveAttribute('aria-disabled', 'true')
    await expect(reason).toHaveCount(0)

    await apply.click()
    // The page window is now 7 days, written through the filter bar's own store…
    await expect.poll(() => pageWindow(page)).toBe(7)
    // …which is a scope change, so the zoom clears and focus waits on the chart body.
    await expect(item.getByRole('button', { name: RESET })).toHaveCount(0)
    await expect(item.locator('[data-chart-body]')).toBeFocused()
    await expect(page.locator('[data-chart-announcer]').first()).toContainText('Page window set to the last 7 days')

    // The choice survives a reload: the same 7-day range is now "already the window".
    await page.reload()
    const again = galleryItem(page, APPLY)
    await again.scrollIntoViewIfNeeded()
    expect(await pageWindow(page)).toBe(7)
    await press(endHandle(again), 'End', 9)
    await press(startHandle(again), 'PageUp', 8)
    await press(startHandle(again), 'PageDown', 1)
    await press(startHandle(again), 'ArrowRight', 2)
    await press(startHandle(again), 'ArrowRight', 3)
    await expect(again.locator('[data-chart-zoom-apply-reason]')).toHaveText('The page window is already the last 7 days')
  })

  test('the zoomed comparison keeps its hidden series hidden, and the zoomed band states the inverted day in view', async ({
    page,
  }) => {
    await openGallery(page)
    const multi = galleryItem(page, 'multi-series-zoom-hidden')
    await multi.scrollIntoViewIfNeeded()
    await expect(multi.locator('.recharts-wrapper > svg.recharts-surface path.recharts-line-curve')).toHaveCount(2)
    await expect(multi.locator('[data-legend-series="cart"]')).toHaveAttribute('aria-pressed', 'false')
    await multi.getByRole('button', { name: RESET }).click()
    await expect(multi.locator('.recharts-wrapper > svg.recharts-surface path.recharts-line-curve')).toHaveCount(2)
    await expect(multi.locator('[data-legend-series="cart"]')).toHaveAttribute('aria-pressed', 'false')

    const band = galleryItem(page, 'duration-band-zoomed')
    await band.scrollIntoViewIfNeeded()
    await expect(band.locator('[data-chart="duration-trend"]')).toContainText('p95 was below p50 on 1 day')
    await expect(selectionLabel(band)).toContainText('(3 of 6 days)')
  })
})

test.describe('VIZ-407 zoom — a finger has a single-pointer way too (SC 2.5.7)', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true })

  test('two taps on the strip zoom to the two days, with no drag', async ({ page }) => {
    await openGallery(page, '?canvas=fluid')
    const item = galleryItem(page, TREND)
    const track = item.locator('[data-chart-brush-track]')
    await track.scrollIntoViewIfNeeded()
    const box = (await track.boundingBox()) as { x: number; y: number; width: number; height: number }
    const at = (day: number) => ({ x: box.x + ((day + 0.5) / 42) * box.width, y: box.y + box.height / 2 })
    await page.touchscreen.tap(at(30).x, at(30).y)
    await expect(selectionLabel(item)).toContainText('click the day the range ends')
    await page.touchscreen.tap(at(33).x, at(33).y)
    await expect(startHandle(item)).toHaveAttribute('aria-valuenow', '30')
    await expect(endHandle(item)).toHaveAttribute('aria-valuenow', '33')
  })
})
