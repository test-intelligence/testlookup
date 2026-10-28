/**
 * Activity live tail across two browser contexts — a LOCAL-stack journey (W21d ACT-02, D-18).
 *
 * Runs ONLY against a running local stack (API + Vite + worker + Postgres + Redis, dev-login
 * enabled): it lives in `tests/e2e/`, which no CI Playwright config reads
 * (`playwright.ci.config.ts` → `tests/ci-e2e`, `playwright.visual.config.ts` → `tests/visual`,
 * `playwright.docs.config.ts` → one docs spec, `playwright.sweep.config.ts` → `*-sweep.spec.ts`,
 * `playwright.perf.config.ts` → `tests/perf`). CI proves the same path with the Postgres + Redis
 * integration tests and the hermetic mocked-stream spec.
 *
 * Context B opens /activity with Live ON and waits until it is streaming. Context A makes one
 * reversible change that records two true ledger rows (a saved view created, then deleted).
 * Each row must appear at the top of B's feed within ~3 s, with no reload, and B's stream
 * request must carry the bearer token in its Authorization header and nothing in its URL.
 *
 * Env: `PLAYWRIGHT_BASE_URL` (the SPA, via the config's baseURL), `VITE_API_BASE_URL` (the API,
 * default http://localhost:8000), optional `ACTIVITY_LIVE_PROJECT_ID` (else the first active
 * project the admin can see).
 */
import { expect, test, type APIRequestContext, type Browser, type BrowserContext, type Page, type Request } from '@playwright/test'

// Node's `process`, typed here: the shared install carries no @types/node, and this spec must
// type-check on its own (`tests/e2e` is outside tsconfig.json's `src`).
declare const process: { env: Record<string, string | undefined> }

const BACKEND_URL = process.env.VITE_API_BASE_URL || 'http://localhost:8000'
const LIVE_WITHIN_MS = 3_000

type Tokens = { access_token: string; refresh_token: string }
type ProjectRow = { id: string; name: string; slug: string; is_active?: boolean }

function bearer(token: string) {
  return { Authorization: `Bearer ${token}` }
}

/** dev-login takes role / username as QUERY parameters (a JSON body is ignored). */
async function devLogin(request: APIRequestContext, role: string): Promise<Tokens> {
  const response = await request.post(`${BACKEND_URL}/api/v1/auth/dev-login?role=${role}`)
  expect(response.status(), 'dev-login must be enabled on the local stack').toBe(200)
  return (await response.json()) as Tokens
}

async function pickProject(request: APIRequestContext, token: string): Promise<ProjectRow> {
  const response = await request.get(`${BACKEND_URL}/api/v1/projects`, { headers: bearer(token) })
  expect(response.status()).toBe(200)
  const body = (await response.json()) as ProjectRow[] | { items?: ProjectRow[] }
  const rows = Array.isArray(body) ? body : Array.isArray(body.items) ? body.items : []
  const wanted = process.env.ACTIVITY_LIVE_PROJECT_ID
  const project = wanted
    ? rows.find((row) => row.id === wanted)
    : rows.find((row) => row.is_active !== false)
  if (!project) throw new Error('No project to watch: seed one or set ACTIVITY_LIVE_PROJECT_ID')
  return project
}

/** A context signed in (Zustand's persisted keys) with the project already active. */
async function signedInContext(browser: Browser, tokens: Tokens, project: ProjectRow): Promise<BrowserContext> {
  const context = await browser.newContext()
  await context.addInitScript(
    ({ auth, active }) => {
      window.localStorage.setItem('auth-storage', JSON.stringify({ state: auth, version: 0 }))
      window.localStorage.setItem(
        'testlookup-active-project',
        JSON.stringify({ state: { activeProjectId: active.id, activeProject: active }, version: 0 }),
      )
    },
    { auth: { token: tokens.access_token, refreshToken: tokens.refresh_token }, active: project },
  )
  return context
}

