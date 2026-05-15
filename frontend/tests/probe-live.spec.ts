/**
 * Ad-hoc diagnostic probe — load the live homelab as a real browser,
 * sign in, hit the pages the user reported as broken, and dump every
 * console message + failed network request. Not a real test; we only
 * run this when investigating a "pages don't render" report.
 *
 * Run: npx playwright test tests/probe-live.spec.ts --reporter=line
 */
import { test, expect } from '@playwright/test'

const BASE = 'http://testlookup.local'
const PAGES = [
  '/overview',
  '/my-failures',
  '/runs',
  '/live',
  '/failures',
  '/intelligence',
  '/agents',
  '/deep-investigate/cc222031-9eb6-435a-bf2c-e5ff12dd28cb',
  '/release-gate/cc222031-9eb6-435a-bf2c-e5ff12dd28cb',
]

test('live probe — capture console + network errors per page', async ({ page }) => {
  // Aggregate everything per URL so the final report shows the user-visible blast radius.
  const consoleByPage: Record<string, string[]> = {}
  const failuresByPage: Record<string, string[]> = {}
  let currentPath = '/login'

  page.on('console', msg => {
    if (msg.type() === 'error' || msg.type() === 'warning') {
      consoleByPage[currentPath] ??= []
      consoleByPage[currentPath].push(`[${msg.type()}] ${msg.text()}`)
    }
  })
  page.on('pageerror', e => {
    consoleByPage[currentPath] ??= []
    consoleByPage[currentPath].push(`[pageerror] ${e.message}\n${e.stack?.split('\n').slice(0,3).join('\n')}`)
  })
  page.on('requestfailed', req => {
    failuresByPage[currentPath] ??= []
    failuresByPage[currentPath].push(`${req.method()} ${req.url()} — ${req.failure()?.errorText}`)
  })
  page.on('response', resp => {
    if (resp.status() >= 400) {
      failuresByPage[currentPath] ??= []
      failuresByPage[currentPath].push(`${resp.status()} ${resp.request().method()} ${resp.url()}`)
    }
  })

  // Sign in once.
  currentPath = '/login'
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
  await page.fill('input[type="text"], input[name="username"]', 'admin')
  await page.fill('input[type="password"]', 'Admin@2026!')
  await Promise.all([
    page.waitForURL(/\/(overview|my-failures)/, { timeout: 15_000 }).catch(() => {}),
    page.click('button[type="submit"]'),
  ])

  const contentByPage: Record<string, string> = {}
  // Visit each page, give SWR time to fetch, then capture rendered text.
  for (const path of PAGES) {
    currentPath = path
    await page.goto(`${BASE}${path}`, { waitUntil: 'domcontentloaded' })
    // Let SWR finish + lazy chunks settle. 6s is generous for SWR's 100-300ms p95.
    await page.waitForTimeout(6_000)
    // Grab the main content area (not the sidebar) so we see what user sees.
    const mainText = await page.evaluate(() => {
      const main = document.querySelector('main, [role="main"], #root > div > div > div:not(:first-child)') as HTMLElement | null
      // Strip whitespace, cap at 1.2KB
      return (main?.innerText ?? document.body.innerText ?? '').replace(/\s+/g, ' ').slice(0, 1200)
    })
    contentByPage[path] = mainText
  }

  // Dump report so the test output carries everything.
  console.log('\n========== PROBE REPORT ==========')
  for (const path of [...PAGES, '/login']) {
    const errs = consoleByPage[path] ?? []
    const fails = failuresByPage[path] ?? []
    const content = contentByPage[path]
    console.log(`\n--- ${path} ---`)
    if (content !== undefined) console.log('  CONTENT:', content.slice(0, 600))
    for (const e of errs) console.log('  CONSOLE:', e)
    for (const f of fails) console.log('  NETWORK:', f)
  }
  console.log('==================================\n')

  // We don't assert — just want the diagnostic output.
  expect(true).toBe(true)
})
