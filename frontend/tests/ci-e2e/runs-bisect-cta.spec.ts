/**
 * W23b FAIL-12 (D-7, D-8) on `/runs`, hermetic (`docs/deferred-scope-2026-10-13/work/W23b-contract.md` §2.1 F-3, §4,
 * §6.3), at 1440 × 900:
 *
 *  - the banner's `Bisect from last green` navigates to `/runs/<latest failed>?tab=changes&bisect=top` (whole value)
 *    and the narrowing opens there on the run's first new failure (the URL rewritten to its fingerprint);
 *  - a window with failures but no green run: the action reads `Bisect the latest failure`, same target;
 *  - the Build history callout names the last green BUILD (`#238` in its `Last green` tile) — never the run's UUID
 *    prefix printed as a SHA, never "Jenkins".
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). World: `fixtures-bisect-drift.ts` with `RUNS_PAGE` as
 * its base (the run page's answers come from the world's `RUN_PAGE`).
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout } from '../lib/rollout'
import { RUN_ID, RUNS_PAGE, runListOf, runs } from '../visual/production/fixtures'
import { bisectDriftWorld, worldNarrowing, worldNewFailures } from '../visual/production/fixtures-bisect-drift'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const banner = (p: Page) => p.locator('[data-status-banner]')
const TARGET = `/runs/${RUN_ID}?tab=changes&bisect=top`

/** Record every history push the SPA makes (React Router's navigate), to read the CTA's exact target. */
async function recordPushes(page: Page) {
  await page.evaluate(() => {
    const w = window as unknown as { __pushed: string[] }
    w.__pushed = []
    const push = history.pushState.bind(history)
    history.pushState = (data: unknown, unused: string, url?: string | URL | null) => {
      w.__pushed.push(String(url))
      return push(data, unused, url)
    }
  })
}
const pushes = (page: Page) => page.evaluate(() => (window as unknown as { __pushed: string[] }).__pushed)

function ctaWorld(base = RUNS_PAGE) {
  const first = worldNewFailures()[0]
  return { first, world: bisectDriftWorld({ base, answers: { [first.test_fingerprint]: worldNarrowing() } }) }
}

async function expectNarrowingOpens(page: Page, fingerprint: string, testName: string) {
  await expect(page).toHaveURL(new RegExp(`/runs/${RUN_ID}\\?`))
  const row = page.getByRole('region', { name: 'What changed since the last good run' }).locator('li').filter({ hasText: testName })
  await expect(row.locator('[data-narrow-box]').getByRole('region', { name: 'Narrowed range' }).locator('[data-bisect-state]'))
    .toHaveText('Last passed in build #236 (goodsen), first failed in build #239 (badsent).')
  await expect.poll(() => new URL(page.url()).searchParams.get('bisect')).toBe(fingerprint)
  expect(new URL(page.url()).searchParams.get('tab')).toBe('changes')
}

test('"Bisect from last green" opens the latest failed run\'s Changes tab with the narrowing', async ({ page }) => {
  const { first, world } = ctaWorld()
  const { api, errors } = await openRollout(page, '/runs', { handlers: world.handlers, ready: banner, user: world.user })
  await networkQuiet(page, api)
  await recordPushes(page)
  await banner(page).getByRole('button', { name: /^Bisect from last green/ }).click()
  await expectNarrowingOpens(page, first.test_fingerprint, first.test_name)
  expect((await pushes(page))[0]).toBe(TARGET)
  await networkQuiet(page, api)
  expect(world.seen).toEqual([first.test_fingerprint])
  assertHermetic(api, errors)
})

test('a window with failures but no green run: "Bisect the latest failure", same target', async ({ page }) => {
  const failedOnly = runListOf(() => runs().filter((r) => r.status === 'FAILED'), () => null)
  const { first, world } = ctaWorld([['/api/v1/runs', failedOnly], ...RUNS_PAGE])
  const { api, errors } = await openRollout(page, '/runs', { handlers: world.handlers, ready: banner, user: world.user })
  await networkQuiet(page, api)
  await expect(banner(page).getByRole('button', { name: /^Bisect from last green/ })).toHaveCount(0)
  await recordPushes(page)
  await banner(page).getByRole('button', { name: /^Bisect the latest failure/ }).click()
  await expectNarrowingOpens(page, first.test_fingerprint, first.test_name)
  expect((await pushes(page))[0]).toBe(TARGET)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('the Build history callout shows the last green build, never a UUID prefix as a SHA', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/runs', { handlers: RUNS_PAGE, ready: banner })
  await networkQuiet(page, api)
  await page.getByRole('button', { name: /^Build history/ }).click()
  const callout = page.getByRole('region', { name: 'Last green build' })
  const lastGreen = runs().find((r) => r.status === 'PASSED')
  expect(lastGreen?.build_number).toBe(238)
  await expect(callout).toContainText('The last green run is #238')
  await expect(callout).toContainText('Bisect narrows each failing test to its own last pass.')
  const tile = callout.locator('[data-metric-card]').filter({ hasText: 'Last green' })
  await expect(tile).toHaveText(/^Last green\s*#238$/)
  await expect(callout).not.toContainText(String(lastGreen?.id).slice(0, 7))
  await expect(callout).not.toContainText(/SHA|Jenkins/)
  await expect(callout.getByRole('button', { name: 'Start bisect' })).toBeVisible()
  assertHermetic(api, errors)
})