async function openLiveFeed(page: Page): Promise<void> {
  await page.goto('/activity')
  const toggle = page.locator('button[data-activity-live]')
  await expect(toggle).toBeVisible({ timeout: 15_000 })
  if ((await toggle.getAttribute('aria-pressed')) !== 'true') await toggle.click()
  await expect(toggle).toHaveAttribute('aria-pressed', 'true')
  // Streaming, not merely ON: the status line is gone only while the stream is live.
  await expect(toggle).toHaveAttribute('data-live-status', 'live', { timeout: 10_000 })
  await expect(page.locator('[data-activity-live-status]')).toHaveCount(0)
}

async function expectStreamed(page: Page, text: string, startedAt: number): Promise<number> {
  await expect(page.getByRole('feed').getByText(text).first()).toBeVisible({ timeout: LIVE_WITHIN_MS })
  return Date.now() - startedAt
}

test.describe('Activity live tail — two contexts (LOCAL stack only)', () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test('a change made in one context reaches the other live feed within 3 s, without a reload', async ({
    browser,
    request,
  }) => {
    test.slow()
    const tokens = await devLogin(request, 'admin')
    const project = await pickProject(request, tokens.access_token)
    const streamPath = `/api/v1/projects/${project.id}/activity/stream`

    const contextA = await signedInContext(browser, tokens, project)
    const contextB = await signedInContext(browser, tokens, project)
    let viewId: string | null = null
    try {
      const watcher = await contextB.newPage()
      const streamRequests: Request[] = []
      watcher.on('request', (req) => {
        if (new URL(req.url()).pathname === streamPath) streamRequests.push(req)
      })
      let navigations = 0
      watcher.on('framenavigated', (frame) => {
        if (frame === watcher.mainFrame()) navigations += 1
      })
      await openLiveFeed(watcher)
      const navigationsWhenLive = navigations

      // The stream: a fetch with the bearer in a HEADER, no token (or anything) in the URL.
      expect(streamRequests.length).toBeGreaterThanOrEqual(1)
      const streamUrl = new URL(streamRequests[0].url())
      expect(streamUrl.pathname).toBe(streamPath)
      expect(streamUrl.search).toBe('')
      // The SPA may have refreshed its token since sign-in: any bearer, but in the header.
      expect(streamRequests[0].headers()['authorization']).toMatch(/^Bearer [\w-]+\.[\w-]+\.[\w-]+$/)

      // Context A makes the change: one saved view, created then deleted (two true rows).
      const label = `Live view ${Date.now().toString(36)}`
      let startedAt = Date.now()
      const created = await contextA.request.post(`${BACKEND_URL}/api/v1/saved-views`, {
        headers: bearer(tokens.access_token),
        data: { project_id: project.id, name: label, filters: {} },
      })
      expect(created.status()).toBe(201)
      viewId = ((await created.json()) as { id: string }).id
      const createdAfter = await expectStreamed(watcher, `Saved view ${label} was created`, startedAt)

      startedAt = Date.now()
      const deleted = await contextA.request.delete(`${BACKEND_URL}/api/v1/saved-views/${viewId}`, {
        headers: bearer(tokens.access_token),
      })
      expect(deleted.status()).toBe(204)
      viewId = null
      const deletedAfter = await expectStreamed(watcher, `Saved view ${label} was deleted`, startedAt)

      // Newest first, at the top; no reload happened; still streaming.
      const feed = watcher.getByRole('feed')
      await expect(feed.locator('li').first()).toContainText(`Saved view ${label} was deleted`)
      expect(navigations).toBe(navigationsWhenLive)
      await expect(watcher.locator('button[data-activity-live]')).toHaveAttribute('data-live-status', 'live')
      test.info().annotations.push({
        type: 'latency',
        description: `created ${createdAfter} ms, deleted ${deletedAfter} ms`,
      })
    } finally {
      if (viewId) {
        await request.delete(`${BACKEND_URL}/api/v1/saved-views/${viewId}`, {
          headers: bearer(tokens.access_token),
        })
      }
      await contextA.close()
      await contextB.close()
    }
  })
})
