/**
 * Focused exploratory probe: FAILURE ANALYSIS.
 *
 * Oracle from the seeded data (project "Checkout Service"):
 *   test_currency_rounding  -> ingested as <error>  => infra / BROKEN, NOT a product bug
 *   test_discount_stacking  -> 4 consecutive fails  => product bug / regression
 *
 * Hunts this repo's documented classes in this area specifically:
 *   * fabricated confidence values presented as real
 *   * AI output stated as fact rather than suggestion
 *   * provenance missing from the analysis contract (Epic 15)
 *   * silent 422 / empty page with no error
 */
import { test, expect, type ConsoleMessage, type Request } from '@playwright/test'

const BASE = 'http://testlookup.local'
const PROJECT_ID = '2aefa4fa-8917-448b-9895-9cd41911ef95'
const PROJECT_NAME = 'Checkout Service'

test('failure analysis — deep walk', async ({ page, request }) => {
  const login = await request.post(`${BASE}/api/v1/auth/login`, {
    form: { username: 'admin', password: 'Admin@2026!' },
  })
  expect(login.ok(), 'login must succeed').toBeTruthy()
  const auth = await login.json()

  await page.addInitScript(
    ([token, refresh, pid, pname]) => {
      localStorage.setItem(
        'auth-storage',
        JSON.stringify({
          state: {
            token,
            refreshToken: refresh,
            user: { username: 'admin', role: 'ADMIN', email: 'admin@testlookup.local' },
            isAuthenticated: true,
          },
          version: 0,
        }),
      )
      localStorage.setItem(
        'testlookup-active-project',
        JSON.stringify({
          state: { activeProjectId: pid, activeProject: { id: pid, name: pname } },
          version: 0,
        }),
      )
    },
    [auth.access_token, auth.refresh_token ?? '', PROJECT_ID, PROJECT_NAME] as const,
  )

  const api: string[] = []
  const errors: string[] = []
  const pageErrors: string[] = []

  page.on('console', (m: ConsoleMessage) => {
    if (m.type() === 'error' && !m.text().includes('DevTools') && !m.text().includes('favicon')) {
      errors.push(m.text().slice(0, 200))
    }
  })
  page.on('pageerror', (e: Error) => pageErrors.push(String(e.message).slice(0, 200)))
  page.on('response', (r) => {
    const u = r.url()
    if (u.includes('/api/')) api.push(`${r.status()} ${u.replace(BASE, '').slice(0, 130)}`)
  })
  page.on('requestfailed', (r: Request) => {
    if (r.url().includes('/api/')) api.push(`NETFAIL ${r.url().replace(BASE, '').slice(0, 130)}`)
  })

  await page.goto(`${BASE}/failures`, { waitUntil: 'networkidle', timeout: 45_000 })
  await page.waitForTimeout(3000)

  const body = (await page.locator('main, body').first().innerText().catch(() => '')) || ''

  console.log('\n===== FAILURE ANALYSIS =====')
  console.log(`chars=${body.length}`)
  console.log('\n--- API CALLS ---')
  for (const a of [...new Set(api)]) console.log('  ' + a)
  console.log('\n--- FAILED API ---')
  const bad = [...new Set(api)].filter((a) => /^(4|5)\d\d|NETFAIL/.test(a))
  console.log(bad.length ? bad.map((b) => '  ' + b).join('\n') : '  (none)')
  console.log('\n--- CONSOLE ERRORS ---')
  console.log(errors.length ? errors.map((e) => '  ' + e).join('\n') : '  (none)')
  console.log('\n--- PAGE ERRORS ---')
  console.log(pageErrors.length ? pageErrors.map((e) => '  ' + e).join('\n') : '  (none)')

  // Does the seeded oracle appear, and how is it labelled?
  console.log('\n--- ORACLE TESTS ON PAGE ---')
  for (const name of ['test_currency_rounding', 'test_discount_stacking', 'test_payment_timeout']) {
    console.log(`  ${name}: ${body.includes(name) ? 'PRESENT' : 'absent'}`)
  }

  // Confidence values rendered as fact + hedging language
  const pct = body.match(/\b\d{1,3}(\.\d+)?%/g) || []
  console.log('\n--- PERCENTAGES RENDERED ---')
  console.log('  ' + (pct.length ? [...new Set(pct)].join(', ') : '(none)'))
  const hedges = ['likely', 'suggest', 'possible', 'may ', 'might', 'appears', 'estimated', 'probable']
  console.log('  hedging words present: ' + hedges.filter((h) => body.toLowerCase().includes(h)).join(', ') || '  (none)')

  console.log('\n--- BODY (first 2500 chars) ---')
  console.log(body.slice(0, 2500))
  console.log('\n============================\n')
})
