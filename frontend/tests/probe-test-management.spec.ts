/**
 * Targeted diagnostic probe for the recurring "Test Cases tab empty" report.
 *
 * Run: npx playwright test tests/probe-test-management.spec.ts \
 *      --config=probe-live.config.ts --reporter=line
 *
 * Does three things:
 * 1. Calls /api/v1/test-management/cases directly (twice — with and
 *    without ``include_automation=true``) using the logged-in JWT.
 *    This isolates "backend returned 0" vs "frontend rendering bug".
 * 2. Visits /test-management?tab=Test+Cases and grabs the rendered
 *    main-content text, the localStorage flag, and the active project id.
 * 3. Captures every 4xx/5xx network response so we see things like
 *    422 validation errors or 500s that didn't reach the rendered page.
 */
import { test, expect } from '@playwright/test'

const BASE = 'http://testlookup.local'

test('test-management probe — API response + rendered state', async ({ page }) => {
  const networkFailures: string[] = []
  const consoleErrors: string[] = []

  page.on('console', msg => {
    if (msg.type() === 'error' || msg.type() === 'warning') {
      consoleErrors.push(`[${msg.type()}] ${msg.text()}`)
    }
  })
  page.on('pageerror', e => {
    consoleErrors.push(`[pageerror] ${e.message}`)
  })
  page.on('response', resp => {
    if (resp.status() >= 400) {
      networkFailures.push(`${resp.status()} ${resp.request().method()} ${resp.url()}`)
    }
  })

  // ── Sign in ──────────────────────────────────────────────────────────
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
  await page.fill('input[name="username"]', 'admin')
  await page.fill('input[type="password"]', 'Admin@2026!')
  await Promise.all([
    page.waitForURL(/\/(overview|my-failures)/, { timeout: 15_000 }).catch(() => {}),
    page.click('button[type="submit"]'),
  ])

  // ── Read the JWT + active project id from the running app ────────────
  const authState = await page.evaluate(() => {
    const raw = localStorage.getItem('auth-storage')
    const proj = localStorage.getItem('project-storage')
    const flag = localStorage.getItem('tl.tm.includeAutomation')
    return { raw, proj, flag }
  })
  console.log('\n========== AUTH/STATE ==========')
  console.log('  auth-storage:', authState.raw?.slice(0, 200))
  console.log('  project-storage:', authState.proj?.slice(0, 200))
  console.log('  tl.tm.includeAutomation:', authState.flag)

  // ── Direct API calls — bypass React, isolate backend ─────────────────
  const apiResponses: Record<string, unknown> = {}
  for (const variant of [
    '/api/v1/test-management/cases?page=1&size=25',
    '/api/v1/test-management/cases?page=1&size=25&include_automation=true',
  ]) {
    const resp = await page.evaluate(async (url) => {
      try {
        const r = await fetch(url, { credentials: 'include' })
        const txt = await r.text()
        return { status: r.status, body: txt.slice(0, 2000) }
      } catch (e) {
        return { status: -1, body: String(e) }
      }
    }, variant)
    apiResponses[variant] = resp
  }

  console.log('\n========== DIRECT API ==========')
  for (const [url, r] of Object.entries(apiResponses)) {
    console.log(`\n  ${url}`)
    console.log(`  ->`, JSON.stringify(r).slice(0, 600))
  }

  // ── Visit the page itself ────────────────────────────────────────────
  await page.goto(`${BASE}/test-management?tab=Test+Cases`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(6_000)
  const content = await page.evaluate(() => {
    const main = document.querySelector('main, [role="main"]') as HTMLElement | null
    return (main?.innerText ?? document.body.innerText ?? '').replace(/\s+/g, ' ').slice(0, 2000)
  })

  console.log('\n========== PAGE CONTENT (first 2KB) ==========')
  console.log(content)

  console.log('\n========== NETWORK 4xx/5xx ==========')
  for (const f of networkFailures) console.log('  ', f)

  console.log('\n========== CONSOLE ==========')
  for (const e of consoleErrors) console.log('  ', e)
  console.log('================================\n')

  expect(true).toBe(true)
})
