/**
 * UX redesign P3, the page template's KPI strip (`KpiStrip` of compact
 * `MetricCard`s), hermetic: on every page that has one, at the two desktop
 * widths, no tile's label is cut short ("MEAN TIME TO RESOL…" in the P3
 * baseline `defects-kpis`) and no value or sparkline runs out of its tile.
 *
 * A compact label is one truncated line by design (a narrower window must not
 * grow the strip); this spec holds the labels the pages actually use to fit,
 * with `LABEL_MARGIN_PX` to spare. A longer name is the card's `hint` (its
 * hover text).
 *
 * Runs has no strip: its banner states the facts (P3).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock).
 */
import { expect, test, type Page } from '@playwright/test'
import { landmark, openProductionPage } from '../lib/production-pages'
import { openRollout } from '../lib/rollout'
import {
  COVERAGE_ON,
  DEFECTS,
  FAILURES_ON,
  NOW,
  OVERVIEW_ON,
  PROJECT_ID,
  SUMMARY_REPORT_ON,
  TRENDS_ON,
  USER,
  VALUE_METRICS,
} from '../visual/production/fixtures'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

/** What a label keeps free of its box, for text that renders a little wider elsewhere. */
const LABEL_MARGIN_PX = 4

type Open = (page: Page) => Promise<unknown>

const PAGES: Record<string, Open> = {
  '/overview': (page) =>
    openRollout(page, '/overview', { handlers: OVERVIEW_ON, ready: (p) => p.getByRole('heading', { level: 1, name: 'Dashboard' }) }),
  '/failures': (page) => openRollout(page, '/failures', { handlers: FAILURES_ON, ready: (p) => landmark(p, 'Failure verdict') }),
  '/defects': (page) => openRollout(page, '/defects', { handlers: DEFECTS, ready: (p) => landmark(p, 'Defect KPIs') }),
  '/trends': (page) => openRollout(page, '/trends', { handlers: TRENDS_ON, ready: (p) => landmark(p, 'Trend metrics') }),
  '/coverage': (page) => openRollout(page, '/coverage', { handlers: COVERAGE_ON, ready: (p) => landmark(p, 'Run cadence') }),
  '/reports/summary': (page) =>
    openRollout(page, '/reports/summary', { handlers: SUMMARY_REPORT_ON, ready: (p) => p.getByText('Total tests', { exact: true }) }),
  '/value-metrics': (page) =>
    openProductionPage(page, '/value-metrics', {
      theme: 'signal',
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: VALUE_METRICS,
      ready: (p) => p.getByRole('heading', { name: 'Hours saved per month' }),
    }),
}

/** Each compact tile: its label when cut short, and whether its value or aside leaves the tile. */
function readTiles(page: Page) {
  return page.evaluate((LABEL_MARGIN_PX) => {
    const textWidth = (el: Element) => {
      const range = document.createRange()
      range.selectNodeContents(el)
      return range.getBoundingClientRect().width
    }
    return Array.from(document.querySelectorAll('#main-content [data-metric-card="compact"]'), (card) => {
      const label = card.querySelector('p') as HTMLElement
      const box = card.getBoundingClientRect()
      const value = card.querySelector('p.tabular-nums')?.getBoundingClientRect()
      const aside = (card.querySelector('[data-metric-aside]')?.firstElementChild as Element | null)?.getBoundingClientRect()
      const out = (r?: DOMRect) => !!r && (r.left < box.left - 0.5 || r.right > box.right + 0.5)
      return {
        label: (label.textContent ?? '').trim(),
        // 4 px to spare: "MEAN TIME TO RESOLVE" fitted to the pixel in DejaVu on
        // Windows and was cut on the Linux runner (same font, other rasteriser).
        cut: textWidth(label) > label.clientWidth - LABEL_MARGIN_PX,
        width: [Math.ceil(textWidth(label)), label.clientWidth],
        valueOut: out(value),
        asideOut: out(aside),
      }
    })
  }, LABEL_MARGIN_PX)
}

for (const width of [1280, 1440]) {
  for (const [path, open] of Object.entries(PAGES)) {
    test(`${path} at ${width} px: every KPI label whole, every value and sparkline inside its tile`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      await open(page)
      await expect(page.locator('#main-content [data-metric-card="compact"]').first()).toBeVisible()
      // Measure in the page's own font, not a fallback still standing in for it.
      await page.evaluate(() => document.fonts.ready)
      const tiles = await readTiles(page)
      console.log(`KPI ${path} ${width} ${JSON.stringify(tiles)}`)
      expect(tiles.length, 'a strip of tiles').toBeGreaterThan(0)
      expect(tiles.filter((t) => t.cut).map((t) => t.label), 'labels cut short').toEqual([])
      expect(tiles.filter((t) => t.valueOut || t.asideOut).map((t) => t.label), 'tiles drawing outside themselves').toEqual([])
    })
  }
}
