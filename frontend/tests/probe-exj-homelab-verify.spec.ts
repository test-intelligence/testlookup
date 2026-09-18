/**
 * Live verification of the EXJ-2026-09-18 UI fixes against the homelab.
 *
 * Run it BEFORE the deploy as well as after. A verification probe that has
 * never been seen to fail is not evidence: "passes after deploy" and "cannot
 * detect the difference" look identical from the outside.
 *
 *   npx playwright test --config probe-live.config.ts probe-exj-homelab-verify
 *
 * Read-only. It navigates, clicks a pipeline card to expand the /agents detail
 * column, and measures. It submits nothing and creates nothing.
 */
import { expect, test, type Page } from '@playwright/test'

const BASE = 'http://testlookup.local'
const USER = 'admin'
const PASS = 'Admin@2026!'

/** A homelab run that carries a completed pipeline. */
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

test.describe('EXJ-2026-09-18 fixes, live', () => {
  test('BUG-005: the pass rate is readable and Build does not eat the row', async ({ page, request }) => {
    test.setTimeout(3 * 60 * 1000)
    await signIn(page, request)
    await page.setViewportSize({ width: 1920, height: 1080 })
    await page.goto(`${BASE}/intelligence`, { waitUntil: 'domcontentloaded' })
    await expect(page.locator('table tbody tr').first()).toBeVisible({ timeout: 30_000 })
    await page.waitForTimeout(1500)

    const geo = await page.evaluate(() => {
      const headers = [...document.querySelectorAll('th')]
      const buildTh = headers.find((th) => (th.textContent || '').trim().startsWith('Build'))
      if (!buildTh) throw new Error('Recent runs analyzed table not found')
      const row = buildTh.parentElement as HTMLTableRowElement
      const table = buildTh.closest('table') as HTMLTableElement
      const passIndex = [...row.children].findIndex((th) =>
        (th.textContent || '').toLowerCase().includes('pass rate'))
      const body = table.querySelector('tbody tr') as HTMLTableRowElement
      const cell = body.children[passIndex] as HTMLTableCellElement
      const meter = cell.firstElementChild as HTMLElement
      return {
        table: table.getBoundingClientRect().width,
        build: (row.children[0] as HTMLElement).getBoundingClientRect().width,
        container: (table.parentElement as HTMLElement).getBoundingClientRect().width,
        passClient: cell.clientWidth,
        passNeeds: meter ? meter.scrollWidth + 28 : 0,
      }
    })
    // eslint-disable-next-line no-console
    console.log(`LIVE_GEO table=${Math.round(geo.table)} build=${Math.round(geo.build)} ` +
      `share=${((geo.build / geo.table) * 100).toFixed(1)}% fill=${((geo.table / geo.container) * 100).toFixed(1)}% passClient=${geo.passClient} passNeeds=${Math.round(geo.passNeeds)}`)

    expect(geo.passNeeds, 'the pass-rate percentage is clipped').toBeLessThanOrEqual(geo.passClient + 28)
    expect(geo.build / geo.table, 'Build is absorbing the row').toBeLessThan(0.5)
    // The follow-up defect: capping the table stopped Build growing but left
    // the table short of its panel, moving the empty band to the right edge.
    const fill = geo.table / geo.container
    expect(fill, 'the table overflows its panel').toBeLessThanOrEqual(1.01)
    expect(fill, 'the table falls short of its panel — empty band at the right edge').toBeGreaterThan(0.97)
  })

  test('BUG-008: the AI report leads and Agent Stages collapses', async ({ page, request }) => {
    test.setTimeout(3 * 60 * 1000)
    await signIn(page, request)
    // Deep-link to a run that HAS a pipeline, so the list is populated. The
    // default project may have none, and an empty list renders "Select a
    // pipeline run to see agent stages" — which would fail this test for a
    // reason that has nothing to do with the fix.
    await page.goto(`${BASE}/agents/run/${RUN_WITH_PIPELINE}`, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(3000)

    // The detail column only renders once a pipeline is selected.
    //
    // The locator is deliberately narrow. `/pipeline/i` also matches the
    // page's **"Trigger pipeline"** button, and this probe runs against the
    // real deployment — a looser match would have started a pipeline run on
    // the homelab rather than reading one. It timed out instead of clicking,
    // which was luck, not design.
    const card = page.getByRole('button', { name: /(offline|deep|live|investigation) pipeline/i }).first()
    await expect(card, 'no pipeline card to select').toBeVisible({ timeout: 25_000 })
    await card.click()
    await page.waitForTimeout(3000)

    const report = page.getByRole('heading', { name: 'AI Report' })
    const stages = page.getByRole('heading', { name: 'Agent Stages' })
    await expect(report, 'no "AI Report" heading — the deploy does not carry this change')
      .toBeVisible({ timeout: 20_000 })
    await expect(stages).toBeVisible({ timeout: 20_000 })

    const reportFirst = await page.evaluate(() => {
      const hs = [...document.querySelectorAll('h3')]
      const r = hs.find((h) => (h.textContent || '').trim() === 'AI Report')
      const s = hs.find((h) => (h.textContent || '').trim() === 'Agent Stages')
      if (!r || !s) return null
      return (r.compareDocumentPosition(s) & 4) !== 0
    })
    expect(reportFirst, 'the AI report must come before Agent Stages').toBe(true)

    const toggle = page.getByRole('button', { name: /hide stages|show stages/i })
    await expect(toggle, 'Agent Stages has no collapse control').toBeVisible({ timeout: 10_000 })
    await toggle.click()
    await page.waitForTimeout(800)
    await expect(report, 'collapsing the stages hid the report too').toBeVisible()
  })
})
