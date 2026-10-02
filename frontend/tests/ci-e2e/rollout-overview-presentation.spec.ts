/**
 * /overview in presentation mode (VIZ-106, R2-13): the KPI values and their
 * changes stay whole at room size ("8m 32s" broke at its space, "+4.2%" broke
 * under its triangle), and the coverage strip under the trend shows its
 * values at the room's size too (they stayed at desk size while the same
 * numbers above were 40 px). Desk mode is measured alongside: none of it
 * applies there.
 *
 * Fail-closed harness: `tests/lib/production-pages.ts`; helpers: `rollout.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { expectNoHorizontalOverflow, openRollout } from '../lib/rollout'
import { CATALOGUE_ON, OVERVIEW_ON } from '../visual/production/fixtures'

const STORAGE_KEY = 'testlookup-presentation'
const ready = (p: Page) => p.getByRole('heading', { level: 1, name: 'Dashboard' })

async function seedPresentation(page: Page, enabled: boolean) {
  await page.addInitScript(
    ({ key, enabled }) => localStorage.setItem(key, JSON.stringify({ state: { enabled }, version: 0 })),
    { key: STORAGE_KEY, enabled },
  )
}

/** Each KPI card's value and change, and each coverage-strip value: text, size, and how many lines it takes. */
function measure(page: Page) {
  return page.evaluate(() => {
    const lines = (el: Element) => {
      const range = document.createRange()
      range.selectNodeContents(el)
      // A new line starts where a text box begins below the one before it ends (boxes of
      // two sizes on one baseline, "91" and its "%", overlap: one line).
      const rects = Array.from(range.getClientRects())
        .filter((r) => r.width > 0)
        .sort((a, b) => a.top - b.top)
      let count = 0
      let bottom = -Infinity
      for (const r of rects) {
        if (r.top >= bottom - 1) count += 1
        bottom = Math.max(bottom, r.bottom)
      }
      return count
    }
    const read = (el: Element) => ({
      text: (el.textContent ?? '').trim(),
      size: parseFloat(getComputedStyle(el).fontSize),
      lines: lines(el),
    })
    const kpiGrid = Array.from(document.querySelectorAll('#main-content .grid')).find((grid) =>
      grid.querySelector('[title="Relative change vs the previous period of the same length"]'),
    ) as HTMLElement
    const values = Array.from(kpiGrid.querySelectorAll('.rounded-xl .tabular-nums.leading-none'), read)
    const deltas = Array.from(
      kpiGrid.querySelectorAll('[title="Relative change vs the previous period of the same length"]'),
      read,
    )
    const strip = ['Automation coverage', 'Avg run duration'].map((label) => {
      // The strip's card (`rounded-md`), not the KPI card of the same name above it.
      const card = Array.from(document.querySelectorAll('#main-content .rounded-md > div:first-child')).find(
        (d) => d.textContent === label,
      )?.parentElement as HTMLElement
      return { label, ...read((card.lastElementChild as HTMLElement).firstElementChild as HTMLElement) }
    })
    const columns = getComputedStyle(kpiGrid).gridTemplateColumns.split(' ').length
    return { values, deltas, strip, columns }
  })
}

for (const width of [640, 1280, 1920]) {
  test.describe(`Overview at ${width} px`, () => {
    test.use({ viewport: { width, height: 1400 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

    test('presentation mode: every KPI value and change on one line, the strip’s values at the room’s size', async ({ page }) => {
      await seedPresentation(page, true)
      await openRollout(page, '/overview', { handlers: OVERVIEW_ON, flags: CATALOGUE_ON, ready })
      await expect(page.locator('html')).toHaveAttribute('data-presentation', 'on')
      const m = await measure(page)
      expect(m.values.length).toBeGreaterThan(0)
      for (const v of m.values) expect(v.lines, `KPI value "${v.text}"`).toBe(1)
      for (const d of m.deltas) expect(d.lines, `KPI change "${d.text}"`).toBe(1)
      // At xl, four cards a row in the room (six at the desk); below xl the desk's columns hold.
      if (width >= 1280) expect(m.columns, 'four KPI cards a row in the room').toBe(4)
      for (const s of m.strip) {
        expect(s.size, `${s.label} "${s.text}"`).toBeGreaterThanOrEqual(36)
        expect(s.lines, `${s.label} "${s.text}"`).toBe(1)
      }
      await expectNoHorizontalOverflow(page, `Overview, presentation, ${width}`)
    })

    test('desk mode: the KPI grid and the strip are as they were (six columns, 18 px strip values)', async ({ page }) => {
      await seedPresentation(page, false)
      await openRollout(page, '/overview', { handlers: OVERVIEW_ON, flags: CATALOGUE_ON, ready })
      await expect(page.locator('html')).not.toHaveAttribute('data-presentation', 'on')
      const m = await measure(page)
      expect(m.columns).toBe(width >= 1280 ? 6 : 3)
      for (const s of m.strip) expect(s.size, s.label).toBe(18)
    })
  })
}
