/**
 * VIZ-608 — full-screen visualisation mode, in a real browser.
 *
 * The story's Playwright tests: enter and exit (Escape AND the button), focus
 * back on the button, the page's scroll position restored, a tooltip visible
 * inside full screen, the chart itself taller — and the fallback when the
 * Fullscreen API is unavailable: a `role="dialog"` overlay that traps Tab and
 * leaves on Escape.
 *
 * Headless Chromium may or may not grant the real Fullscreen API; when it does
 * not answer, the frame falls back to the overlay after `GRANT_TIMEOUT_MS`
 * (1.5 s). The first tests therefore accept either mode and say which they
 * saw; the fallback test REMOVES the API, so it is always the overlay.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { CHART_SVG, galleryItem, leaveCharts, markCentres, openGallery, pointAt, watchErrors } from '../lib/chart-gallery-page'

const ITEM = 'timeseries-trend-releases'
/** `CHART_MESSAGES.fullScreen` / `exitFullScreen`. */
const FULL_SCREEN = 'Full screen'
const EXIT_FULL_SCREEN = 'Exit full screen'
/** Long enough for the grant timeout (1.5 s) to hand over to the overlay. */
const ENTER_TIMEOUT = 5000

const frameOf = (item: Locator) => item.locator('[data-chart-frame]')

async function enter(page: Page, item: Locator) {
  // A real click: the Fullscreen API refuses a request without a user gesture.
  await item.getByRole('button', { name: FULL_SCREEN, exact: true }).click()
  const frame = frameOf(item)
  await expect(frame).toHaveAttribute('data-chart-fullscreen', /^(api|overlay)$/, { timeout: ENTER_TIMEOUT })
  // The body is measured and handed to the chart after entry; let it settle.
  await expect(frame.getByRole('button', { name: EXIT_FULL_SCREEN })).toBeVisible()
  return frame
}

/** The page's scroll position: the window here (the gallery has no inner scroller). */
const scrollY = (page: Page) => page.evaluate(() => window.scrollY)

