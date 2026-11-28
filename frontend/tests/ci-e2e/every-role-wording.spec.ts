/**
 * GAP-D09 (test-coverage round 2026-11-18, card TEST-D), hermetic: the exact words a reader of any role sees when
 * the backend or a page's read is not there, which no browser test pinned (the unit tests match fragments). Each
 * sentence obeys `frontend.load-error-wording` (`scripts/quality_gate.py`: a failed read is "Could not load …").
 *
 *  - the shell's health banner (`DegradedBanner.tsx`), on every page: the health poll failing ("Can't reach the
 *    TestLookup backend. …"), a critical store down, an optional dependency down — as a VIEWER;
 *  - Suites when its list read fails: "Could not load suites" / "Check the backend logs or try again.";
 *  - Ask AI with no conversation: "No conversations in this project yet"; in ML and in Rules mode, the guidance the
 *    direct URL promises (`App.tsx`: the route stays registered "so a direct URL renders the page's own 'switch mode'
 *    guidance") — as a VIEWER;
 *  - Projects with none: "No projects yet" / "Create your first project to start ingesting test reports" (a QA lead,
 *    the lowest role the page admits).
 *
 * Enterprise roles E00-S06: the backend-unreachable banner is checked as every role a person can hold (the five
 * ranks and the four job roles; AGENT signs in only with keys, never to the app), and the VIEWER sentences as each
 * job role too — a job role ranks as VIEWER until a page names a permission, so it reads exactly what a viewer reads.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). `/health/details` is served at the platform root,
 * outside the harness's `/api` router, so each test answers it here with `page.route` (otherwise the dev server's
 * page shell answers it and the banner stays quiet).
 */
import { expect, test, type Page } from '@playwright/test'
import { respond, type ApiHandlers } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout } from '../lib/rollout'
import { LAYOUT, OVERVIEW_ON, USER } from '../visual/production/fixtures'
import { chatDeleteWorld, suitesInPageWorld } from '../visual/production/fixtures-in-page-b'
import { TESTER, VIEWER } from '../visual/production/fixtures-defects'
import { ADMIN, QA_ENGINEER } from '../visual/production/fixtures-releases'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const h1 = (name: string) => (p: Page) => p.getByRole('heading', { level: 1, name, exact: true })

const jobUser = (role: string, n: number, additional_roles: string[] = []) => ({
  ...VIEWER,
  id: `00000000-0000-4000-8000-0000000000c${n}`,
  username: role.toLowerCase(),
  full_name: role.replace('_', ' '),
  role,
  additional_roles,
})

/** The job roles, and a developer who is also a release manager (extra roles add no rank). */
const JOB_READERS: Array<[string, typeof VIEWER]> = [
  ['RELEASE_MANAGER', jobUser('RELEASE_MANAGER', 1)],
  ['PROJECT_MANAGER', jobUser('PROJECT_MANAGER', 2)],
  ['PRODUCT_MANAGER', jobUser('PRODUCT_MANAGER', 3)],
  ['DEVELOPER', jobUser('DEVELOPER', 4)],
  ['DEVELOPER+RELEASE_MANAGER', jobUser('DEVELOPER', 5, ['RELEASE_MANAGER'])],
]

/** Every role a person can hold: the five ranks, then the job roles. */
const EVERY_PERSON_ROLE: Array<[string, typeof VIEWER]> = [
  ['VIEWER', VIEWER],
  ['TESTER', TESTER],
  ['QA_ENGINEER', QA_ENGINEER],
  ['QA_LEAD', USER],
  ['ADMIN', ADMIN],
  ...JOB_READERS,
]

/** VIEWER and every job role: the same sentences. */
const VIEWER_READERS: Array<[string, typeof VIEWER]> = [['VIEWER', VIEWER], ...JOB_READERS]

/** Answer the shell's health poll (`GET /health/details`): a status and body, or a network failure. */
async function answerHealth(page: Page, answer: { status: number; body?: unknown } | 'unreachable') {
  await page.route((url) => url.pathname === '/health/details', (route) => {
    if (answer === 'unreachable') return route.abort('connectionrefused')
    return route.fulfill({ status: answer.status, contentType: 'application/json', body: JSON.stringify(answer.body ?? {}) })
  })
}

const health = (checks: Record<string, { status: string }>) => ({
  status: 'degraded',
  version: '0.0.0-ci',
  env: 'test',
  uptime_seconds: 60,
  timestamp: '2026-09-18T12:00:00Z',
  checks,
})

