/**
 * EX-01 (EXJ-2026-09-18) — landmark tour over EVERY declared static route.
 *
 * `probe-route-sweep.spec.ts` covers a curated 18 and `probe-exploratory.spec.ts`
 * another curated 18. This one derives its list from `src/App.tsx` at runtime,
 * so a route added tomorrow is swept tomorrow without anyone remembering to add
 * it — and a route that stops existing stops being swept rather than silently
 * resolving to `/overview` through the catch-all (TL-2026-09-18-01-001).
 *
 * Read-only: it navigates and reads. It submits nothing.
 *
 * Mechanics are taken from `probe-exploratory.spec.ts`, which walks 18 pages in
 * 53s. Three earlier versions of this file did not, and the reasons are worth
 * keeping:
 *
 *   1. A fresh context per route exhausted the browser at ~57 contexts
 *      (`Target.disposeBrowserContext: Failed to find context`).
 *   2. Chunking into one context per 12 routes still cost ~2 minutes per route:
 *      each chunk re-bootstraps the whole SPA, and the per-route listeners were
 *      attached to a shared page and never removed, so the last route in a
 *      chunk carried twelve sets of them.
 *   3. Both died on the test timeout with every row still buffered in memory.
 *
 * So: ONE page, auth seeded into localStorage before first paint, listeners
 * attached and detached per route, and each row printed as it completes.
 *
 *   npx playwright test --config probe-live.config.ts probe-full-route-sweep
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { test, expect, type ConsoleMessage, type Request } from '@playwright/test'

// Playwright loads specs as ESM, where `__dirname` does not exist.
const HERE = dirname(fileURLToPath(import.meta.url))

const BASE = 'http://testlookup.local'
const PROJECT_ID = '2aefa4fa-8917-448b-9895-9cd41911ef95'
const PROJECT_NAME = 'Checkout Service'

/** Static route paths declared in `App.tsx`'s two route tables. */
function declaredStaticRoutes(): string[] {
  const source = readFileSync(join(HERE, '..', 'src', 'App.tsx'), 'utf-8')
  const table = (name: string): string[] => {
    const block = source.match(new RegExp(`const ${name}: AppRoute\\[\\] = \\[([\\s\\S]*?)\\n\\]`))
    if (!block) throw new Error(`${name} table not found in App.tsx`)
    return [...block[1].matchAll(/\{ path: '([^']+)'/g)].map((m) => m[1])
  }
  // Dynamic routes need real ids; `probe-route-sweep.spec.ts` covers those.
  return [...table('appRoutes'), ...table('managementRoutes')]
    .map((p) => `/${p}`)
    .filter((p) => !p.includes(':'))
}

/** Noise that is not evidence of a defect. */
function isIgnorableConsole(text: string): boolean {
  return (
    text.includes('Download the React DevTools') ||
    text.includes('[vite]') ||
    text.includes('favicon')
  )
}

/** Documented "not configured" answers, kept to one endpoint each. */
const ALLOWED_404 = [/\/llm-quota$/]

test('every declared static route renders its own page', async ({ browser, request }) => {
  test.setTimeout(25 * 60 * 1000)

  const all = declaredStaticRoutes()
  expect(all.length, 'route table parse produced too few routes').toBeGreaterThan(50)
  // EXJ_SWEEP_LIMIT exists to time the sweep on a slice before committing the
  // whole list to a long run. Unset, every declared route is swept.
  const limit = Number(process.env.EXJ_SWEEP_LIMIT || 0)
  const routes = limit > 0 ? all.slice(0, limit) : all

  const login = await request.post(`${BASE}/api/v1/auth/login`, {
    form: { username: 'admin', password: 'Admin@2026!' },
  })
  expect(login.ok(), 'login must succeed before sweeping').toBeTruthy()
  const auth = await login.json()

  type InitArgs = readonly [string, string, string, string]
  const initScript = ([token, refresh, pid, pname]: InitArgs) => {
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
  }
  const initArgs: InitArgs = [
    auth.access_token,
    auth.refresh_token ?? '',
    PROJECT_ID,
    PROJECT_NAME,
  ]

  const redirected: string[] = []
  const thin: string[] = []
  const withErrors: string[] = []

  // The renderer dies after roughly eight route navigations on one page: an
  // SPA accumulates React trees, SWR caches and pollers per navigation, and
  // Chromium eventually reports `page.goto: Page crashed` — after which every
  // later route reports a crash and the stale URL, which reads as 47 redirects
  // and is really one crash cascading. /coverage/suite was blamed for it until
  // it was opened on its own and rendered fine (479 chars, no errors).
  //
  // A page is cheap inside an existing context; a context is not, because each
  // one re-bootstraps the whole SPA. So: one context, a fresh page every
  // PAGE_RECYCLE routes.
  // Chromium shares one renderer process across same-origin pages in a
  // context, so a fresh PAGE does not free what the SPA accumulated — the
  // renderer still died around route 9 ("page.goto: Page crashed"), after
  // which every later route reported the crash and the stale URL, which reads
  // as dozens of redirects and is really one crash cascading.
  //
  // /coverage/suite was blamed twice because it is where the ceiling happened
  // to land. It was cleared twice: loaded on its own it renders 479 chars with
  // no errors, and /coverage -> /coverage/suite -> /suites in sequence is
  // clean. The limit is cumulative, not that page.
  //
  // A fresh CONTEXT gets a fresh renderer. It costs one SPA bootstrap each
  // time, which is affordable now that the per-route wait is on rendered
  // content rather than `networkidle`.
  const CONTEXT_RECYCLE = 6
  let context = await browser.newContext()
  await context.addInitScript(initScript, initArgs)
  let page = await context.newPage()
  let sinceRecycle = 0

  for (const route of routes) {
    if (sinceRecycle >= CONTEXT_RECYCLE) {
      await context.close()
      context = await browser.newContext()
      await context.addInitScript(initScript, initArgs)
      page = await context.newPage()
      sinceRecycle = 0
    }
    sinceRecycle += 1
    const consoleErrors: string[] = []
    const failedRequests: string[] = []
    const pageErrors: string[] = []

    const onConsole = (m: ConsoleMessage) => {
      if (m.type() === 'error' && !isIgnorableConsole(m.text())) {
        consoleErrors.push(m.text().slice(0, 160))
      }
    }
    const onPageError = (e: Error) => pageErrors.push(String(e.message).slice(0, 160))
    const onResponse = (r: { status: () => number; url: () => string }) => {
      const status = r.status()
      const url = r.url()
      if (status < 400 || !url.includes('/api/')) return
      const path = url.replace(BASE, '').slice(0, 120)
      if (status === 404 && ALLOWED_404.some((re) => re.test(path.split('?')[0]))) return
      failedRequests.push(`${status} ${path}`)
    }
    const onRequestFailed = (r: Request) => {
      if (r.url().includes('/api/')) {
        failedRequests.push(`NETFAIL ${r.url().replace(BASE, '').slice(0, 120)}`)
      }
    }

    page.on('console', onConsole)
    page.on('pageerror', onPageError)
    page.on('response', onResponse)
    page.on('requestfailed', onRequestFailed)

    let bodyLen = 0
    let heading = ''
    let navError = ''
    let final = ''
    try {
      // NOT `networkidle`. `probe-route-sweep.spec.ts` records why: background
      // pollers started by earlier routes keep firing, so on a single page the
      // network never goes quiet for 500ms and every route burns the full
      // timeout. That cost this sweep two runs before the header comment next
      // door was taken at its word. Wait on rendered content instead — which
      // is the actual contract, and is what a user would call "the page came
      // up".
      await page.goto(`${BASE}${route}`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
      await page
        .locator('h1, h2')
        .first()
        .waitFor({ state: 'visible', timeout: 6_000 })
        .catch(() => undefined)
      // Let SWR settle: most pages fetch on mount.
      await page.waitForTimeout(900)
      bodyLen = (await page.locator('main, body').first().innerText().catch(() => '')).trim().length
      heading = (await page.locator('h1, h2').first().innerText().catch(() => '')).trim().slice(0, 44)
    } catch (e) {
      navError = String((e as Error).message).split('\n')[0].slice(0, 100)
    }
    try {
      final = new URL(page.url()).pathname
    } catch {
      final = '?'
    }

    page.off('console', onConsole)
    page.off('pageerror', onPageError)
    page.off('response', onResponse)
    page.off('requestfailed', onRequestFailed)

    const flags: string[] = []
    if (navError) flags.push(`NAV_ERROR(${navError})`)
    if (pageErrors.length) flags.push(`PAGE_ERROR x${pageErrors.length}`)
    if (consoleErrors.length) flags.push(`CONSOLE x${consoleErrors.length}`)
    if (failedRequests.length) flags.push(`HTTP_FAIL x${failedRequests.length}`)
    if (!navError && bodyLen < 200) flags.push(`THIN(${bodyLen})`)
    if (final !== route) flags.push(`REDIRECT(-> ${final})`)

    if (final !== route) redirected.push(`${route} -> ${final}`)
    if (!navError && bodyLen < 200) thin.push(`${route} (${bodyLen} chars)`)
    if (pageErrors.length || consoleErrors.length || failedRequests.length) {
      withErrors.push(
        `${route}: ${[...pageErrors, ...consoleErrors, ...failedRequests].slice(0, 3).join(' | ')}`,
      )
    }

    // Printed as it completes, not batched: two earlier runs died on a timeout
    // and took every row with them.
    console.log(
      `${flags.length ? 'FLAG' : '  ok'} ${route.padEnd(30)} chars=${String(bodyLen).padStart(5)} ` +
        `h="${heading}" ${flags.join(' ')}`,
    )
  }

  await context.close()

  console.log('===== EX-01 FULL ROUTE SWEEP =====')
  console.log(
    `routes=${routes.length}/${all.length} redirected=${redirected.length} thin=${thin.length} witherrors=${withErrors.length}`,
  )
  for (const r of redirected) console.log(`  REDIRECT ${r}`)
  for (const t of thin) console.log(`  THIN     ${t}`)
  for (const e of withErrors) console.log(`  ERRORS   ${e}`)
  console.log('==================================')

  // Only the unambiguous contract is asserted here. THIN and ERRORS are triage
  // input for the mission report — an empty state is a legitimate render.
  expect(redirected, 'a declared route redirected away from itself').toEqual([])
})
