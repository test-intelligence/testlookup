/**
 * BUG-011 live verification: the run dropdown on the agents page states its purpose.
 *
 * Run it BEFORE the deploy as well as after. A verification probe that has never
 * been seen to fail is not evidence.
 *
 *   npx playwright test --config probe-live.config.ts probe-bug011-agents-trigger
 *
 * Expected BEFORE the deploy: the select is present but carries no visible
 * label, so the label assertions fail while the "control exists" assertion
 * passes. That split matters — it proves the probe reached the right control and
 * is measuring the fix, not the absence of the page.
 *
 * Why this defect needed a live probe at all: the same page was reported once
 * before (TL-2026-09-18-01-009), fixed, verified only by unit test, and never
 * checked against the deployment. The fix was aimed at a different control -- a
 * pipeline card, not this dropdown -- so it looked done and the user reported it
 * again. A unit test cannot tell you that you fixed the wrong thing; a live
 * check against the reported surface can.
 *
 * Read-only: it navigates, reads, and changes a select value. It never submits
 * the form, so no pipeline is triggered on the homelab.
 */
import { expect, test, type Page } from '@playwright/test'

const BASE = 'http://testlookup.local'
const USER = 'admin'
const PASS = 'Admin@2026!'

/** A homelab run that carries a completed pipeline, so the list is populated. */
const RUN_WITH_PIPELINE = '109dd6f7-ae42-4317-96e4-a01b1a328ab5'

async function signIn(page: Page, request: import('@playwright/test').APIRequestContext) {
  const login = await request.post(`${BASE}/api/v1/auth/login`, {
    form: { username: USER, password: PASS },
  })
  expect(login.ok(), 'login must succeed before verifying').toBeTruthy()
  const auth = await login.json()
  await page.addInitScript(
    ([token, refresh]) => {
      localStorage.setItem('auth-storage', JSON.stringify({
        state: {
          token,
          refreshToken: refresh,
          user: { username: 'admin', role: 'ADMIN', email: 'admin@testlookup.local' },
          isAuthenticated: true,
        },
        version: 0,
      }))
    },
    [auth.access_token, auth.refresh_token ?? ''] as const,
  )
}

test.describe('BUG-011: the trigger dropdown announces itself, live', () => {
  test('the run dropdown carries a visible label saying it does not change the view', async ({ page, request }) => {
    test.setTimeout(3 * 60 * 1000)
    await signIn(page, request)
    await page.setViewportSize({ width: 1600, height: 1000 })
    await page.goto(`${BASE}/agents/run/${RUN_WITH_PIPELINE}`, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(3000)

    // Locate the control by what it did BEFORE the fix. If this fails, the probe
    // never reached the dropdown and the label assertions below would be
    // meaningless rather than informative -- fail closed here instead.
    const select = page.locator('select[aria-label*="trigger" i]').first()
    await expect(
      select,
      'the manual-trigger run dropdown is not on the page — this probe is not ' +
        'measuring what it claims to (wrong role, empty run list, or the control moved)',
    ).toBeVisible({ timeout: 25_000 })

    const body = await page.locator('body').innerText()
    // eslint-disable-next-line no-console
    console.log(`LIVE_BUG011 hasLabel=${/trigger a pipeline manually/i.test(body)} ` +
      `hasDisclaimer=${/does not change the view below/i.test(body)}`)

    expect(
      body,
      'the dropdown has no visible label, so it still reads as "pick which ' +
        'pipeline run to view" — the exact misreading that produced two reports',
    ).toMatch(/trigger a pipeline manually/i)

    expect(
      body,
      'the label must say selecting does not change the view; without that a ' +
        'user still expects the panel below to refresh',
    ).toMatch(/does not change the view below/i)
  })

  test('selecting a run in it still does not change the displayed pipeline', async ({ page, request }) => {
    test.setTimeout(3 * 60 * 1000)
    await signIn(page, request)
    await page.setViewportSize({ width: 1600, height: 1000 })
    await page.goto(`${BASE}/agents/run/${RUN_WITH_PIPELINE}`, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(3000)

    const select = page.locator('select[aria-label*="trigger" i]').first()
    await expect(select).toBeVisible({ timeout: 25_000 })

    const options = await select.locator('option').count()
    // The first option is the "— Pick a … —" placeholder, so a real run needs 2+.
    test.skip(options < 2, 'no selectable runs on this deployment')

    const before = await page.locator('body').innerText()
    await select.selectOption({ index: 1 })
    await page.waitForTimeout(2500)
    const after = await page.locator('body').innerText()

    // Behaviour is unchanged by this fix, and that is the point: the control
    // arms the trigger button. If the detail panel starts changing here, the
    // control has silently become a view selector and the label is now false.
    const stripSelect = (s: string) => s.replace(/\s+/g, ' ').trim()
    expect(
      stripSelect(after) === stripSelect(before),
      'the page content changed when the trigger dropdown was used — either the ' +
        'control became a view selector (label now wrong) or something else ' +
        're-rendered; both need a look',
    ).toBeTruthy()

    // And nothing was submitted: no pipeline should have been queued.
    expect(after).not.toMatch(/pipeline queued/i)
  })
})
