/**
 * GAP-D06 (test-coverage round 2026-11-18, card TEST-D), hermetic: `/live` (Runs › Live, every role) had nine vitest
 * files and no browser journey. As a VIEWER and as a TESTER (one browser context each):
 *
 *  - with no session in the window, the table says exactly "No sessions in the last 30 days. Start a test run with
 *    the client SDK." (D-94: it said "No active execution sessions") and the header "No active runs" / "Stream is
 *    connected — waiting for the first run";
 *  - with one running and one completed session, both rows are listed (by build label, linked to the run; D-88: the
 *    running one has no "Run #N" yet, the finished one keeps it as a muted ordinal), the header counts
 *    one active run, no empty line shows; a search that matches nothing says "No sessions match the current filter.";
 *  - no toast and no write, and the page's socket (`/ws/live/{project}`) authenticates with the stored token.
 *
 * Harness: the fail-closed router of `tests/lib/production-pages.ts` (opened step by step here so the page's own
 * socket can be answered: `mockApi` closes every socket and records it as unhandled, and a route registered after it
 * wins). Pinned clock, seeded session, every request answered by a fixture.
 */
import { expect, test, type Page, type WebSocketRoute } from '@playwright/test'
import {
  assertHermetic,
  freezeClock,
  mockApi,
  seedSession,
  stillMotion,
  watchPageErrors,
  type ApiHandlers,
} from '../lib/production-pages'
import { networkQuiet, observed, requestsTo } from '../lib/rollout'
import { LAYOUT, NOW, PROJECT_ID, RUNS, USER } from '../visual/production/fixtures'
import { TESTER, VIEWER } from '../visual/production/fixtures-defects'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const ago = (seconds: number) => new Date(NOW.getTime() - seconds * 1000).toISOString()

/** One run still sending results (its last event 10 s ago, so not idle), one finished an hour ago. */
const RUNNING = {
  run_id: 'ci-live-run-0001',
  test_run_id: '22222222-2222-4222-8222-0000000000a1',
  project_id: P,
  build_number: 'build-1042',
  status: 'running',
  total: 40,
  passed: 30,
  failed: 2,
  skipped: 0,
  broken: 0,
  pass_rate: 93.8,
  current_test: 'test_checkout_total',
  started_at: ago(120),
  last_event_at: ago(10),
  suite_name: 'checkout',
  run_seq: 12,
}
const COMPLETED = {
  run_id: 'ci-live-run-0000',
  test_run_id: '22222222-2222-4222-8222-0000000000a0',
  project_id: P,
  build_number: 'build-1041',
  status: 'completed',
  total: 38,
  passed: 38,
  failed: 0,
  skipped: 0,
  broken: 0,
  pass_rate: 100,
  started_at: ago(3900),
  last_event_at: ago(3600),
  completed_at: ago(3600),
  suite_name: 'checkout',
  run_seq: 11,
}

type Session = typeof RUNNING | typeof COMPLETED

/** `GET /api/v1/stream/active`: the page's own read (`days` = the window) and the sidebar's live dot (`days=1`). */
function liveWorld(sessions: Session[]): ApiHandlers {
  return [
    ['/api/v1/stream/active', () => ({ sessions, count: sessions.length })],
    // The pass-rate bands: no policy anywhere, so the default bands (the server answers `null`).
    [`/api/v1/release-gate-policies/effective/${P}`, () => null],
    ...LAYOUT,
    // The suite picker's options (`useSuiteOptions`: the last 7 days' runs).
    ...RUNS,
  ]
}

interface OpenedLive {
  api: Awaited<ReturnType<typeof mockApi>>
  errors: string[]
  frames: string[]
}

