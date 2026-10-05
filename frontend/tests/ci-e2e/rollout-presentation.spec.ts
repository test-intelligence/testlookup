/**
 * VIZ-106 presentation mode on the real routes (Wave 2.6, plan 3.5, 5.3).
 *
 * The mode is a per-browser preference (`store/presentationStore.ts`, key
 * `testlookup-presentation`) toggled from the sidebar footer. It sets
 * `data-presentation="on"` on `<html>` at import time, before React draws
 * anything, and `index.css` raises the metric-value and headline tokens and
 * the small UI sizes; every Recharts drawing is scaled by 16/11 (11 px axis
 * text reads 16 px).
 *
 * Asserted here: the toggle (state, attribute, persistence across a reload,
 * off again); the attribute is already on when the app first puts content on
 * the page (no flash of desk sizes); metric values grow onto their raised
 * tokens; and at 1280 px, with every catalogue section drawn, nothing is
 * wider than the scroller and no chart text escapes its frame, while every
 * axis tick reads at 16 px or more on screen.
 *
 * Fail-closed harness: `tests/lib/production-pages.ts`; helpers: `rollout.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { landmark, TALL_VIEWPORT, type ApiHandlers, type FlagMap } from '../lib/production-pages'
import { textEscapes } from '../lib/chart-text-escapes'
import {
  expectDrawn,
  expectNoErrorFrame,
  expectNoHorizontalOverflow,
  frameStates,
  networkQuiet,
  openRollout,
  sectionFrame,
} from '../lib/rollout'
import {
  CATALOGUE_ON,
  GATE_CLUSTERS,
  HEATMAP_ON,
  OVERVIEW_ON,
  releaseGateOn,
  RUN_ID,
  SUITE,
  SUITE_DETAIL_ON,
  SUMMARY_REPORT_ON,
  TRENDS_ON,
} from '../visual/production/fixtures'

const STORAGE_KEY = 'testlookup-presentation'
/** A Recharts drawing (the selector of `tests/lib/chart-gallery-page.ts`). */
const CHART_SVG = '.recharts-wrapper > svg.recharts-surface'
const overviewReady = (p: Page) => p.getByRole('heading', { level: 1, name: 'Dashboard' })

/** Store the mode as ON before any app script runs (the zustand `persist` record). */
async function seedPresentation(page: Page) {
  await page.addInitScript((key) => {
    localStorage.setItem(key, JSON.stringify({ state: { enabled: true }, version: 0 }))
  }, STORAGE_KEY)
}

/**
 * Every element whose font size is one of the metric-value / headline tokens
 * (`--text-stat-*`, `--text-display-*`, inline or as a Tailwind arbitrary
 * value), with its computed size and the token's current value.
 */
function metricValues(page: Page) {
  return page.evaluate(() => {
    const root = getComputedStyle(document.documentElement)
    return Array.from(document.querySelectorAll<HTMLElement>('#main-content *'))
      .map((element) => {
        const source = `${element.getAttribute('style') ?? ''} ${element.getAttribute('class') ?? ''}`
        const token = source.match(/--text-(stat|display)-[a-z]+/)?.[0]
        if (!token || !element.textContent?.trim() || element.getBoundingClientRect().width === 0) return null
        return {
          text: element.textContent.trim().slice(0, 24),
          token,
          size: parseFloat(getComputedStyle(element).fontSize),
          tokenSize: parseFloat(root.getPropertyValue(token)),
        }
      })
      .filter((value): value is NonNullable<typeof value> => value !== null)
  })
}