test.describe('VIZ-608 full screen', () => {
  test('enter and leave with Escape: a dialog named by the chart, a taller chart, then scroll and focus restored', async ({
    page,
  }, testInfo) => {
    const errors = watchErrors(page)
    await openGallery(page)
    const item = galleryItem(page, ITEM)
    const frame = frameOf(item)
    // The chart sits far down the page, so a restore to 0 would be caught. The
    // position is read with the button already in view, as a reader clicking
    // it has it — Playwright's click would otherwise scroll it there first.
    await item.getByRole('button', { name: FULL_SCREEN, exact: true }).scrollIntoViewIfNeeded()
    const before = await scrollY(page)
    expect(before).toBeGreaterThan(200)
    const svgBefore = (await item.locator(CHART_SVG).boundingBox())?.height ?? 0
    const bodyBefore = (await frame.locator('[data-chart-body]').boundingBox())?.height ?? 0

    await enter(page, item)
    const mode = await frame.getAttribute('data-chart-fullscreen')
    testInfo.annotations.push({ type: 'fullscreen-mode', description: String(mode) })
    console.log(`full-screen mode granted in this browser: ${mode}`)
    await expect(frame).toHaveAttribute('role', 'dialog')
    await expect(frame).toHaveAttribute('aria-modal', 'true')
    const titleId = await frame.getByRole('heading', { level: 2 }).getAttribute('id')
    await expect(frame).toHaveAttribute('aria-labelledby', titleId ?? 'missing')
    if (mode === 'api') {
      expect(await page.evaluate(() => document.fullscreenElement?.hasAttribute('data-chart-frame'))).toBe(true)
    }
    // It fills the screen, and the CHART grows with it — not just the box around it.
    const viewport = page.viewportSize() as { width: number; height: number }
    const box = (await frame.boundingBox()) as { width: number; height: number }
    expect(box.width).toBeGreaterThanOrEqual(viewport.width - 1)
    expect(box.height).toBeGreaterThanOrEqual(viewport.height - 1)
    await expect.poll(async () => (await frame.locator('[data-chart-body]').boundingBox())?.height ?? 0).toBeGreaterThan(bodyBefore + 100)
    await expect.poll(async () => (await item.locator(CHART_SVG).boundingBox())?.height ?? 0).toBeGreaterThan(svgBefore + 100)
    // Presentation type sizes: the title grows.
    const titleSize = await frame.getByRole('heading', { level: 2 }).evaluate((el) => Number.parseFloat(getComputedStyle(el).fontSize))
    expect(titleSize).toBeGreaterThanOrEqual(24)

    // The chart grew under the pointer that clicked "Full screen", and
    // Chromium hovers what moves under a still pointer: a tooltip may be up,
    // and Escape would dismiss IT first (innermost first — see the review-fix
    // tests below). Nothing is showing here, so one Escape leaves.
    await leaveCharts(page)
    await page.keyboard.press('Escape')
    await expect(frame).not.toHaveAttribute('data-chart-fullscreen', /.+/)
    await expect(frame).not.toHaveAttribute('role', 'dialog')
    expect(await page.evaluate(() => document.fullscreenElement)).toBeNull()
    await expect(item.getByRole('button', { name: FULL_SCREEN, exact: true })).toBeFocused()
    expect(await scrollY(page)).toBe(before)
    expect(errors).toEqual([])
  })

  test('leave with the "Exit full screen" button: focus and scroll restored the same way', async ({ page }) => {
    await openGallery(page)
    const item = galleryItem(page, ITEM)
    await item.getByRole('button', { name: FULL_SCREEN, exact: true }).scrollIntoViewIfNeeded()
    const before = await scrollY(page)
    const frame = await enter(page, item)
    await frame.getByRole('button', { name: EXIT_FULL_SCREEN }).click()
    await expect(frame).not.toHaveAttribute('data-chart-fullscreen', /.+/)
    await expect(item.getByRole('button', { name: FULL_SCREEN, exact: true })).toBeFocused()
    expect(await scrollY(page)).toBe(before)
  })

  /**
   * The gallery pins every chart in a fixed-height box, so a frame that leaves
   * the flow on entry (the overlay is `position: fixed`; so is `:fullscreen`)
   * leaves a hole of the same size and the page never gets shorter: its scroll
   * could not be lost, so restoring it could not be tested. In the app the
   * frame's box is NOT pinned. This test unpins the LAST item's box and scrolls
   * to the very bottom, so entry really shortens the page and clamps the scroll
   * — which is the case the restore exists for — in both modes.
   */
  for (const fallback of [false, true]) {
    test(`the scroll a shortened page loses on entry is restored on exit (${fallback ? 'overlay' : 'Fullscreen API'})`, async ({
      page,
    }) => {
      if (fallback) {
        await page.addInitScript(() => {
          delete (Element.prototype as { requestFullscreen?: unknown }).requestFullscreen
          Object.defineProperty(Document.prototype, 'fullscreenEnabled', { configurable: true, get: () => false })
        })
      }
      await openGallery(page)
      const last = page.locator('[data-gallery-item]').last()
      await expect(last.locator(CHART_SVG)).toBeVisible()
      await last.locator('[data-gallery-canvas]').evaluate((el) => {
        ;(el as HTMLElement).style.height = 'auto'
      })
      // Chromium's scroll anchoring would otherwise put the scroll back by itself
      // when the frame returns to the flow, and a restore that never ran would
      // pass. Anchoring is not something to rely on: it does not run in every
      // browser, nor in every scroller (the app's scroller is <main>).
      await page.addStyleTag({ content: '*, html, body { overflow-anchor: none !important; }' })
      // 200 px short of the bottom: deep enough that entry clamps the scroll,
      // and a position that nothing but a restore returns to — the button stays
      // in view at the clamped position too, so focusing it scrolls nothing.
      await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight - window.innerHeight - 200))
      const button = last.getByRole('button', { name: FULL_SCREEN, exact: true })
      await expect(button).toBeInViewport()
      const before = await scrollY(page)
      const frame = await enter(page, last)
      // The page really did get shorter under the frame: the scroll was clamped.
      expect(await scrollY(page), 'entry did not move the scroll: this test would prove nothing').toBeLessThan(before)
      await frame.getByRole('button', { name: EXIT_FULL_SCREEN }).click()
      await expect(frame).not.toHaveAttribute('data-chart-fullscreen', /.+/)
      await expect(button).toBeFocused()
      await expect.poll(() => scrollY(page)).toBe(before)
    })
  }

  test('a tooltip is visible inside full screen, within the frame', async ({ page }) => {
    await openGallery(page)
    const item = galleryItem(page, ITEM)
    await item.scrollIntoViewIfNeeded()
    const frame = await enter(page, item)
    await expect.poll(async () => (await item.locator(CHART_SVG).boundingBox())?.height ?? 0).toBeGreaterThan(400)
    const bars = await markCentres(item.locator('.recharts-bar-rectangle path'))
    await leaveCharts(page)
    await pointAt(page, bars[2])
    const tooltip = frame.locator('[data-chart-tooltip]')
    await expect(tooltip).toBeVisible()
    const tip = (await tooltip.boundingBox()) as { x: number; y: number; width: number; height: number }
    const box = (await frame.boundingBox()) as { x: number; y: number; width: number; height: number }
    expect(tip.x).toBeGreaterThanOrEqual(box.x)
    expect(tip.y).toBeGreaterThanOrEqual(box.y)
    expect(tip.x + tip.width).toBeLessThanOrEqual(box.x + box.width + 0.5)
    expect(tip.y + tip.height).toBeLessThanOrEqual(box.y + box.height + 0.5)
    // It is really on top: the element at its centre belongs to it.
    const onTop = await page.evaluate(
      ({ x, y }) => Boolean(document.elementFromPoint(x, y)?.closest('[data-chart-tooltip]')),
      { x: tip.x + tip.width / 2, y: tip.y + tip.height / 2 },
    )
    expect(onTop, 'the tooltip is covered by something').toBe(true)
    await page.keyboard.press('Escape')
  })

  test('fallback: with no Fullscreen API, a role="dialog" overlay traps Tab and leaves on Escape', async ({ page }) => {
    await page.addInitScript(() => {
      // An old browser, or an iframe without allow="fullscreen".
      delete (Element.prototype as { requestFullscreen?: unknown }).requestFullscreen
      Object.defineProperty(Document.prototype, 'fullscreenEnabled', { configurable: true, get: () => false })
    })
    const errors = watchErrors(page)
    await openGallery(page)
    const item = galleryItem(page, ITEM)
    await item.getByRole('button', { name: FULL_SCREEN, exact: true }).scrollIntoViewIfNeeded()
    const before = await scrollY(page)
    const frame = await enter(page, item)
    await expect(frame).toHaveAttribute('data-chart-fullscreen', 'overlay')
    await expect(frame).toHaveAttribute('role', 'dialog')
    await expect(frame).toHaveAttribute('aria-modal', 'true')
    // Fixed over the page, covering the viewport.
    expect(await frame.evaluate((el) => getComputedStyle(el).position)).toBe('fixed')

    // Tab and Shift+Tab never leave the dialog.
    const inside = () => page.evaluate(() => Boolean(document.activeElement?.closest('[data-chart-fullscreen]')))
    for (let i = 0; i < 14; i++) {
      await page.keyboard.press('Tab')
      expect(await inside(), `Tab ${i + 1} left the dialog`).toBe(true)
    }
    for (let i = 0; i < 6; i++) {
      await page.keyboard.press('Shift+Tab')
      expect(await inside(), `Shift+Tab ${i + 1} left the dialog`).toBe(true)
    }

    // No tooltip up (see the first test), so one Escape leaves.
    await leaveCharts(page)
    await page.keyboard.press('Escape')
    await expect(frame).not.toHaveAttribute('data-chart-fullscreen', /.+/)
    await expect(frame).not.toHaveAttribute('role', 'dialog')
    await expect(item.getByRole('button', { name: FULL_SCREEN, exact: true })).toBeFocused()
    expect(await scrollY(page)).toBe(before)
    expect(errors).toEqual([])
  })
})

