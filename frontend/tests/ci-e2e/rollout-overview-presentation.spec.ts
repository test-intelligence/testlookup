/**
 * /overview in presentation mode (VIZ-106, R2-13): the KPI values and their
 * changes stay whole at room size ("8m 32s" broke at its space, "+4.2%" broke
 * under its triangle). Desk mode is measured alongside. (The coverage strip
 * under the trend was removed in the UX redesign P2.)
 *
 * UX redesign P3: the KPIs are the page template's `KpiStrip` of five compact
 * `MetricCard`s — one row of five in both modes on a desktop page (it was six
 * at the desk and four in the room); where five room-sized tiles cannot fit
 * (640 px) the strip wraps rather than push the page sideways. A compact card's value never wraps (`whitespace-nowrap`)
 * and its change line is one truncated line (its full text is the title), so
 * "every value and change on one line" holds by construction; this spec
 * measures it in a real browser at three widths, and that nothing overflows:
 * not the page sideways, and not a sparkline over its own value or out of its
 * tile (the room-sized value leaves it too little room: it drops below).
 *
 * Fail-closed harness: `tests/lib/production-pages.ts`; helpers: `rollout.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { expectNoHorizontalOverflow, openRollout } from '../lib/rollout'
import { OVERVIEW_ON } from '../visual/production/fixtures'

const STORAGE_KEY = 'testlookup-presentation'
const ready = (p: Page) => p.getByRole('heading', { level: 1, name: 'Dashboard' })

async function seedPresentation(page: Page, enabled: boolean) {
  await page.addInitScript(
    ({ key, enabled }) => localStorage.setItem(key, JSON.stringify({ state: { enabled }, version: 0 })),
    { key: STORAGE_KEY, enabled },
  )
}

/** Each KPI tile's value and change: text, size, and how many lines it takes. */
function measure(page: Page) {
  return page.evaluate(() => {
    const lines = (el: Element) => {
      const range = document.createRange()
      range.selectNodeContents(el)
      // A new line starts where a text box begins below the one before it ends (boxes of
      // two sizes on one baseline overlap: one line).
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
    const strip = document.querySelector('#main-content [data-kpi-strip]') as HTMLElement
    const values = Array.from(strip.querySelectorAll('[data-metric-card="compact"] p.tabular-nums'), read)
    const changes = Array.from(strip.querySelectorAll('[data-metric-card="compact"] [data-metric-trend]'), read)
    // `auto-fit` keeps its collapsed tracks in the computed value as `0px`:
    // count the tracks that hold a tile.
    const columns = getComputedStyle(strip).gridTemplateColumns.split(' ').filter((t) => parseFloat(t) > 0).length
    // Each tile's sparkline (its aside) against its value and its card: the P3
    // baseline drew the room-sized "4437" over its own sparkline, and that
    // sparkline into the next tile.
    const collisions = Array.from(strip.querySelectorAll('[data-metric-card="compact"]'), (card) => {
      const value = card.querySelector('p.tabular-nums')?.getBoundingClientRect()
      const aside = (card.querySelector('[data-metric-aside]')?.firstElementChild as Element | null)?.getBoundingClientRect()
      if (!value || !aside) return null
      const box = card.getBoundingClientRect()
      const overlaps = value.left < aside.right && aside.left < value.right && value.top < aside.bottom && aside.top < value.bottom
      const outside = aside.left < box.left || aside.right > box.right
      return overlaps || outside ? `${(card.textContent ?? '').trim().slice(0, 24)}: overlaps ${overlaps}, outside ${outside}` : null
    }).filter(Boolean)
    const asides = strip.querySelectorAll('[data-metric-aside]').length
    return { values, changes, columns, collisions, asides }
  })
}

for (const width of [640, 1280, 1920]) {
  test.describe(`Overview at ${width} px`, () => {
    test.use({ viewport: { width, height: 1400 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

    test('presentation mode: every KPI value and change on one line', async ({ page }) => {
      await seedPresentation(page, true)
      await openRollout(page, '/overview', { handlers: OVERVIEW_ON, ready })
      await expect(page.locator('html')).toHaveAttribute('data-presentation', 'on')
      const m = await measure(page)
      expect(m.values.length, 'five KPI values').toBe(5)
      expect(m.changes.length, 'the fixture sends a change for every KPI').toBe(5)
      for (const v of m.values) expect(v.lines, `KPI value "${v.text}"`).toBe(1)
      for (const d of m.changes) expect(d.lines, `KPI change "${d.text}"`).toBe(1)
      expect(m.asides, 'the fixture draws three sparklines').toBe(3)
      expect(m.collisions, 'no sparkline over its value or out of its tile').toEqual([])
      // One row of five on a desktop page; at 640 px the room-sized tiles
      // cannot fit five across, so the strip wraps (and nothing scrolls sideways).
      if (width >= 1280) expect(m.columns, 'one row of five tiles').toBe(5)
      else expect(m.columns, 'fewer columns when five cannot fit').toBeLessThan(5)
      await expectNoHorizontalOverflow(page, `Overview, presentation, ${width}`)
    })

    test('desk mode: one row of five tiles on a desktop page', async ({ page }) => {
      await seedPresentation(page, false)
      await openRollout(page, '/overview', { handlers: OVERVIEW_ON, ready })
      await expect(page.locator('html')).not.toHaveAttribute('data-presentation', 'on')
      const m = await measure(page)
      if (width >= 1280) expect(m.columns).toBe(5)
      else expect(m.columns).toBeLessThanOrEqual(5)
      await expectNoHorizontalOverflow(page, `Overview, desk, ${width}`)
      for (const v of m.values) expect(v.lines, `KPI value "${v.text}"`).toBe(1)
      expect(m.asides).toBe(3)
      expect(m.collisions, 'no sparkline over its value or out of its tile').toEqual([])
    })
  })
}
