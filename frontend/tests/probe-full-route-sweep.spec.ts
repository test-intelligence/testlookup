/**
 * EX-01 (EXJ-2026-09-18) — landmark tour over EVERY declared static route.
 *
 * `probe-route-sweep.spec.ts` covers a curated 18. This one derives its list
 * from `src/App.tsx` at runtime, so a route added tomorrow is swept tomorrow
 * without anyone remembering to add it — and a route that stops existing stops
 * being swept rather than silently resolving to `/overview` through the
 * catch-all (TL-2026-09-18-01-001).
 *
 * Read-only: it navigates and reads. It submits nothing.
 *
 *   npx playwright test --config probe-live.config.ts probe-full-route-sweep
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { test, expect, type Page } from '@playwright/test'

// Playwright loads specs as ESM, where `__dirname` does not exist.
const HERE = dirname(fileURLToPath(import.meta.url))

const BASE = 'http://testlookup.local'
const USER = 'admin'
const PASS = 'Admin@2026!'

type Declared = { path: string; management: boolean }

function declaredStaticRoutes(): Declared[] {
  const source = readFileSync(join(HERE, '..', 'src', 'App.tsx'), 'utf-8')
  const table = (name: string): string[] => {
    const block = source.match(new RegExp(`const ${name}: AppRoute\\[\\] = \\[([\\s\\S]*?)\\n\\]`))
    if (!block) throw new Error(`${name} table not found in App.tsx`)
    return [...block[1].matchAll(/\{ path: '([^']+)'/g)].map((m) => m[1])
  }
  const app = table('appRoutes').map((p) => ({ path: `/${p}`, management: false }))
  const mgmt = table('managementRoutes').map((p) => ({ path: `/${p}`, management: true }))
  // Dynamic routes need real ids; they are covered by probe-route-sweep.
  return [...app, ...mgmt].filter((r) => !r.path.includes(':'))
}

/** Documented "not configured" answers, kept to one endpoint each. */
const ALLOWED_404 = [/\/llm-quota$/]

type Result = {
  path: string
  final: string
  heading: string
  chars: number
  consoleErrors: string[]
  httpFailures: string[]
}

async function sweep(page: Page, path: string): Promise<Result> {
  const consoleErrors: string[] = []
  const httpFailures: string[] = []
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text().slice(0, 160))
  })
  page.on('response', (res) => {
    const status = res.status()
    const url = res.url()
    if (status < 400) return
    if (status === 404 && ALLOWED_404.some((re) => re.test(new URL(url).pathname))) return
    httpFailures.push(`${status} ${new URL(url).pathname}`)
  })

  await page.goto(`${BASE}${path}`, { waitUntil: 'domcontentloaded' })
  // Wait for rendered content rather than network silence: background pollers
  // mean `networkidle` never arrives on the later routes.
  await page
    .locator('h1, h2, [role="heading"]')
    .first()
    .waitFor({ state: 'visible', timeout: 5_000 })
    .catch(() => undefined)
  await page.waitForTimeout(700)

  const main = page.locator('main').first()
  const body = (await main.count()) ? main : page.locator('body')
  return {
    path,
    final: new URL(page.url()).pathname,
    heading: ((await page.locator('h1, h2').first().textContent().catch(() => '')) || '').trim().slice(0, 48),
    chars: ((await body.innerText().catch(() => '')) || '').trim().length,
    consoleErrors,
    httpFailures,
  }
}

test('every declared static route renders its own page', async ({ browser }) => {
  // probe-live.config.ts caps tests at 60s, which is a sane default for a
  // single-page probe. This one walks ~57 routes at roughly 3s each.
  test.setTimeout(20 * 60 * 1000)
  const routes = declaredStaticRoutes()
  expect(routes.length, 'route table parse produced too few routes').toBeGreaterThan(50)

  // Sign in once, keep the storage state, then give every route its own
  // context. `probe-route-sweep.spec.ts` records why: background pollers
  // started by earlier routes keep firing, and sharing one context makes the
  // later routes flakier the later they run. Reusing one long-lived context
  // for 57 pages also exhausted the browser here — `Target.createTarget: Not
  // supported` on a mid-list newPage().
  const authCtx = await browser.newContext()
  const login = await authCtx.newPage()
  await login.goto(`${BASE}/login`)
  await login.getByLabel(/username/i).fill(USER)
  await login.getByLabel(/password/i).fill(PASS)
  await login.getByRole('button', { name: /sign in|log in/i }).click()
  await login.waitForURL(/\/overview/, { timeout: 30_000 })
  const storageState = await authCtx.storageState()
  await authCtx.close()

  // One context per route exhausted the browser at ~57 contexts
  // (`Target.disposeBrowserContext: Failed to find context`). One context for
  // all 57 exhausted it too. Recycling every CHUNK routes bounds both the
  // context count and the background-poller accumulation that
  // `probe-route-sweep.spec.ts` documents.
  const CHUNK = 12
  const results: Result[] = []
  for (let i = 0; i < routes.length; i += CHUNK) {
    const ctx = await browser.newContext({ storageState })
    try {
      const page = await ctx.newPage()
      for (const route of routes.slice(i, i + CHUNK)) {
        const result = await sweep(page, route.path)
        results.push(result)
        // Printed as it completes, not batched at the end: the first two runs
        // of this sweep died on a browser/timeout fault and took every row
        // with them.
        const flag = result.final !== result.path ? 'REDIRECT' : result.chars < 400 ? 'THIN' : result.httpFailures.length || result.consoleErrors.length ? 'ERRORS' : 'ok'
        console.log(`${flag.padEnd(9)} ${result.path.padEnd(30)} -> ${result.final.padEnd(30)} chars=${String(result.chars).padStart(5)} h="${result.heading}"`)
      }
    } finally {
      await ctx.close()
    }
  }

  const redirected = results.filter((r) => r.final !== r.path)
  const thin = results.filter((r) => r.final === r.path && r.chars < 400)
  const broken = results.filter((r) => r.httpFailures.length || r.consoleErrors.length)

  console.log('===== EX-01 FULL ROUTE SWEEP =====')
  console.log(`routes=${results.length} redirected=${redirected.length} thin=${thin.length} witherrors=${broken.length}`)
  for (const r of broken) {
    for (const h of r.httpFailures.slice(0, 4)) console.log(`   HTTP  ${r.path}: ${h}`)
    for (const c of r.consoleErrors.slice(0, 3)) console.log(`   CONS  ${r.path}: ${c}`)
  }
  console.log('==================================')

  // Assert only the unambiguous contract here; THIN and ERRORS are triage
  // input for the mission report, not automatic defects — an empty state is a
  // legitimate render.
  expect(
    redirected.map((r) => `${r.path} -> ${r.final}`),
    'a declared route redirected away from itself',
  ).toEqual([])
})