/** The Fullscreen API taken away (an old browser, an iframe without allow="fullscreen"): the overlay. */
async function withoutFullscreenApi(page: Page) {
  await page.addInitScript(() => {
    delete (Element.prototype as { requestFullscreen?: unknown }).requestFullscreen
    Object.defineProperty(Document.prototype, 'fullscreenEnabled', { configurable: true, get: () => false })
  })
}

/**
 * Whether Chromium's accessibility tree IGNORES the element `selector`
 * matches — what a screen reader is told about it, which the DOM alone does
 * not show (review A1: Chromium prunes everything outside the full-screen
 * element, the page's live regions included).
 */
async function ignoredByAssistiveTech(page: Page, selector: string): Promise<boolean> {
  const cdp = await page.context().newCDPSession(page)
  try {
    const { root } = await cdp.send('DOM.getDocument', { depth: 0 })
    const { nodeId } = await cdp.send('DOM.querySelector', { nodeId: root.nodeId, selector })
    expect(nodeId, `${selector} is not in the page`).toBeGreaterThan(0)
    const { nodes } = await cdp.send('Accessibility.getPartialAXTree', { nodeId, fetchRelatives: false })
    return nodes[0]?.ignored === true
  } finally {
    await cdp.detach()
  }
}

/** A tooltip showing inside the frame (it may linger 300 ms after its chart lets go). */
const visibleTips = (frame: Locator) => frame.locator('[data-chart-tooltip]:visible')

