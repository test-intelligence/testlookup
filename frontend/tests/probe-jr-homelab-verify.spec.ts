/**
 * Live verification of the JR-02..JR-06 fixes against the homelab.
 *
 * Run it BEFORE the deploy as well as after. A verification probe that has never
 * been seen to fail is not evidence: "passes after deploy" and "cannot detect
 * the difference" look identical from the outside.
 *
 *   npx playwright test --config probe-live.config.ts probe-jr-homelab-verify
 *
 * Expected BEFORE the deploy:
 *   BUG-009  fails — 14 of 23 settings sub-pages have no way back
 *   TL-003   fails — run ac670406 returns CONDITIONAL_GO with conditions_for_go: []
 *
 * Read-only. It navigates and reads; it submits nothing and creates nothing. The
 * release-gate check is a GET, and `/release-readiness` does not mutate.
 *
 * TL-2026-09-19-01-001 (the seeder pass-rate rule) is deliberately NOT checked
 * here. Deploying does not re-seed, and the homelab's existing rows keep the
 * values they were written with, so there is nothing a live probe could observe.
 * Claiming otherwise would be decoration.
 */
import { expect, test, type Page } from '@playwright/test'

const BASE = 'http://testlookup.local'
const USER = 'admin'
const PASS = 'Admin@2026!'

/**
 * A homelab run that reproduced the defect pre-deploy: CONDITIONAL_GO with an
 * empty conditions list, at composite 13.0. The verdict comes from the *band
 * floor* downgrading a GO rather than from the composite landing in the
 * conditional band, which is the case most likely to be missed — the fix must
 * read the recommendation AFTER `_apply_band_floor`, not before.
 */
const RUN_CONDITIONAL_GO = 'ac670406-bf13-4f47-88b1-f9483222a7b5'

/**
 * Sub-pages to check. The first four had **no** back affordance at all before
 * the fix; the last two had a `btn-secondary` "Back" button that was replaced.
 * Both groups matter: one proves the gap closed, the other proves the old
 * control was removed rather than duplicated.
 */
const SUB_PAGES = [
  '/settings/profile',
  '/settings/notifications',
  '/settings/audit',
  '/settings/api-keys',
  '/settings/ai',
  '/settings/storage',
] as const

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

test.describe('JR-02..JR-06 fixes, live', () => {
  test('BUG-009: every settings sub-page offers exactly one way back, the index none', async ({ page, request }) => {
    test.setTimeout(5 * 60 * 1000)
    await signIn(page, request)
    await page.setViewportSize({ width: 1600, height: 1000 })

    const missing: string[] = []
    const duplicated: string[] = []
    const staleButton: string[] = []

    for (const path of SUB_PAGES) {
      await page.goto(`${BASE}${path}`, { waitUntil: 'domcontentloaded' })
      // The bar is layout-level, so it renders before the page's own data
      // arrives; still wait for the shell so a slow chunk is not read as absent.
      await page.waitForTimeout(1500)

      // Scoped to the breadcrumb landmark, NOT to `a[href="/settings"]`.
      //
      // The first draft of this probe used the bare href and reported
      // `missing=[]` against a deployment where 14 of 23 pages had nothing —
      // because the **sidebar** carries its own Settings nav link with the same
      // href. Every page matched once from the sidebar alone, and the two pages
      // that also had the old button matched twice, so the probe measured the
      // sidebar and called it a back affordance. After the fix every page would
      // have matched twice and the duplicate check would have failed on a
      // correct deployment.
      const count = await page.locator('nav[aria-label="Breadcrumb"] a[href="/settings"]').count()
      if (count === 0) missing.push(path)
      else if (count > 1) duplicated.push(`${path} (${count})`)

      // The old Pattern-A control must be gone, not sitting beside the new one.
      const oldButton = await page.locator('a.btn-secondary[href="/settings"]').count()
      if (oldButton > 0) staleButton.push(path)
    }

    // eslint-disable-next-line no-console
    console.log(`LIVE_SETTINGS missing=[${missing.join(', ')}] ` +
      `duplicated=[${duplicated.join(', ')}] staleButton=[${staleButton.join(', ')}]`)

    expect(missing, 'these settings sub-pages have no way back to /settings').toEqual([])
    expect(duplicated, 'a page-level copy is rendering beside the layout one').toEqual([])
    expect(staleButton, 'the old per-page "Back" button is still rendered').toEqual([])

    // The index is the destination — a link to itself there is a loop.
    await page.goto(`${BASE}/settings`, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(1500)
    const onIndex = await page.locator('nav[aria-label="Breadcrumb"] a[href="/settings"]').count()
    expect(onIndex, 'the settings index must not offer "Back to Settings"').toBe(0)
  })

  test('TL-003: a CONDITIONAL_GO states what it is conditional on', async ({ page, request }) => {
    test.setTimeout(3 * 60 * 1000)
    const login = await request.post(`${BASE}/api/v1/auth/login`, {
      form: { username: USER, password: PASS },
    })
    expect(login.ok()).toBeTruthy()
    const token = (await login.json()).access_token

    const res = await request.get(
      `${BASE}/api/v1/release-readiness/${RUN_CONDITIONAL_GO}`,
      { headers: { Authorization: `Bearer ${token}` } },
    )
    expect(res.ok(), 'release-readiness must answer').toBeTruthy()
    const decision = await res.json()

    // eslint-disable-next-line no-console
    console.log(`LIVE_GATE recommendation=${decision.recommendation} ` +
      `composite=${decision.composite_risk} conditions=${(decision.conditions_for_go || []).length}`)

    // Fail closed rather than skip: if this run stopped being non-GO the probe
    // has lost its subject and must say so, not pass quietly.
    expect(
      decision.recommendation,
      'this run is no longer a non-GO verdict — the probe has lost its subject ' +
        'and is no longer testing anything',
    ).not.toBe('GO')

    const conditions: string[] = decision.conditions_for_go || []
    expect(
      conditions.length,
      'a CONDITIONAL_GO with no conditions hides the conditions block in ' +
        'ReleaseGatePage entirely — the verdict says "conditional" and the page ' +
        'offers nothing to satisfy',
    ).toBeGreaterThan(0)

    // The provisional condition must name that the risk was unmeasured, not
    // merely suggest an action. "not measured" is the difference between low
    // risk and unknown risk.
    expect(conditions.join(' ')).toContain('not measured')

    // And the UI must actually render them, not just receive them.
    await signIn(page, request)
    await page.goto(`${BASE}/release-gate/${RUN_CONDITIONAL_GO}`, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(4000)
    const body = (await page.locator('body').innerText()).toLowerCase()
    expect(
      body,
      'the conditions reached the API but the page does not show them',
    ).toContain('not measured')
  })
})
