/**
 * GAP-D10 + GAP-D11 (test-coverage round 2026-11-18, card TEST-D), hermetic.
 *
 * 1. A QA lead, the lowest role `ManagementGuard` admits (`App.tsx`, `canAccessManagement` = QA lead+), opens the
 *    management pages no QA-lead crawl loaded (`/settings/storage` was opened as ADMIN only; `/settings/agent-activity`
 *    and `/settings/seed-data` by nobody): each draws its own state with no toast, no write at load, and none of the
 *    ADMIN-only reads the round's scan listed (`run/agents/GAP-D/guarded_gets.tsv`).
 * 2. A TESTER is sent to `/overview` from every path of `managementRoutes` (`frontend/src/App.tsx`): the redirect
 *    was asserted for 4 of the 33, and nothing pinned it for `/releases/:releaseId`, `/settings` and
 *    `/policies/:policyId`. The page never mounts, so none of its reads is made.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock) for part 1; for part 2 the same fail-closed router
 * (`tests/lib/production-pages.ts`) opened step by step, because `openProductionPage` fails on any redirect.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import {
  assertHermetic,
  freezeClock,
  mockApi,
  seedSession,
  stillMotion,
  watchPageErrors,
  type ApiHandlers,
} from '../lib/production-pages'
import { networkQuiet, observed, openRollout, requestsTo } from '../lib/rollout'
import { LAYOUT, NOW, OVERVIEW_ON, PROJECT_ID, USER } from '../visual/production/fixtures'
import { embeddingWorld } from '../visual/production/fixtures-search'
import { notificationsWorld } from '../visual/production/fixtures-notify-flaky'
import { aiAgentsWorld } from '../visual/production/fixtures-agent-actions'
import { integrationsWorld } from '../visual/production/fixtures-integrations-runtime'
import { TESTER } from '../visual/production/fixtures-defects'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const QA_LEAD = USER
const h1 = (name: string) => (p: Page) => p.getByRole('heading', { level: 1, name, exact: true })
const toasts = (page: Page) => page.locator('div[style*="z-index: 9999"] [role="status"]')

/** ADMIN-only GETs (`require_role(UserRole.ADMIN)`) a management page could read; a QA lead's load asks none. */
const ADMIN_ONLY_READS = [
  /^\/api\/v1\/settings\/secret-providers/,
  /^\/api\/v1\/performance\/slow-queries/,
  /^\/api\/v1\/audit-dashboard\/export/,
  /^\/api\/v1\/ai-eval\/candidate-options/,
  /^\/api\/v1\/settings\/retention-defaults/,
  /^\/api\/v1\/settings\/review-gate/,
  /^\/api\/v1\/identity\//,
  /^\/api\/v1\/sso\/configs/,
]

const AUDIT_CATEGORIES = [
  { key: 'access', description: 'Role and membership changes' },
  { key: 'settings', description: 'Configuration and secret changes' },
]

const PERFORMANCE: ApiHandlers = [
  ['/api/v1/performance/budgets', () => ({ latency_budgets: [], throughput_budgets: [], scale_scenarios: [] })],
  ['/api/v1/performance/search-config', () => ({ index_batch_size: 500, incremental_limit: 5000, query_timeout_ms: 1500, max_results: 100, pg_pool_size: 20, pg_max_overflow: 10, pg_pool_recycle: 1800, celery_worker_concurrency: 4 })],
  ['/api/v1/performance/observed', () => ({ window: '1h', source: 'in_app', measured: false, slot_seconds: 300, budgets: [] })],
]

interface LeadRoute {
  route: string
  handlers: () => ApiHandlers
  ready: (page: Page) => Locator
  /** The page's own read, made once at least (proves the page, not only the shell, loaded). */
  reads?: string
}

