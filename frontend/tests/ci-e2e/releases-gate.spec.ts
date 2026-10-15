/**
 * W05 REL-01, hermetic (`docs/deferred-scope-2026-10-13/work/W05-contract.md` §2.2), on `/releases`:
 * a release shows its CURRENT recorded verdict, nothing computed in the browser.
 *
 *  - 2026.10 (released) reads Go with its tiles; 2026.11 (in progress) reads No-Go and lists the server's
 *    readable reasons (`test_pay (checkout-e2e) is FAILED`), three of them, then `+23 more` (25 reasons and
 *    a failed phase); its phases are its OWN, none of the old Smoke / Regression / E2E template;
 *  - the hotfix (in progress, never evaluated) reads "Not evaluated yet"; a QA lead's Evaluate gate POSTs
 *    `.../gate/evaluate`, the badge moves to Go with the recorded numbers, the card does not open, and the
 *    banner's "Ready to ship" counts it;
 *  - a blocker's "Open gate →" opens `/releases/{id}#gate` with the gate panel in view (scorecard, every
 *    reason, the baseline sentence) and each phase's live gate chip;
 *  - "Evaluate all in progress" evaluates both in-progress releases, one toast for all;
 *  - a QA engineer and a viewer never reach the page (a QA-lead route): Home, no release call, no button.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-releases.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout } from '../lib/rollout'
import { OVERVIEW_ON } from '../visual/production/fixtures'
import { QA_ENGINEER, RELEASE, releasesHandlers, VIEWER } from '../visual/production/fixtures-releases'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ready = (p: Page) => p.getByRole('heading', { name: 'Releases', level: 1 })
const card = (page: Page, name: string) => page.getByRole('article', { name: new RegExp(`^${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')} `) })
const badge = (page: Page, name: string) => card(page, name).locator('[data-gate-decision]').first()
const readyToShip = (page: Page) => page.locator('[data-release-health] [data-banner-fact]').filter({ hasText: 'Ready to ship' })

test('Go and No-Go are the recorded verdicts; Evaluate gate records the hotfix and the banner counts it', async ({ page }) => {
  const { handlers, writes } = releasesHandlers({ hotfix: true })
  const { api, errors } = await openRollout(page, '/releases', { handlers, ready })

  // The released release: its recorded GO and the numbers it was decided on.
  await expect(badge(page, '2026.10')).toHaveText('Go')
  const shipped = card(page, '2026.10')
  await expect(shipped.locator('[data-gate-tile="Pass rate"]')).toContainText('99.2%')
  await expect(shipped.locator('[data-gate-tile="Evidence"]')).toContainText('of 250 in scope')

  // The in-progress NO_GO: the server's readable reasons, three, then the count of the rest.
  const nogo = card(page, '2026.11')
  await expect(badge(page, '2026.11')).toHaveText('No-Go')
  await expect(nogo.getByText('test_pay (checkout-e2e) is FAILED')).toBeVisible()
  await expect(nogo.getByText(/test_checkout_step_02 \(checkout-e2e\) is FAILED/)).toBeVisible()
  await expect(nogo.getByText(/test_checkout_step_03/)).toHaveCount(0)
  await expect(nogo.locator('[data-blockers-more]')).toHaveText('+23 more · open the release for the full list')
  // Its own phases, never the six-bucket template.
  for (const phase of ['Build verification', 'Payments soak', 'Customer acceptance']) {
    await expect(nogo.getByText(phase, { exact: true })).toBeVisible()
  }
  for (const bucket of ['Smoke', 'Regression', 'E2E', 'Perf', 'UAT', 'Sign']) {
    await expect(nogo.getByText(bucket, { exact: true })).toHaveCount(0)
  }

  // The hotfix was never evaluated: no tiles, no ready-to-ship.
  const hotfix = card(page, '2026.11.1')
  await expect(badge(page, '2026.11.1')).toHaveText('Not evaluated yet')
  await expect(hotfix.locator('[data-gate-tile]')).toHaveCount(0)
  await expect(readyToShip(page)).toHaveText('Ready to ship 0')

  await hotfix.getByTestId('release-evaluate').click()

  await expect(badge(page, '2026.11.1')).toHaveText('Go')
  await expect(page.getByText('2026.11.1: Go — pass rate 97.5% over 40 tests')).toBeVisible()
  await expect(hotfix.locator('[data-gate-tile="Pass rate"]')).toContainText('97.5%')
  await expect(hotfix.locator('[data-gate-tile="Runs"]')).toContainText('3')
  await expect(hotfix.locator('[data-gate-evaluated]')).toHaveText('Evaluated just now')
  await expect(hotfix.getByTestId('release-evaluate')).toHaveText('Re-evaluate')
  await expect(readyToShip(page)).toHaveText('Ready to ship 1')
  // The button is not the card: no side panel opened.
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(writes).toEqual([{ line: `POST /api/v1/releases/${RELEASE.hotfix}/gate/evaluate`, body: null }])
  assertHermetic(api, errors)
})

test('a No-Go blocker opens /releases/{id}#gate: the panel in view with every reason, the baseline and the phase gates', async ({ page }) => {
  const { handlers } = releasesHandlers()
  const { api, errors } = await openRollout(page, '/releases', { handlers, ready })

  await card(page, '2026.11').getByRole('link', { name: /Open gate/ }).first().click()

  await expect(page).toHaveURL(new RegExp(`/releases/${RELEASE.active}#gate$`))
  const gate = page.locator('#gate')
  await expect(gate).toBeInViewport()
  await expect(gate.getByRole('heading', { name: 'Release gate' })).toBeVisible()
  await expect(gate.locator('[data-gate-decision]')).toHaveText('No-Go')
  await expect(gate.locator('[data-gate-scorecard]')).toContainText('94.0%')
  await expect(gate.locator('[data-gate-status-counts]')).toHaveText('Passed 376 · Failed 22 · Broken 2')
  // Every reason, readable: the display strings beside the stored fingerprints.
  const blocking = gate.getByRole('list', { name: 'Blocking' }).getByRole('listitem')
  await expect(blocking).toHaveCount(25)
  await expect(blocking.first()).toHaveText('test_pay (checkout-e2e) is FAILED')
  await expect(gate.getByText(/::\d{4}a1b2c3d4e5f6/)).toHaveCount(0)
  await expect(gate.locator('[data-baseline-deltas]')).toContainText('Pass rate -5.2 pp vs 2026.10')
  // Each phase's live gate (a preview: nothing recorded).
  const chips = page.getByTestId('phase-gate-chip')
  await expect(chips).toHaveCount(3)
  await expect(chips.nth(0)).toHaveAttribute('data-verdict', 'GO')
  await expect(chips.nth(1)).toHaveAttribute('data-verdict', 'NO_GO')
  await expect(chips.nth(1)).toHaveAttribute('title', 'test_pay (checkout-e2e) is FAILED')
  await expect(chips.nth(2)).toHaveAttribute('data-verdict', 'NOT_EVALUATED')
  await expect(chips.nth(2)).toHaveAttribute('title', 'Live preview — nothing is recorded')
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('Evaluate all in progress evaluates every in-progress release, one toast for all', async ({ page }) => {
  const { handlers, writes } = releasesHandlers({ hotfix: true })
  const { api, errors } = await openRollout(page, '/releases', { handlers, ready })

  await page.getByTestId('release-evaluate-all').click()

  await expect(page.getByText('Evaluated 2 releases: 1 Go, 1 No-Go, 0 not evaluated.')).toBeVisible()
  await expect(badge(page, '2026.11.1')).toHaveText('Go')
  await networkQuiet(page, api)
  expect(writes.map((w) => w.line).sort()).toEqual(
    [`POST /api/v1/releases/${RELEASE.active}/gate/evaluate`, `POST /api/v1/releases/${RELEASE.hotfix}/gate/evaluate`].sort(),
  )
  assertHermetic(api, errors)
})

// `/releases` is a management route (QA lead and ADMIN, `App.tsx` `managementRoutes`): a QA engineer or a
// viewer who opens it lands on Home. So no lower role ever sees an Evaluate button, and none makes a call the
// release page makes (gate, packs, activity, members, an evaluation) that could answer it 403. The page's own
// role gating (a verdict for everyone, buttons for a QA lead) is pinned by `ReleasesPage.roles.test.tsx`.
for (const user of [QA_ENGINEER, VIEWER]) {
  test(`a ${user.role}: /releases is a QA lead's page; Home, no release call, no Evaluate`, async ({ page }) => {
    const { handlers, writes } = releasesHandlers({ hotfix: true })
    const dashboard = (p: Page) => p.getByRole('heading', { level: 1, name: 'Dashboard' })
    const { api, errors } = await openRollout(page, '/overview', { handlers: [...OVERVIEW_ON, ...handlers], ready: dashboard, user })
    // Typed in (a bookmark): the route guard sends a lower role home.
    await page.goto('/releases')
    await expect(page).toHaveURL(/\/overview$/)
    await expect(dashboard(page)).toBeVisible()
    await expect(page.getByTestId('release-evaluate')).toHaveCount(0)
    await networkQuiet(page, api)
    expect(writes).toEqual([])
    const releaseCalls = api.seen.filter((line) => /\/gate|\/compliance-packs|\/activity|\/members|\/releases\/[^/?]+/.test(line))
    expect(releaseCalls, 'no call of the release page is made').toEqual([])
    await expect(page.getByText(/403|Requires at least|not allowed/i)).toHaveCount(0)
    assertHermetic(api, errors)
  })
}
