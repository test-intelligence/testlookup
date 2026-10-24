/**
 * W11 GATE-04 on `/trends` and `/overview`, hermetic (`docs/deferred-scope-2026-10-13/work/W11-contract.md` §2.6, §4.1,
 * §4.2): one rule everywhere — the GO line is the policy's `yellow_min`, below `orange_min` is the No-Go zone.
 *
 *  - `/trends` with the summary naming Checkout's project policy "Payments gate" v4 (bands 92 / 97 / 99): the pass-rate
 *    hero's legend reads `GO ≥ 97%` and `No-Go < 92%` (never `Target 90%`), the takeaway names the source
 *    `(97%, Payments gate v4)`, the policy's NAME is not in the legend (§4.1: the legend must not wrap on DejaVu), and
 *    the page asks exactly what it asked before (the bands ride `/metrics/summary`; no `/effective` read);
 *  - `/trends` with no `readiness_policy` (an old cache entry, no evidence): the default bands, labelled —
 *    `GO ≥ 95%`, `No-Go < 90%`, `(95%, default bands)`;
 *  - `/overview` with the same policy and a P0 hard cap that forced No-Go: **How this verdict is decided** (closed on
 *    load, its summary line unchanged) names `Decided by Payments gate v4 (project policy): …`, links **Open policies**
 *    to the policy, and lists `Open critical (P0) defects: 2, cap 0` under `A hard cap forced No-Go:`;
 *  - `#main-content` never scrolls sideways at 1280 and 1440; the legend's line count is printed (`INPAGE`).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-release-planning.ts`.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { expectNoOverlay } from '../lib/inPage'
import { expectMainFits, inPageLine } from '../lib/inPageGeometry'
import { landmark } from '../lib/production-pages'
import { assertHermetic, expectDrawn, networkQuiet, openRollout, requestsTo, sectionFrame } from '../lib/rollout'
import { TRENDS_ON } from '../visual/production/fixtures'
import {
  DECIDED_BY,
  overviewPolicyWorld,
  P0_DOWNGRADE,
  PAYMENTS_POLICY,
  QA_LEAD,
  trendsPolicyWorld,
} from '../visual/production/fixtures-release-planning'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const trendsReady = (p: Page) => landmark(p, 'Trend metrics')
const hero = (page: Page) => sectionFrame(page, 'trends-pass-rate', 'Pass rate trend')

/**
 * The legend entries' rows: how many distinct lines the hero's legend takes (1 = it did not wrap), and the room left
 * on its row (the legend's width minus the span its entries take), which DejaVu Sans on CI eats first.
 */
async function legendLines(frame: Locator): Promise<{ lines: number; slack: number | null; entries: string[] }> {
  return frame.evaluate((root) => {
    const legend = root.querySelector('[data-chart-legend]')
    const items = Array.from(root.querySelectorAll('[data-chart-legend] li'))
    const tops = new Set(items.map((el) => Math.round(el.getBoundingClientRect().top)))
    const boxes = items.map((el) => el.getBoundingClientRect())
    const span = boxes.length ? Math.max(...boxes.map((b) => b.right)) - Math.min(...boxes.map((b) => b.left)) : 0
    const slack = legend ? Math.round(legend.getBoundingClientRect().width - span) : null
    return { lines: tops.size, slack, entries: items.map((el) => (el.textContent ?? '').trim()) }
  })
}

for (const width of [1280, 1440]) {
  test.describe(`at ${width} px`, () => {
    test.use({ viewport: { width, height: 1000 } })

    test(`/trends with a project policy: GO ≥ 97%, No-Go < 92%, the takeaway names it, no new request — ${width}`, async ({ page }) => {
      const { api, errors } = await openRollout(page, '/trends', { handlers: trendsPolicyWorld(), ready: trendsReady, user: QA_LEAD })
      const frame = hero(page)
      await expectDrawn(frame, 'pass rate')
      await networkQuiet(page, api)
      await expect(frame).toContainText('GO ≥ 97%')
      await expect(frame).toContainText('No-Go < 92%')
      await expect(frame).not.toContainText('Target 90%')
      await expect(frame).toContainText(`(97%, ${PAYMENTS_POLICY.name} v${PAYMENTS_POLICY.version})`)
      await expect(frame.getByText(/the GO line \(97%, Payments gate v4\)/)).toHaveCount(1)
      const legend = await legendLines(frame)
      inPageLine('/trends pass-rate legend', width, legend)
      expect(legend.entries.some((e) => e.includes(PAYMENTS_POLICY.name)), 'the policy name is in the takeaway, not the legend').toBe(false)
      // The bands ride the summary the page already reads: nothing new on the wire.
      expect(requestsTo(api, /\/release-gate-policies\//)).toEqual([])
      expect(requestsTo(api, '/api/v1/metrics/summary').length).toBe(1)
      await expectMainFits(page, `/trends @${width}`)
      assertHermetic(api, errors)
    })
  })
}

test('/trends with no readiness_policy: the default bands, labelled', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  const { api, errors } = await openRollout(page, '/trends', { handlers: TRENDS_ON, ready: trendsReady, user: QA_LEAD })
  const frame = hero(page)
  await expectDrawn(frame, 'pass rate')
  await expect(frame).toContainText('GO ≥ 95%')
  await expect(frame).toContainText('No-Go < 90%')
  await expect(frame).toContainText('(95%, default bands)')
  await expect(frame).not.toContainText('Target 90%')
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

for (const width of [1280, 1440]) {
  test(`/overview: How this verdict is decided names the policy, links it, lists the P0 cap — ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 })
    const { api, errors } = await openRollout(page, '/overview', {
      handlers: overviewPolicyWorld(),
      ready: (p) => p.getByRole('heading', { level: 1, name: 'Dashboard' }),
      user: QA_LEAD,
    })
    await networkQuiet(page, api)
    const toggle = page.getByRole('button', { name: /^How this verdict is decided/ })
    await expect(toggle, 'closed on load').toHaveAttribute('aria-expanded', 'false')
    await expect(page.getByText(DECIDED_BY, { exact: true }), 'nothing of it before it is opened').toHaveCount(0)
    await toggle.click()
    const details = page.getByTestId('verdict-details')
    await expect(details.getByText(DECIDED_BY, { exact: true })).toBeVisible()
    const open = details.getByRole('link', { name: 'Open policies', exact: true })
    await expect(open).toBeVisible()
    expect(new URL((await open.getAttribute('href')) ?? '', page.url()).pathname).toBe(`/policies/${PAYMENTS_POLICY.id}`)
    await expect(details.getByText('A hard cap forced No-Go:', { exact: true })).toBeVisible()
    await expect(details.getByText(P0_DOWNGRADE.text, { exact: true })).toBeVisible()
    await expect(details).not.toContainText(P0_DOWNGRADE.token)
    expect(requestsTo(api, /\/release-gate-policies\//), 'the policy rides the summary').toEqual([])
    await expectMainFits(page, `/overview @${width}`)
    await expectNoOverlay(page)
    assertHermetic(api, errors)
  })
}