/** `openProductionPage`, step by step, with the page's socket answered: it accepts, reads the auth frame, acks it. */
async function openLive(page: Page, sessions: Session[], user: typeof USER): Promise<OpenedLive> {
  const errors = watchPageErrors(page)
  await seedSession(page, { theme: 'signal', user, projectId: P })
  await freezeClock(page, NOW)
  await stillMotion(page)
  const api = await mockApi(page, user, liveWorld(sessions))
  const frames: string[] = []
  await page.routeWebSocket(
    (url) => url.hostname === '127.0.0.1' && url.pathname === `/ws/live/${P}`,
    (ws: WebSocketRoute) => {
      ws.onMessage((message) => {
        const text = String(message)
        frames.push(text)
        if ((JSON.parse(text) as { type?: string }).type === 'auth') ws.send(JSON.stringify({ type: 'connected' }))
      })
    },
  )
  await page.goto('/live')
  expect(new URL(page.url()).pathname, 'the route redirected').toBe('/live')
  await expect(page.getByRole('heading', { level: 1, name: 'Live Execution', exact: true })).toBeVisible({ timeout: 20_000 })
  return { api, errors, frames }
}

const toasts = (page: Page) => page.locator('div[style*="z-index: 9999"] [role="status"]')
const sessionsTable = (page: Page) => page.locator('#main-content table').filter({ has: page.getByRole('columnheader', { name: /^Build/ }) })
const bodyRows = (page: Page) => sessionsTable(page).locator('tbody tr')

async function expectCleanEnd(page: Page, opened: OpenedLive, role: string) {
  await networkQuiet(page, opened.api)
  await expect(toasts(page), `/live as ${role}: a toast`).toHaveCount(0)
  expect(observed(opened.api).filter((line) => !line.startsWith('GET ')), `/live as ${role}: a write`).toEqual([])
  // The page's own read: the active project, the stored 30-day window (the sidebar's dot asks `days=1`).
  expect(requestsTo(opened.api, '/api/v1/stream/active'), 'the page read its sessions').toContain(
    `GET /api/v1/stream/active?project_id=${P}&days=30`,
  )
  const auth = opened.frames.map((f) => JSON.parse(f) as { type?: string; token?: string }).filter((f) => f.type === 'auth')
  expect(auth.length, 'the socket authenticated').toBeGreaterThan(0)
  expect(auth[0].token, 'with the stored token').toBe('access')
  assertHermetic(opened.api, opened.errors)
}

for (const user of [VIEWER, TESTER]) {
  test(`/live as ${user.role} with no session: the empty line tells how to start a run`, async ({ page }) => {
    const opened = await openLive(page, [], user)
    await expect(bodyRows(page)).toHaveCount(1)
    await expect(bodyRows(page)).toHaveText('No sessions in the last 30 days. Start a test run with the client SDK.')
    await expect(page.getByText('No active runs', { exact: true })).toBeVisible()
    await expect(page.getByText('Stream is connected — waiting for the first run', { exact: true })).toBeVisible()
    await expectCleanEnd(page, opened, user.role)
  })

  test(`/live as ${user.role} with sessions: both rows listed, one active run; a search with no match says so`, async ({ page }) => {
    const opened = await openLive(page, [RUNNING, COMPLETED], user)
    await expect(bodyRows(page)).toHaveCount(2)
    await expect(page.getByText('No sessions in the last 30 days. Start a test run with the client SDK.')).toHaveCount(0)
    // Newest first (sorted by start): the running run, then the finished one, each linked to its run.
    await expect(bodyRows(page).nth(0).getByRole('link', { name: 'build-1042', exact: true })).toHaveAttribute('href', `/runs/${RUNNING.test_run_id}`)
    await expect(bodyRows(page).nth(1).getByRole('link', { name: 'build-1041', exact: true })).toHaveAttribute('href', `/runs/${COMPLETED.test_run_id}`)
    await expect(bodyRows(page).nth(0)).not.toContainText('Run #12')
    await expect(bodyRows(page).nth(1)).toContainText('Run #11')
    await expect(bodyRows(page).nth(0)).toContainText('running')
    await expect(bodyRows(page).nth(0)).toContainText('93.8%')
    await expect(bodyRows(page).nth(1)).toContainText('completed')
    await expect(bodyRows(page).nth(1)).toContainText('100.0%')
    await expect(page.getByText('1 active run', { exact: true })).toBeVisible()

    await page.getByPlaceholder('Search runs…').fill('no-such-build-zzz')
    await expect(bodyRows(page)).toHaveCount(1)
    await expect(bodyRows(page)).toHaveText('No sessions match the current filter.')
    await page.getByPlaceholder('Search runs…').fill('')
    await expect(bodyRows(page)).toHaveCount(2)
    await expectCleanEnd(page, opened, user.role)
  })
}