const LEAD_ROUTES: LeadRoute[] = [
  { route: '/settings/storage', handlers: () => embeddingWorld().handlers, ready: h1('Data & Storage') },
  { route: '/settings/ai', handlers: () => embeddingWorld().handlers, ready: h1('AI Configuration'), reads: '/api/v1/settings/ai' },
  {
    route: '/settings/agent-activity',
    handlers: () => [
      [`/api/v1/projects/${P}/agent-runs`, () => ({ items: [], total: 0, limit: 25, offset: 0 })],
      // The page's Fix attempts section (`FixAttemptsSection`): none yet.
      [`/api/v1/projects/${P}/fixer/attempts`, () => ({ items: [], total: 0 })],
      // The section's Fixer config read (`agent-configs/fixer`), as the AI Agents page's world answers it.
      ...aiAgentsWorld().handlers,
      ...LAYOUT,
    ],
    ready: (p) => p.getByRole('heading', { name: 'No agent activity yet', exact: true }),
    reads: `/api/v1/projects/${P}/agent-runs`,
  },
  {
    route: '/settings/seed-data',
    handlers: () => [['/api/v1/dev/seed/status', () => ({ seeded: false })], ...LAYOUT],
    ready: (p) => p.getByText('No seed data present'),
    reads: '/api/v1/dev/seed/status',
  },
  { route: '/settings/notifications', handlers: () => notificationsWorld().handlers, ready: (p) => p.locator('[data-page-header] h1') },
  {
    route: '/settings/audit',
    handlers: () => [
      ['/api/v1/audit-dashboard/categories', () => AUDIT_CATEGORIES],
      ['/api/v1/audit-dashboard/events', () => ({ total: 0, items: [] })],
      ...LAYOUT,
    ],
    ready: h1('Audit Dashboard'),
    reads: '/api/v1/audit-dashboard/events',
  },
  { route: '/settings/performance', handlers: () => [...PERFORMANCE, ...LAYOUT], ready: (p) => p.locator('[data-page-header] h1'), reads: '/api/v1/performance/budgets' },
  { route: '/settings/mfa-policy', handlers: () => LAYOUT, ready: (p) => p.getByText('Admin access required') },
  { route: '/settings/ai-agents', handlers: () => aiAgentsWorld().handlers, ready: (p) => p.getByTestId('agent-policy-investigator') },
  { route: '/settings/integrations', handlers: () => integrationsWorld().handlers, ready: h1('Integrations') },
]

test.describe('a QA lead opens the management pages no crawl loaded as a QA lead', () => {
  for (const r of LEAD_ROUTES) {
    test(`${r.route} as QA_LEAD: its own state, no toast, no write, no ADMIN-only read`, async ({ page }) => {
      const { api, errors } = await openRollout(page, r.route, { handlers: r.handlers(), ready: r.ready, user: QA_LEAD })
      await networkQuiet(page, api)
      await expect(toasts(page), `${r.route} as QA_LEAD: a toast at load`).toHaveCount(0)
      expect(observed(api).filter((line) => !line.startsWith('GET ')), `${r.route} as QA_LEAD: a write at load`).toEqual([])
      for (const admin of ADMIN_ONLY_READS) {
        expect(requestsTo(api, admin), `${r.route} as QA_LEAD: an ADMIN-only read (${admin.source})`).toEqual([])
      }
      if (r.reads) expect(requestsTo(api, r.reads).length, `${r.route}: the page read ${r.reads}`).toBeGreaterThan(0)
      assertHermetic(api, errors)
    })
  }
})

/**
 * Every path of `managementRoutes` (`frontend/src/App.tsx`), with the dynamic segments filled in. Kept in the
 * file's order so a new management route is easy to add here.
 */
const MANAGEMENT_PATHS = [
  '/projects',
  '/releases',
  '/releases/33333333-3333-4333-8333-0000000000r1',
  '/activity',
  '/users',
  '/settings',
  '/settings/notifications',
  '/settings/team-channels',
  '/settings/ai',
  '/settings/review-gate',
  '/settings/integrations',
  '/settings/storage',
  '/settings/sso',
  '/settings/digests',
  '/settings/integration-health',
  '/settings/audit',
  '/settings/ai-eval',
  '/settings/performance',
  '/settings/seed-data',
  '/settings/feature-flags',
  '/settings/billing',
  '/settings/github',
  '/settings/gitlab',
  '/settings/webhooks',
  '/settings/api-keys',
  '/settings/project-data',
  '/settings/retention',
  '/settings/mfa-policy',
  '/settings/ai-agents',
  '/settings/agent-activity',
  '/policies',
  '/policies/new',
  '/ownership',
]

test('the list above has the 33 management paths', () => {
  expect(MANAGEMENT_PATHS).toHaveLength(33)
  expect(new Set(MANAGEMENT_PATHS).size).toBe(33)
})

test.describe('a TESTER is sent to /overview from every management path', () => {
  for (const path of MANAGEMENT_PATHS) {
    test(`${path} as TESTER: redirected to /overview, the page never mounts`, async ({ page }) => {
      const errors = watchPageErrors(page)
      await seedSession(page, { theme: 'signal', user: TESTER, projectId: P })
      await freezeClock(page, NOW)
      await stillMotion(page)
      const api = await mockApi(page, TESTER, OVERVIEW_ON)
      await page.goto(path)
      await expect
        .poll(() => new URL(page.url()).pathname, { message: `${path} as TESTER: where the guard sent the reader`, timeout: 20_000 })
        .toBe('/overview')
      await expect(page.getByRole('heading', { level: 1, name: 'Dashboard', exact: true })).toBeVisible({ timeout: 20_000 })
      await expect(page.getByText('Page not found')).toHaveCount(0)
      await networkQuiet(page, api)
      await expect(toasts(page), `${path} as TESTER: a toast`).toHaveCount(0)
      assertHermetic(api, errors)
    })
  }
})
