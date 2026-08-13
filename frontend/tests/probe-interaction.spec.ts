/**
 * Interaction-level probe — the dimensions the contract-level probes missed.
 *
 * Everything so far has been HTTP against the live API. This drives the SPA as a
 * user does and looks for state bugs that only appear through interaction:
 *
 *   1. back/forward navigation           — is state preserved or silently reset?
 *   2. reload mid-flow                   — does a filtered view survive F5?
 *   3. combined filters                  — suite + time window together
 *   4. pagination boundaries             — first/last/overshoot
 *   5. deep-linking                      — does a filtered URL reproduce the view?
 *
 * Reports observations; it does not assert pass/fail. A human (or the next
 * iteration) judges. Nothing here is filed as a bug without a separate repro.
 */
import { test, expect, type ConsoleMessage } from '@playwright/test'

const BASE = 'http://testlookup.local'
const PROJECT_ID = '2aefa4fa-8917-448b-9895-9cd41911ef95'
const PROJECT_NAME = 'Checkout Service'

type Obs = { label: string; detail: string }
const obs: Obs[] = []
const note = (label: string, detail: string) => obs.push({ label, detail })

test('interaction-level walk', async ({ page, request }) => {
  const login = await request.post(`${BASE}/api/v1/auth/login`, {
    form: { username: 'admin', password: 'Admin@2026!' },
  })
  expect(login.ok()).toBeTruthy()
  const auth = await login.json()

  await page.addInitScript(
    ([token, refresh, pid, pname]) => {
      localStorage.setItem('auth-storage', JSON.stringify({
        state: {
          token, refreshToken: refresh,
          user: { username: 'admin', role: 'ADMIN', email: 'admin@testlookup.local' },
          isAuthenticated: true,
        }, version: 0,
      }))
      localStorage.setItem('testlookup-active-project', JSON.stringify({
        state: { activeProjectId: pid, activeProject: { id: pid, name: pname } }, version: 0,
      }))
    },
    [auth.access_token, auth.refresh_token ?? '', PROJECT_ID, PROJECT_NAME] as const,
  )

  const consoleErrors: string[] = []
  page.on('console', (m: ConsoleMessage) => {
    if (m.type() === 'error' && !m.text().includes('DevTools') && !m.text().includes('favicon')) {
      consoleErrors.push(m.text().slice(0, 140))
    }
  })
  const httpFails: string[] = []
  page.on('response', (r) => {
    if (r.status() >= 400 && r.url().includes('/api/')) {
      httpFails.push(`${r.status()} ${r.url().replace(BASE, '').slice(0, 110)}`)
    }
  })

  const bodyLen = async () =>
    ((await page.locator('main, body').first().innerText().catch(() => '')) || '').trim().length

  // ── 1. deep-link with a filter in the URL ────────────────────────────────
  await page.goto(`${BASE}/runs?days=30`, { waitUntil: 'networkidle', timeout: 45_000 })
  await page.waitForTimeout(1500)
  note('deep-link /runs?days=30', `url=${page.url().replace(BASE, '')} chars=${await bodyLen()}`)

  // ── 2. change the time window, check the URL reflects it ─────────────────
  const before = page.url()
  for (const label of ['7d', '14d', '30d', '90d']) {
    const btn = page.getByRole('button', { name: label, exact: true }).first()
    if (await btn.count()) {
      await btn.click()
      await page.waitForTimeout(1200)
      note(`time-window "${label}" clicked`, `url=${page.url().replace(BASE, '')} chars=${await bodyLen()}`)
      break
    }
  }
  note('url changed by window switch?', `${before !== page.url()}`)

  // ── 3. navigate away then BACK ───────────────────────────────────────────
  await page.goto(`${BASE}/coverage`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1200)
  const coverageChars = await bodyLen()
  await page.goBack({ waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)
  note('after goBack to /runs', `url=${page.url().replace(BASE, '')} chars=${await bodyLen()} (coverage had ${coverageChars})`)

  await page.goForward({ waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)
  note('after goForward to /coverage', `url=${page.url().replace(BASE, '')} chars=${await bodyLen()}`)

  // ── 4. reload mid-flow ───────────────────────────────────────────────────
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)
  note('after reload', `url=${page.url().replace(BASE, '')} chars=${await bodyLen()}`)

  // ── 5. pagination boundaries via URL ─────────────────────────────────────
  for (const p of ['1', '2', '999']) {
    await page.goto(`${BASE}/runs?page=${p}`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(1200)
    note(`/runs?page=${p}`, `chars=${await bodyLen()}`)
  }

  // ── 6. combined filters: suite + window, then reload ─────────────────────
  await page.goto(`${BASE}/coverage?days=30&suite=regression`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)
  const combined = await bodyLen()
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)
  note('combined filters survive reload', `before=${combined} after=${await bodyLen()} url=${page.url().replace(BASE, '')}`)

  // ── 7. an unknown project id in a deep link ──────────────────────────────
  await page.goto(`${BASE}/runs?project_id=00000000-0000-0000-0000-000000000000`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)
  note('deep-link unknown project_id', `chars=${await bodyLen()}`)

  console.log('\n===== INTERACTION WALK =====')
  for (const o of obs) console.log(`  ${o.label.padEnd(38)} ${o.detail}`)
  console.log('\n--- CONSOLE ERRORS ---')
  console.log(consoleErrors.length ? [...new Set(consoleErrors)].map((e) => '  ' + e).join('\n') : '  (none)')
  console.log('\n--- FAILED API CALLS ---')
  console.log(httpFails.length ? [...new Set(httpFails)].map((e) => '  ' + e).join('\n') : '  (none)')
  console.log('============================\n')
})
