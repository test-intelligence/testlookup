/**
 * Live verification for the /overview first-run guide gate (#881).
 *
 * Reported: selecting a window with no runs in it renders the new-user
 * onboarding guide on an established project. On this deployment every one of
 * the four projects is a subject -- all have runs, none in the last 24h:
 *
 *     Auth Service        last run 2026-08-05
 *     Checkout Service    last run 2026-08-07   <- the one in the report
 *     Inventory Service   last run 2026-08-05
 *     Payment Service     last run 2026-08-05
 *
 * The unit tests cover the gate; this asserts it against the real bundle, real
 * data and a real browser, because the bug was only ever visible there.
 */
import { test, expect, request as playwrightRequest } from '@playwright/test'

const BASE = 'http://testlookup.local'
const PROJECT = { id: '2aefa4fa', name: 'Checkout Service' }

async function login(page) {
  await page.goto(`${BASE}/login`)
  await page.fill('input[name="username"], input[type="text"]', 'admin')
  await page.fill('input[type="password"]', 'Admin@2026!')
  await page.click('button[type="submit"]')
  await page.waitForURL(/\/(overview|dashboard|$)/, { timeout: 20000 })
}

test('an established project with an empty window is not greeted as new', async ({ page }) => {
  await login(page)

  // Resolve the real project id through the API rather than guessing which
  // localStorage key holds the browser's token -- guessing it produced an
  // unauthenticated fetch and an empty list, which looks exactly like "the
  // project does not exist".
  const api = await playwrightRequest.newContext({ baseURL: BASE })
  const auth = await api.post('/api/v1/auth/login', {
    form: { username: 'admin', password: 'Admin@2026!' },
  })
  expect(auth.ok(), 'API login failed').toBeTruthy()
  const token = (await auth.json()).access_token
  const listed = await api.get('/api/v1/projects?size=200', {
    headers: { Authorization: `Bearer ${token}` },
  })
  const payload = await listed.json()
  const items = Array.isArray(payload) ? payload : (payload.items ?? [])
  const projectId = items.find((p) => p.name === PROJECT.name)?.id ?? null
  expect(projectId, `${PROJECT.name} must exist on this deployment`).toBeTruthy()

  // It must genuinely be the bug's subject: runs exist, but none in 24h.
  const auth2 = { Authorization: `Bearer ${token}` }
  const everRuns = await (await api.get(
    `/api/v1/runs?project_id=${projectId}&page=1&size=1`, { headers: auth2 })).json()
  const windowRuns = await (await api.get(
    `/api/v1/runs?project_id=${projectId}&page=1&size=1&days=1`, { headers: auth2 })).json()
  expect(everRuns.items?.length, 'project must HAVE runs, or this proves nothing')
    .toBeGreaterThan(0)
  expect(windowRuns.items?.length ?? 0, 'the 24h window must be empty').toBe(0)
  await api.dispose()

  await page.evaluate((id) => {
    localStorage.setItem('testlookup-active-project', JSON.stringify({
      state: { activeProjectId: id, activeProject: { id, name: 'Checkout Service' } },
      version: 0,
    }))
    // 24h -- the window in the report.
    localStorage.setItem('testlookup-time-window', JSON.stringify({
      state: { days: 1 }, version: 3,
    }))
  }, projectId)

  await page.goto(`${BASE}/overview`)
  await page.waitForTimeout(8000)

  const body = await page.locator('body').innerText()

  // The bug: the new-user guide on a project with 20 days of history.
  expect(body, 'the onboarding guide rendered on an established project')
    .not.toMatch(/Welcome to TestLookup/i)
  expect(body).not.toMatch(/No test runs here yet/i)

  // And the correct message IS shown -- the guide must be replaced, not just
  // suppressed, or the page goes silent about why it is empty.
  const banner = page.getByTestId('overview-empty-window')
  await expect(banner).toBeVisible()
  expect(await banner.innerText()).toMatch(/No runs in the last/i)
})