test.describe('the presentation toggle at 1280 px', () => {
  test.use({ viewport: { width: 1280, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('toggles, grows the metric values onto the raised tokens, survives a reload, and turns off again', async ({ page }) => {
    const { api, errors } = await openRollout(page, '/overview', { handlers: OVERVIEW_ON, ready: overviewReady })
    // Phase D S1: the catalogue's headline row mounts on every load; read the
    // metric values once it has drawn, so the desk and room lists are one page.
    await expectDrawn(sectionFrame(page, 'overview-trend', 'Pass rate trend'), 'trend')
    await expectDrawn(sectionFrame(page, 'overview-donut', 'Status breakdown'), 'donut')
    const toggle = page.locator('aside').getByRole('button', { name: 'Presentation mode' })
    const html = page.locator('html')
    await expect(toggle).toHaveAttribute('aria-pressed', 'false')
    await expect(html).not.toHaveAttribute('data-presentation', /.*/)

    const desk = await metricValues(page)
    expect(desk.length, 'metric values on the Overview').toBeGreaterThan(0)
    expect(new Set(desk.map((v) => v.size)), 'desk sizes (26 px values, 22 px dashes)').toEqual(
      new Set(desk.map((v) => v.tokenSize)),
    )

    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-pressed', 'true')
    await expect(html).toHaveAttribute('data-presentation', 'on')
    const room = await metricValues(page)
    expect(room.map((v) => v.text)).toEqual(desk.map((v) => v.text))
    for (const [i, value] of room.entries()) {
      expect(value.size, `${value.text} (${value.token}) reads its raised token`).toBe(value.tokenSize)
      expect(value.size, `${value.text} grew`).toBeGreaterThan(desk[i].size)
      expect(value.size, `${value.text} is a room size`).toBeGreaterThanOrEqual(36)
    }
    // Quiet text is the secondary text colour (7:1 in every theme; the vitest recomputes the ratios).
    const colours = await page.evaluate(() => {
      const s = getComputedStyle(document.documentElement)
      const v = (name: string) => s.getPropertyValue(name).trim()
      return { muted: v('--color-text-muted'), faint: v('--color-text-faint'), axis: v('--chart-axis'), secondary: v('--color-text-secondary') }
    })
    expect(colours.muted).toBe(colours.secondary)
    expect(colours.faint).toBe(colours.secondary)
    expect(colours.axis).toBe(colours.secondary)

    // A preference of this browser: it survives a reload.
    await page.reload()
    await expect(overviewReady(page)).toBeVisible()
    await expect(html).toHaveAttribute('data-presentation', 'on')
    await expect(toggle).toHaveAttribute('aria-pressed', 'true')
    expect(await page.evaluate((key) => localStorage.getItem(key), STORAGE_KEY)).toContain('"enabled":true')

    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-pressed', 'false')
    await expect(html).not.toHaveAttribute('data-presentation', /.*/)
    expect((await metricValues(page)).map((v) => v.size)).toEqual(desk.map((v) => v.size))
    await networkQuiet(page, api)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })

  test('a stored "on" is on <html> before the app first puts content on the page (no flash of desk sizes)', async ({
    page,
  }) => {
    await seedPresentation(page)
    // Records the attribute at the moment the app first inserts content into #root.
    await page.addInitScript(() => {
      const w = window as unknown as { __atFirstContent?: string | null }
      const observer = new MutationObserver(() => {
        const root = document.getElementById('root')
        if (root && root.childNodes.length > 0 && !('__atFirstContent' in w)) {
          w.__atFirstContent = document.documentElement.getAttribute('data-presentation')
          observer.disconnect()
        }
      })
      observer.observe(document, { childList: true, subtree: true })
    })
    const { api, errors } = await openRollout(page, '/overview', { handlers: OVERVIEW_ON, ready: overviewReady })
    const atFirstContent = await page.evaluate(() => (window as unknown as { __atFirstContent?: string | null }).__atFirstContent)
    expect(atFirstContent, 'data-presentation when #root first got content').toBe('on')
    await expect(page.locator('aside').getByRole('button', { name: 'Presentation mode' })).toHaveAttribute('aria-pressed', 'true')
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })
})

// ── Every page, flags on, in presentation mode, at 1280 ─────────────────────

interface RoutePage {
  name: string
  path: string
  ready: (page: Page) => ReturnType<Page['locator']>
  handlers: ApiHandlers
  flags: FlagMap
  frames: number
}

const PAGES: RoutePage[] = [
  // Phase D S1: Overview asks no flag; its catalogue mounts unconditionally.
  { name: 'Overview', path: '/overview', ready: overviewReady, handlers: OVERVIEW_ON, flags: {}, frames: 4 },
  { name: 'Trends', path: '/trends', ready: (p) => landmark(p, 'Trend metrics'), handlers: TRENDS_ON, flags: HEATMAP_ON, frames: 6 },
  {
    name: 'Summary',
    path: '/reports/summary',
    ready: (p) => p.getByText('Total tests', { exact: true }),
    handlers: SUMMARY_REPORT_ON,
    flags: CATALOGUE_ON,
    frames: 4,
  },
  {
    name: 'Suite detail',
    path: `/coverage/suite?name=${SUITE}&days=30`,
    ready: (p) => p.getByRole('heading', { name: /^Run history/ }),
    handlers: SUITE_DETAIL_ON,
    flags: CATALOGUE_ON,
    frames: 2,
  },
  {
    name: 'Release gate',
    path: `/release-gate/${RUN_ID}`,
    ready: (p) => p.getByRole('meter', { name: 'Risk Score' }),
    handlers: releaseGateOn({ clusters: GATE_CLUSTERS }),
    flags: CATALOGUE_ON,
    frames: 2,
  },
]

test.describe('presentation mode, every catalogue section drawn (1280 x 4000)', () => {
  test.use({ viewport: { ...TALL_VIEWPORT }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  for (const report of PAGES) {
    test(`${report.name}: nothing overflows the scroller or its frame, axis text reads 16 px or more`, async ({ page }) => {
      await seedPresentation(page)
      const { api, errors } = await openRollout(page, report.path, {
        handlers: report.handlers,
        flags: report.flags,
        ready: report.ready,
      })
      await expect(page.locator('html')).toHaveAttribute('data-presentation', 'on')
      await networkQuiet(page, api)
      await expect(page.locator('[data-chart-frame]')).toHaveCount(report.frames)
      await expect
        // Compare (VIZ-605) with nothing chosen in the filter bar states it, by design: not-measured.
        .poll(async () => (await frameStates(page)).filter((s) => !/: (ready|truncated)$/.test(s) && s !== 'Compare: not-measured'), { timeout: 20_000 })
        .toEqual([])
      await expectNoErrorFrame(page)
      // Metric values sit on their raised tokens (Release gate has none: D10 of B4).
      for (const value of await metricValues(page)) {
        expect(value.size, `${value.text} (${value.token})`).toBe(value.tokenSize)
        expect(value.size, `${value.text} is a room size`).toBeGreaterThanOrEqual(36)
      }
      // The gate's one metric is the ring's number (R1-5, K2): the ring doubles
      // in presentation mode, so its value reads at a room size too.
      if (report.name === 'Release gate') {
        const risk = page.getByRole('meter', { name: 'Risk Score' }).locator('span').first()
        const px = parseFloat(await risk.evaluate((node) => getComputedStyle(node).fontSize))
        expect(px, 'the Risk Score number in presentation mode').toBeGreaterThanOrEqual(36)
      }
      await expectNoHorizontalOverflow(page, `${report.name} in presentation mode`)
      const escapes: string[] = []
      const ticks: string[] = []
      for (const frame of await page.locator('[data-chart-frame]').all()) {
        const title = (await frame.locator('h2, h3').first().textContent())?.trim()
        escapes.push(...(await frame.evaluate(textEscapes)).map((problem) => `${title}: ${problem}`))
        // On-screen tick size: computed size x the drawing's scale (the method of chart-fullscreen-text.spec.ts).
        for (const svg of await frame.locator(CHART_SVG).all()) {
          const smallest = await svg.evaluate((element) => {
            const box = element.getBoundingClientRect()
            const scale = box.width / ((element as SVGSVGElement).width.baseVal.value || box.width)
            const sizes = Array.from(element.querySelectorAll('.recharts-cartesian-axis-tick text'))
              .filter((text) => text.getBoundingClientRect().width > 0)
              .map((text) => parseFloat(getComputedStyle(text).fontSize) * scale)
            return sizes.length ? Math.min(...sizes) : null
          })
          if (smallest !== null && smallest < 15.9) ticks.push(`${title}: ${smallest.toFixed(1)} px`)
        }
      }
      expect(escapes, 'chart text escaping its frame in presentation mode').toEqual([])
      expect(ticks, 'axis ticks under 16 px on screen').toEqual([])
      expect(api.unhandled).toEqual([])
      expect(api.offHost).toEqual([])
      expect(errors).toEqual([])
    })
  }
})