test.describe('the health banner, on every page', () => {
  for (const [role, user] of EVERY_PERSON_ROLE) {
    test(`the health poll fails: "Can't reach the TestLookup backend." and why the page may be empty — ${role}`, async ({ page }) => {
      await answerHealth(page, 'unreachable')
      const { api, errors } = await openRollout(page, '/overview', { handlers: OVERVIEW_ON, ready: h1('Dashboard'), user })
      const banner = page.getByTestId('health-unreachable-banner')
      await expect(banner).toHaveText(
        "Can't reach the TestLookup backend. Health status is unknown, not healthy — pages may show stale data or none at all. Retrying automatically.",
      )
      await expect(banner).toHaveAttribute('role', 'alert')
      await networkQuiet(page, api)
      assertHermetic(api, errors)
    })
  }

  test('a critical store is down: the store by name, and no promise of a fallback', async ({ page }) => {
    await answerHealth(page, { status: 200, body: health({ postgres: { status: 'error' }, mongo: { status: 'ok' }, redis: { status: 'ok' } }) })
    const { api, errors } = await openRollout(page, '/overview', { handlers: OVERVIEW_ON, ready: h1('Dashboard'), user: VIEWER })
    const banner = page.getByTestId('health-critical-banner')
    await expect(banner).toHaveText(
      'Critical services unreachable: PostgreSQL. Core data is unavailable — pages may be empty or fail to load until these recover.',
    )
    await expect(page.getByText('show data from PostgreSQL where possible')).toHaveCount(0)
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })

  test('an optional dependency is down: "Degraded:" with the names and the PostgreSQL fallback', async ({ page }) => {
    await answerHealth(page, { status: 200, body: health({ postgres: { status: 'ok' }, minio: { status: 'unavailable' }, chromadb: { status: 'timeout' }, ollama: { status: 'skipped' } }) })
    const { api, errors } = await openRollout(page, '/overview', { handlers: OVERVIEW_ON, ready: h1('Dashboard'), user: VIEWER })
    const banner = page.locator('[role="status"]').filter({ hasText: /^Degraded:/ })
    await expect(banner).toHaveText('Degraded: MinIO, ChromaDB unreachable. Pages relying on these will show data from PostgreSQL where possible.')
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })
})

for (const [role, user] of VIEWER_READERS) {
  test(`Suites, its list read refused: "Could not load suites" and what to do — ${role}`, async ({ page }) => {
    const handlers: ApiHandlers = [
      ['/api/v1/suites', () => respond(500, { detail: 'ci-suites-read-failed-test-only' })],
      ...suitesInPageWorld().handlers,
    ]
    // Ready on any "Could not load …" line, so a reworded title fails on the words below, not on the load.
    const loadError = (p: Page) => p.getByText(/^Could not load\b/)
    const { api, errors } = await openRollout(page, '/suites', { handlers, ready: loadError, user })
    await expect(loadError(page), 'Suites: the failed read\'s title').toHaveText('Could not load suites')
    await expect(page.getByText('Check the backend logs or try again.', { exact: true })).toBeVisible()
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })
}

test.describe('Ask AI, as a VIEWER', () => {
  const chatWorld = (mode: 'llm' | 'ml' | 'rules', sessions: unknown[] = []): ApiHandlers => [
    ['/api/v1/settings/ai/mode', () => ({ analysis_mode: mode })],
    ['/api/v1/chat/sessions', () => sessions],
    ...chatDeleteWorld().handlers,
  ]
  const ready = (p: Page) => p.getByRole('heading', { name: 'Ask about Checkout' })

  for (const [role, user] of VIEWER_READERS) {
    test(`no conversation yet: "No conversations in this project yet" — ${role}`, async ({ page }) => {
      const { api, errors } = await openRollout(page, '/chat', { handlers: chatWorld('llm'), ready, user, flags: { ask_ai_chat: true } })
      await expect(page.getByText('No conversations in this project yet', { exact: true })).toBeVisible()
      await expect(page.getByTestId('chat-session')).toHaveCount(0)
      await networkQuiet(page, api)
      assertHermetic(api, errors)
    })
  }

  for (const [mode, name] of [['ml', 'ML'], ['rules', 'Rules']] as const) {
    test(`${name} mode: the direct URL says chat is unavailable and how to switch`, async ({ page }) => {
      const { api, errors } = await openRollout(page, '/chat', { handlers: chatWorld(mode), ready, user: VIEWER, flags: { ask_ai_chat: true } })
      await expect(page.getByText(`Chat is unavailable in ${name} mode. Switch to LLM or Auto mode in Settings > AI Configuration.`, { exact: true })).toBeVisible()
      await expect(page.getByTestId('chat-input')).toHaveCount(0)
      await networkQuiet(page, api)
      assertHermetic(api, errors)
    })
  }
})

test('Projects with none: "No projects yet" and the next step (a QA lead)', async ({ page }) => {
  const handlers: ApiHandlers = [['/api/v1/projects', () => []], ...LAYOUT]
  const { api, errors } = await openRollout(page, '/projects', { handlers, ready: h1('Projects'), user: USER })
  await expect(page.getByText('No projects yet', { exact: true })).toBeVisible()
  await expect(page.getByText('Create your first project to start ingesting test reports', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})