for (const fallback of [false, true]) {
  const mode = fallback ? 'overlay' : 'Fullscreen API'

  test.describe(`VIZ-608 full screen, review fixes (${mode})`, () => {
    test.beforeEach(async ({ page }) => {
      if (fallback) await withoutFullscreenApi(page)
    })

    test('A1: the page announcer speaks from INSIDE the full-screen frame, where assistive technology still hears it', async ({
      page,
    }) => {
      await openGallery(page)
      const item = galleryItem(page, ITEM)
      await item.scrollIntoViewIfNeeded()
      const frame = await enter(page, item)
      await expect(frame).toHaveAttribute('data-chart-fullscreen', fallback ? 'overlay' : 'api')
      const outlet = frame.locator('[data-chart-announcer-outlet="assertive"]')
      await expect(outlet).toHaveCount(1)
      await expect(frame.locator('[data-chart-announcer-outlet="polite"]')).toHaveAttribute('role', 'status')
      // The keyboard cursor's readout is announced — inside the frame, not on the page.
      await frame.locator('[data-chart-cursor]').first().focus()
      await page.keyboard.press('Home')
      await expect(outlet).toContainText('2026-03-01')
      await expect(page.locator('[data-chart-announcer="assertive"]')).toHaveText('')
      // What a screen reader is told: the page's regions are out of the tree
      // (pruned by full screen, or inert behind the overlay); the frame's are in it.
      expect(await ignoredByAssistiveTech(page, '[data-chart-announcer="assertive"]')).toBe(true)
      expect(await ignoredByAssistiveTech(page, '[data-chart-announcer-outlet="assertive"]')).toBe(false)
      expect(await ignoredByAssistiveTech(page, '[data-chart-announcer-outlet="polite"]')).toBe(false)
      // Out of full screen, the page's announcer is the one voice again.
      await frame.getByRole('button', { name: EXIT_FULL_SCREEN }).click()
      await expect(frame.locator('[data-chart-announcer-outlet]')).toHaveCount(0)
      await frame.locator('[data-chart-cursor]').first().focus()
      await page.keyboard.press('End')
      await expect(page.locator('[data-chart-announcer="assertive"]')).toContainText('2026-03-10')
    })

    test('A7: Escape closes the innermost thing first — the menu, then the tooltip, then full screen', async ({ page }) => {
      await openGallery(page)
      const item = galleryItem(page, ITEM)
      await item.scrollIntoViewIfNeeded()
      const frame = await enter(page, item)
      await expect.poll(async () => (await item.locator(CHART_SVG).boundingBox())?.height ?? 0).toBeGreaterThan(400)
      const bars = await markCentres(item.locator('.recharts-bar-rectangle path'))
      await leaveCharts(page)
      await pointAt(page, bars[2])
      await expect(visibleTips(frame)).toHaveCount(1)
      // The menu opened from the keyboard, so the pointer stays on the bar and the tooltip stays up.
      const trigger = frame.getByRole('button', { name: 'Export', exact: true })
      await trigger.focus()
      await page.keyboard.press('ArrowDown')
      await expect(frame.getByRole('menu')).toBeVisible()

      await page.keyboard.press('Escape')
      await expect(frame.getByRole('menu')).toHaveCount(0)
      await expect(visibleTips(frame)).toHaveCount(1)
      await expect(frame).toHaveAttribute('data-chart-fullscreen', /^(api|overlay)$/)

      await page.keyboard.press('Escape')
      await expect(visibleTips(frame)).toHaveCount(0)
      await expect(frame).toHaveAttribute('data-chart-fullscreen', /^(api|overlay)$/)

      await page.keyboard.press('Escape')
      await expect(frame).not.toHaveAttribute('data-chart-fullscreen', /.+/)
      await expect(item.getByRole('button', { name: FULL_SCREEN, exact: true })).toBeFocused()
    })

    test('A7: a tooltip up and focus on "Exit full screen" — one Escape dismisses the tooltip only', async ({ page }) => {
      await openGallery(page)
      const item = galleryItem(page, ITEM)
      await item.scrollIntoViewIfNeeded()
      const frame = await enter(page, item)
      await expect.poll(async () => (await item.locator(CHART_SVG).boundingBox())?.height ?? 0).toBeGreaterThan(400)
      const bars = await markCentres(item.locator('.recharts-bar-rectangle path'))
      await leaveCharts(page)
      await pointAt(page, bars[4])
      await expect(visibleTips(frame)).toHaveCount(1)
      await frame.getByRole('button', { name: EXIT_FULL_SCREEN }).focus()
      await page.keyboard.press('Escape')
      await expect(visibleTips(frame)).toHaveCount(0)
      await expect(frame).toHaveAttribute('data-chart-fullscreen', /^(api|overlay)$/)
      await page.keyboard.press('Escape')
      await expect(frame).not.toHaveAttribute('data-chart-fullscreen', /.+/)
    })

    test('A4: at 320 px the full-screen frame reflows — nothing scrolls sideways, the way out is on screen', async ({
      page,
    }) => {
      await page.setViewportSize({ width: 320, height: 720 })
      await openGallery(page, '?canvas=fluid')
      const item = galleryItem(page, ITEM)
      await item.scrollIntoViewIfNeeded()
      const frame = await enter(page, item)
      // Let the chart re-measure into the frame.
      await expect.poll(async () => (await item.locator(CHART_SVG).boundingBox())?.height ?? 0).toBeGreaterThan(200)
      const overflow = await frame.evaluate((el) => ({ scroll: el.scrollWidth, client: el.clientWidth }))
      expect(overflow.scroll, `the frame scrolls sideways: ${JSON.stringify(overflow)}`).toBeLessThanOrEqual(overflow.client)
      // Every toolbar control lies inside the screen.
      for (const button of await frame.locator('[data-chart-toolbar] button').all()) {
        const box = (await button.boundingBox()) as { x: number; width: number }
        expect(box.x).toBeGreaterThanOrEqual(0)
        expect(box.x + box.width, `${await button.innerText()} runs off the screen`).toBeLessThanOrEqual(320)
      }
      await expect(frame.getByRole('button', { name: EXIT_FULL_SCREEN })).toBeInViewport({ ratio: 1 })
    })

    test('A10: the page behind the full-screen frame is inert, and live again on exit', async ({ page }) => {
      await openGallery(page)
      const item = galleryItem(page, ITEM)
      await item.scrollIntoViewIfNeeded()
      const frame = await enter(page, item)
      // The frame and every ancestor stay live; another chart on the page is inert.
      expect(
        await frame.evaluate((el) => {
          for (let node: Element | null = el; node; node = node.parentElement) if (node.hasAttribute('inert')) return false
          return true
        }),
      ).toBe(true)
      const other = galleryItem(page, 'bar-ranked')
      expect(await other.evaluate((el) => el.closest('[inert]') !== null)).toBe(true)
      await frame.getByRole('button', { name: EXIT_FULL_SCREEN }).click()
      await expect(frame).not.toHaveAttribute('data-chart-fullscreen', /.+/)
      expect(await page.locator('[inert]').count()).toBe(0)
    })
  })
}

