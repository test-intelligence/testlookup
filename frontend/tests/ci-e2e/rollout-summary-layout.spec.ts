/**
 * /reports/summary with the catalogue ON, while its chunk is on its way and
 * after it lands (R1-2, R1 (a), R2-6).
 *
 * The page draws the catalogue frames' boxes itself until the section chunk
 * arrives (`SummaryCatalogueShell`): the same header markup and words, the
 * toolbar's room, the plot heights. Held here, IN A BROWSER, at the desk
 * widths: the section chunk's module request is held open, the stand-in is
 * measured, the request is let go, and the real frames must lay out in
 * exactly the stand-in's boxes: every title and takeaway in the same place at
 * the same size (the takeaway is the page's LCP element, and the real one is
 * not a new LCP entry only if it is no larger), and both tables where they
 * were. `SummaryCatalogueShell.test.tsx` holds the markup equal in jsdom; this
 * proves the browser lays it out alike.
 *
 * The viewport is short (600 px) so that the lazy trend under the headline
 * row stays its placeholder through the swap: what moves when the TREND
 * mounts and draws is the trend's own height (its frame grows from the
 * loading state to the drawn one), not the swap's, and is not measured here.
 *
 * And at 375 px both tables scroll sideways inside their card, so each
 * scroller is a named, focusable region (axe `scrollable-region-focusable`).
 *
 * Fail-closed harness: `tests/lib/production-pages.ts`; helpers: `rollout.ts`.
 */
import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { expectDrawn, expectInventory, networkQuiet, openRollout, sectionFrame, SHELL_ON } from '../lib/rollout'
import { CATALOGUE_ON, PROJECT_ID, SUMMARY_REPORT_ON } from '../visual/production/fixtures'

const ready = (p: Page) => p.getByText('Total tests', { exact: true })
/** The section chunk's module (the dev server's `.tsx`, a build's hashed `.js`); NOT `SummaryCatalogueShell`. */
const SECTION_CHUNK = /\/SummaryCatalogue(\.tsx|-[\w-]+\.js)(\?.*)?$/

const FRAMES = ['Status breakdown', 'Results by suite', 'Failures by test'] as const

interface Box {
  x: number
  y: number
  width: number
  height: number
}

/** Every box the swap must not move, from the stand-in (`shell`) or the real frames. */
async function layout(page: Page, which: 'shell' | 'real') {
  return page.evaluate(
    ({ which, frames }) => {
      const round = (r: DOMRect) => ({
        x: Math.round(r.x * 100) / 100,
        y: Math.round(r.y * 100) / 100,
        width: Math.round(r.width * 100) / 100,
        height: Math.round(r.height * 100) / 100,
      })
      const frameOf = (title: string): Element | null =>
        which === 'shell'
          ? document.querySelector(`[data-summary-shell-frame="${title}"]`)
          : (Array.from(document.querySelectorAll('[data-catalogue-section] [data-chart-frame]')).find(
              (frame) => frame.querySelector('h2')?.textContent === title,
            ) ?? null)
      const out: Record<string, Box | null> = {}
      for (const title of frames) {
        const frame = frameOf(title)
        const takeaway = frame?.querySelector(which === 'shell' ? '[data-summary-shell-takeaway]' : '[data-chart-takeaway]')
        out[`${title} frame`] = frame ? round(frame.getBoundingClientRect()) : null
        out[`${title} title`] = frame?.querySelector('h2') ? round((frame.querySelector('h2') as Element).getBoundingClientRect()) : null
        out[`${title} takeaway`] = takeaway ? round(takeaway.getBoundingClientRect()) : null
      }
      const tables = Array.from(document.querySelectorAll('#main-content table'))
      out['per-suite table'] = tables[0] ? round(tables[0].getBoundingClientRect()) : null
      out['top-failing table'] = tables[1] ? round(tables[1].getBoundingClientRect()) : null
      return out
    },
    { which, frames: [...FRAMES] },
  )
}

/** Hold the section chunk's request until `release()`; counts how often it was asked. */
async function holdSectionChunk(page: Page) {
  let release: () => void = () => {}
  const held = new Promise<void>((resolve) => {
    release = resolve
  })
  const state = { asked: 0, release: () => release() }
  await page.route(SECTION_CHUNK, async (route) => {
    state.asked += 1
    await held
    await route.continue()
  })
  return state
}

