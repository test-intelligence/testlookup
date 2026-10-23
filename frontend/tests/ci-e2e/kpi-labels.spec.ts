/**
 * UX redesign P3, the page template's KPI strip (`KpiStrip` of compact
 * `MetricCard`s), hermetic: on every page that has one, at the two desktop
 * widths, no tile's label is cut short ("MEAN TIME TO RESOL…" in the P3
 * baseline `defects-kpis`), no value or sparkline runs out of its tile, and
 * no sparkline or note drops a line under its value (the strip stays one row
 * of values: the second P3 baseline run showed Trends' and Summary's strips
 * grow 20-32 px when it did).
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
        // On a line of its own, under the value (fine in the room, not at the desk).
        asideBelow: !!value && !!aside && aside.top >= value.bottom - 1,
      }
    })
  }, LABEL_MARGIN_PX)
}

/**
 * W29b fix-kpi: each change line's figure (the direction word and the number with its unit, `[data-change-head]`)
 * when it is cut or runs out of its tile. One truncating span drew "Down 1.6 p… (worse)" at 1280 px on CI and
 * "Down … (worse)" in presentation mode; only the context after the figure may take the ellipsis.
 */
function readFigures(page: Page) {
  return page.evaluate(() =>
    Array.from(document.querySelectorAll('#main-content [data-metric-card="compact"] [data-change-head]'), (head) => {
      const card = head.closest('[data-metric-card]') as HTMLElement
      const style = getComputedStyle(card)
      const inner = card.getBoundingClientRect().right - parseFloat(style.paddingRight) - parseFloat(style.borderRightWidth)
      const judgement = head.parentElement?.querySelector('[data-trend-judgement]')
      const right = Math.max(head.getBoundingClientRect().right, judgement?.getBoundingClientRect().right ?? 0)
      return {
        figure: (head.textContent ?? '').trim(),
        cut: head.scrollWidth > head.clientWidth + 0.5 || right > inner + 0.5,
      }
    }),
  )
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
      // At the desk a tile is one line of value: an aside that drops a line grows
      // the strip (Trends 100 -> 132 px on CI's font with a 4rem floor).
      expect(tiles.filter((t) => t.asideBelow).map((t) => t.label), 'asides dropped under their value').toEqual([])
      const figures = await readFigures(page)
      console.log(`KPI-FIGURES ${path} ${width} ${JSON.stringify(figures)}`)
      expect(figures.filter((f) => f.cut).map((f) => f.figure), 'change figures cut short').toEqual([])
    })
  }
}

// W29b fix-kpi: in presentation mode the tile's label (`.eyebrow`) and change line (`text-xs`) are 16 px. Five
// 186 px tiles at 1280 cut "TOTAL EXECU…", "NEW FAILURE…" and "Down … (worse)" on CI's fonts: the strip's floor
// grows with the room size (`--kpi-tile-min`), so every label stays whole and every change keeps its figure. (In
// the room an aside may drop under its value: the 40 px value leaves it no room.)
for (const width of [1280, 1440]) {
  for (const [path, open] of Object.entries(PAGES)) {
    test(`${path} at ${width} px in presentation mode: every KPI label whole, every change figure whole`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      await page.addInitScript(() =>
        localStorage.setItem('testlookup-presentation', JSON.stringify({ state: { enabled: true }, version: 0 })),
      )
      await open(page)
      await expect(page.locator('html')).toHaveAttribute('data-presentation', 'on')
      await expect(page.locator('#main-content [data-metric-card="compact"]').first()).toBeVisible()
      await page.evaluate(() => document.fonts.ready)
      const tiles = await readTiles(page)
      const figures = await readFigures(page)
      console.log(`KPI-ROOM ${path} ${width} ${JSON.stringify({ tiles, figures })}`)
      expect(tiles.length, 'a strip of tiles').toBeGreaterThan(0)
      expect(tiles.filter((t) => t.cut).map((t) => t.label), 'labels cut short').toEqual([])
      expect(tiles.filter((t) => t.valueOut || t.asideOut).map((t) => t.label), 'tiles drawing outside themselves').toEqual([])
      expect(figures.filter((f) => f.cut).map((f) => f.figure), 'change figures cut short').toEqual([])
    })
  }
}

// HON-07 relabelled the share of defects with no Jira issue, which was called
// "Escape rate", "Unlinked to Jira". W06 DEF-03 measures the real escape rate
// (defects filed after a release they affect had shipped) and its tile REPLACES
// that one (the strip holds five tiles); the unlinked count stays in the
// banner's "No Jira ticket" fact. The label fits at 1280 px (the loop above).
test('/defects shows the real "Escape rate" from its endpoint, and no "Unlinked to Jira" tile', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await PAGES['/defects'](page)
  await expect(page.locator('#main-content [data-metric-card="compact"]').first()).toBeVisible()
  const labels = (await readTiles(page)).map((t) => t.label)
  expect(labels).toContain('Escape rate')
  expect(labels).not.toContain('Unlinked to Jira')
  // The endpoint's fixture: 5 of 12 attributed defects escaped.
  const escape = page.locator('#main-content [data-metric-card="compact"]').filter({ hasText: 'Escape rate' })
  await expect(escape.locator('p.tabular-nums')).toHaveText('41.7%')
  await expect(landmark(page, 'Defect queue verdict').locator('[data-banner-fact]').filter({ hasText: 'No Jira ticket' })).toBeVisible()
})