test('A7 (Fullscreen API): when the BROWSER leaves full screen on its own, no menu or tooltip is left behind', async ({ page }) => {
  await openGallery(page)
  const item = galleryItem(page, ITEM)
  await item.scrollIntoViewIfNeeded()
  const frame = await enter(page, item)
  // Only the real API can be left behind the page's back.
  await expect(frame).toHaveAttribute('data-chart-fullscreen', 'api')
  await expect.poll(async () => (await item.locator(CHART_SVG).boundingBox())?.height ?? 0).toBeGreaterThan(400)
  const bars = await markCentres(item.locator('.recharts-bar-rectangle path'))
  await leaveCharts(page)
  await pointAt(page, bars[3])
  const trigger = frame.getByRole('button', { name: 'Export', exact: true })
  await trigger.focus()
  await page.keyboard.press('ArrowDown')
  await expect(frame.getByRole('menu')).toBeVisible()
  // The browser's own exit (its Escape, F11): the page's key handlers never run.
  await page.evaluate(() => document.exitFullscreen())
  await expect(frame).not.toHaveAttribute('data-chart-fullscreen', /.+/)
  await expect(frame.getByRole('menu')).toHaveCount(0)
  await expect(visibleTips(frame)).toHaveCount(0)
  await expect(item.getByRole('button', { name: FULL_SCREEN, exact: true })).toBeFocused()
})
