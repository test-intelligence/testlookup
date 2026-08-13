/**
 * First coverage of /getting-started — the first-run onboarding page.
 *
 * The interesting oracle is not "does it render" but "does it tell the truth".
 * This deployment is NOT a fresh install: it has 2 live projects, ~68 runs and
 * real test data. An onboarding checklist that still says "no projects yet",
 * or that reports a step incomplete when it demonstrably is complete, is
 * lying to the user about their own instance.
 *
 * Dumps page text rather than substring-matching a guess: CSS uppercases
 * labels and innerText returns the transformed string, which already produced
 * one false negative this session.
 */
import { test, expect } from '@playwright/test'

const BASE = 'http://testlookup.local'

async function login(page) {
  await page.goto(`${BASE}/login`)
  await page.fill('input[name="username"], input[type="text"]', 'admin')
  await page.fill('input[type="password"]', 'Admin@2026!')
  await page.click('button[type="submit"]')
  await page.waitForURL(/\/(overview|dashboard|$)/, { timeout: 20000 })
}

test('getting-started: what it renders and what it claims', async ({ page }) => {
  const consoleErrors: string[] = []
  const failedRequests: string[] = []
  page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(m.text())
  })
  page.on('response', (r) => {
    if (r.status() >= 400 && r.url().includes('/api/')) {
      failedRequests.push(`${r.status()} ${r.url().replace(BASE, '')}`)
    }
  })

  await login(page)
  await page.goto(`${BASE}/getting-started`)
  await page.waitForTimeout(6000)

  const body = await page.locator('body').innerText()

  console.log('=== CONSOLE ERRORS ===', JSON.stringify(consoleErrors, null, 1))
  console.log('=== FAILED API REQUESTS ===', JSON.stringify(failedRequests, null, 1))
  console.log('=== PAGE TEXT ===')
  console.log(body)

  expect(body.length).toBeGreaterThan(0)
})