/** The trend below the screen has not asked yet: the shell, the seam's lookup and the report. */
const inventoryOff = [
  ...SHELL_ON,
  `GET /api/v1/reports/summary?project_id=${PROJECT_ID}&days=30&mode=latest`,
  // VIZ-607: the reader's background exports.
  `GET /api/v1/reports/summary/exports?project_id=${PROJECT_ID}`,
]

for (const width of [1024, 1280, 1440, 1920]) {
  test.describe(`Summary, catalogue on, the swap at ${width} px`, () => {
    test.use({ viewport: { width, height: 600 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

    test('the real frames land exactly in the stand-in’s boxes: titles, takeaways and both tables stay put', async ({ page }) => {
      const chunk = await holdSectionChunk(page)
      const { api, errors } = await openRollout(page, '/reports/summary', {
        handlers: SUMMARY_REPORT_ON,
        flags: CATALOGUE_ON,
        ready,
      })
      // The chunk is asked for (the preload) and held: the page draws its stand-in.
      await expect.poll(() => chunk.asked).toBeGreaterThan(0)
      await expect(page.locator('[data-summary-catalogue-shell="headline"]')).toBeVisible()
      await expect(page.locator('[data-summary-catalogue-shell="top-failing"]')).toBeVisible()
      await expect(page.locator('[data-catalogue-section]')).toHaveCount(0)
      const before = await layout(page, 'shell')
      for (const [name, box] of Object.entries(before)) expect(box, `stand-in: ${name}`).not.toBeNull()

      chunk.release()
      await expectDrawn(sectionFrame(page, 'summary-suites', 'Results by suite'), 'suites')
      await expectDrawn(sectionFrame(page, 'summary-donut', 'Status breakdown'), 'donut')
      await expectDrawn(sectionFrame(page, 'summary-top-failing', 'Failures by test'), 'failures')
      await expect(page.locator('[data-summary-catalogue-shell]')).toHaveCount(0)
      // The trend is still its placeholder (it is below this screen), as it was in the stand-in.
      await expect(page.locator('[data-lazy-section="summary-trend"]')).toHaveCount(1)
      expect(await layout(page, 'real'), 'the boxes after the swap').toEqual(before)

      // The preload asked for nothing the page did not already ask for (the trend has not mounted).
      await networkQuiet(page, api)
      expectInventory(api, errors, inventoryOff, `Summary at ${width} with the chunk held`)
    })
  })
}

test.describe('Summary, the tables at 375 px (R2-6)', () => {
  test.use({ viewport: { width: 375, height: 1600 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('each sideways-scrolling table is a named, focusable region with a visible focus ring', async ({ page }) => {
    await openRollout(page, '/reports/summary', { handlers: SUMMARY_REPORT_ON, flags: CATALOGUE_ON, ready })
    await expectDrawn(sectionFrame(page, 'summary-suites', 'Results by suite'), 'suites')
    for (const name of ['Per-suite breakdown table', 'Top failing tests table']) {
      const region = page.getByRole('region', { name })
      // It does scroll sideways here: that is why it must be reachable.
      expect(await region.evaluate((el) => el.scrollWidth > el.clientWidth), `${name} scrolls`).toBe(true)
      await region.focus()
      await expect(region).toBeFocused()
      // Focus from the keyboard shows the ring (focus-visible): Tab out and back in.
      await page.keyboard.press('Shift+Tab')
      await page.keyboard.press('Tab')
      await expect(region).toBeFocused()
      const ring = await region.evaluate((el) => getComputedStyle(el).boxShadow)
      expect(ring, `${name}: focus ring`).not.toBe('none')
      // The keyboard scrolls it.
      await page.keyboard.press('ArrowRight')
      await page.keyboard.press('ArrowRight')
      await expect.poll(() => region.evaluate((el) => el.scrollLeft), { message: `${name} scrolls by keyboard` }).toBeGreaterThan(0)
    }
    const result = await new AxeBuilder({ page }).include('#main-content').withRules(['scrollable-region-focusable']).analyze()
    expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html.slice(0, 80)).join(' | ')}`)).toEqual([])
  })
})
