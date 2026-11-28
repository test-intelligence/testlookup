/**
 * GAP-D05 (test-coverage round 2026-11-18, card TEST-D), hermetic: the every-role routes as a TESTER and as a VIEWER.
 *
 * Enterprise roles E00-S06: and as each job role (RELEASE_MANAGER, PROJECT_MANAGER, PRODUCT_MANAGER, DEVELOPER)
 * and a DEVELOPER who also holds RELEASE_MANAGER. Until a route names a permission, a job role ranks as VIEWER, so
 * each must be offered exactly what a VIEWER is offered (and load every page with no toast).
 *
 * `in-page-sweep.spec.ts` crawls VIEWER, QA ENGINEER, QA LEAD and ADMIN; nothing loads these pages as a TESTER, the
 * role with its own UI (`TestManagementPage.tsx` `canWrite = hasRole('TESTER')`) just under the QA-engineer floor many
 * endpoints use. For every route below, as each of the two roles (one browser context per test, so one account per
 * context):
 *
 *  - the page loads with no toast (every status but 401/404 toasts, `services/apiErrors.ts`, so a read the role may
 *    not make shows here), no write is sent at load, and every request is answered by a fixture (fail closed);
 *  - every action listed for the route is usable exactly when the role reaches the API's floor for it (the floor is
 *    cited beside each action): below the floor the control is absent, hidden or disabled; at or above it the control
 *    is there and enabled. Nothing is clicked.
 *
 * Out of scope here, on purpose (each is a known leak, reported with a failing test in the round's findings):
 *  - `/test-management?tab=approvals` and `/my-failures?tab=approvals&source=cases`: Approve / Request changes /
 *    Reject offered to a VIEWER, API floor TESTER (BUG-D01, GAP-D01);
 *  - `/defects?tab=jira`: the Jira bridge tab reads `/settings/integrations`, QA lead+ (BUG-D02, GAP-D02);
 *  - `/test-management?tab=knowledge`: Sync / Retrieve / Generate offered below QA engineer (BUG-D03, GAP-D03);
 *  - the "Analysis report" items of the Summary report's ⋯ menu, API floor QA engineer (BUG-D04, GAP-D04): the
 *    page's load is crawled, the menu is not.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: the worlds `in-page-sweep.spec.ts` loads.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { landmark, type ApiHandlers, type FlagMap } from '../lib/production-pages'
import { assertHermetic, networkQuiet, observed, openRollout } from '../lib/rollout'
import {
  COVERAGE_ON,
  FAILURES_ON,
  GATE_CLUSTERS,
  OVERVIEW_ON,
  releaseGateOn,
  RUN_ID,
  RUNS_PAGE,
  SUITE_ID,
  SUMMARY_REPORT_ON,
  TRENDS_ON,
  VALUE_METRICS,
} from '../visual/production/fixtures'
import { RUN_PAGE } from '../visual/production/fixtures-run'
import { agentsHandlers, flakyHandlers, TEST_CASE, TEST_CASE_NAME, TEST_CASE_PATH, withGateBlockers } from '../visual/production/fixtures-pages'
import {
  CANONICAL_ID,
  chatDeleteWorld,
  keysInPageWorld,
  LEGACY_SUITE_NAME,
  lifecycleHostsWorld,
  METHODOLOGY_HANDLERS,
  notificationsDeleteWorld,
  SEARCH_PAGE,
  suitesInPageWorld,
  testLibraryWorld,
  tmCaseId,
  tmCaseTitle,
} from '../visual/production/fixtures-in-page-b'
import { EXPLORE, trendsReportWorld } from '../visual/production/fixtures-trends-coverage'
import { defectsWorld, TESTER, VIEWER } from '../visual/production/fixtures-defects'
import { attributionRulesWorld } from '../visual/production/fixtures-release-planning'

test.use({ viewport: { width: 1280, height: 1000 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

type Role = 'VIEWER' | 'TESTER' | 'QA_ENGINEER' | 'QA_LEAD' | 'ADMIN'
type JobRole = 'RELEASE_MANAGER' | 'PROJECT_MANAGER' | 'PRODUCT_MANAGER' | 'DEVELOPER'
const ORDER: Role[] = ['VIEWER', 'TESTER', 'QA_ENGINEER', 'QA_LEAD', 'ADMIN']
/** A job role's rank is VIEWER on every guard that names no permission (enterprise roles E00). */
const rankOf = (role: Role | JobRole): Role => ((ORDER as string[]).includes(role) ? (role as Role) : 'VIEWER')
const reaches = (role: Role | JobRole, floor: Role) => ORDER.indexOf(rankOf(role)) >= ORDER.indexOf(floor)

const jobUser = (role: JobRole, n: number, additional_roles: JobRole[] = []) => ({
  ...VIEWER,
  id: `00000000-0000-4000-8000-0000000000f${n}`,
  username: role.toLowerCase(),
  full_name: role.replace('_', ' '),
  role,
  additional_roles,
})

const ROLES: Array<{ role: string; rank: Role | JobRole; user: typeof VIEWER }> = [
  { role: 'TESTER', rank: 'TESTER', user: TESTER },
  { role: 'VIEWER', rank: 'VIEWER', user: VIEWER },
  { role: 'RELEASE_MANAGER', rank: 'RELEASE_MANAGER', user: jobUser('RELEASE_MANAGER', 1) },
  { role: 'PROJECT_MANAGER', rank: 'PROJECT_MANAGER', user: jobUser('PROJECT_MANAGER', 2) },
  { role: 'PRODUCT_MANAGER', rank: 'PRODUCT_MANAGER', user: jobUser('PRODUCT_MANAGER', 3) },
  { role: 'DEVELOPER', rank: 'DEVELOPER', user: jobUser('DEVELOPER', 4) },
  { role: 'DEVELOPER+RELEASE_MANAGER', rank: 'DEVELOPER', user: jobUser('DEVELOPER', 5, ['RELEASE_MANAGER']) },
]

interface CrawlAction {
  name: string
  /** The API's floor for what the control sends (`require_role`), cited at each entry. */
  floor: Role
  trigger: (page: Page) => Locator
  /** Steps before the trigger is looked for (open a menu); it may itself need a control the role lacks. */
  before?: (page: Page) => Promise<void>
}

interface CrawlRoute {
  route: string
  path: string
  handlers: () => ApiHandlers
  ready: (page: Page) => Locator
  flags?: FlagMap
  prepare?: (page: Page) => Promise<void>
  actions?: CrawlAction[]
}

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const h1 = (name: string) => (p: Page) => p.getByRole('heading', { level: 1, name, exact: true })
const headerH1 = (p: Page) => p.locator('[data-page-header] h1')
const tmReady = (p: Page) => p.getByRole('heading', { name: 'Test Case Management', level: 1 })
const attributionRow = (p: Page) => p.locator('[data-primary]').getByRole('row').filter({ hasText: 'Hotfix branches' })
const suiteRow = (p: Page) =>
  p.locator('[data-primary] tbody > tr:not([data-in-page-row])').filter({ has: p.locator('[data-suite-name]', { hasText: LEGACY_SUITE_NAME }) })

const ROUTES: CrawlRoute[] = [
  { route: '/overview', path: '/overview', handlers: () => OVERVIEW_ON, ready: h1('Dashboard') },
  { route: '/runs', path: '/runs', handlers: () => RUNS_PAGE, ready: (p) => p.locator('[data-status-banner]') },
  { route: '/runs/:id', path: `/runs/${RUN_ID}`, handlers: () => RUN_PAGE, ready: (p) => p.locator('[data-primary] table') },
  { route: '/runs/:id/tests/:testId', path: TEST_CASE_PATH, handlers: () => TEST_CASE, ready: h1(TEST_CASE_NAME) },
  { route: '/failures', path: '/failures', handlers: () => FAILURES_ON, ready: (p) => landmark(p, 'Failure verdict') },
  {
    route: '/defects',
    path: '/defects',
    handlers: () => defectsWorld().handlers,
    ready: (p) => landmark(p, 'Defect KPIs'),
    // PATCH /projects/{id}/defects/{id}: `backend/app/routers/defect_jira.py:153` (QA_ENGINEER).
    actions: [{ name: 'Defect actions', floor: 'QA_ENGINEER', trigger: (p) => p.getByRole('button', { name: 'Defect actions' }) }],
  },
  // BUG-D04: the ⋯ menu's "Analysis report" items are not checked here (load only).
  { route: '/reports/summary', path: '/reports/summary', handlers: () => SUMMARY_REPORT_ON, ready: (p) => p.getByText('Total tests', { exact: true }) },
  { route: '/flaky', path: '/flaky', handlers: () => flakyHandlers(), ready: headerH1 },
  {
    route: '/release-gate/:id',
    path: `/release-gate/${RUN_ID}`,
    handlers: () => withGateBlockers(releaseGateOn({ clusters: GATE_CLUSTERS })),
    ready: (p) => p.getByRole('meter', { name: 'Risk Score' }),
  },
  {
    route: '/releases/attribution-rules',
    path: '/releases/attribution-rules',
    handlers: () => attributionRulesWorld().handlers,
    ready: h1('Attribution rules'),
    // POST / PATCH / DELETE attribution rules: `backend/app/routers/release_attribution_rules.py:270,322,371` (QA_LEAD).
    actions: [
      { name: 'New rule', floor: 'QA_LEAD', trigger: (p) => p.getByRole('button', { name: 'New rule', exact: true }) },
      { name: 'a rule\'s Edit', floor: 'QA_LEAD', trigger: (p) => attributionRow(p).getByRole('button', { name: /^Edit/ }) },
      { name: 'a rule\'s Delete', floor: 'QA_LEAD', trigger: (p) => attributionRow(p).getByRole('button', { name: /^Delete/ }) },
    ],
  },
  { route: '/agents', path: `/agents?run=${RUN_ID}`, handlers: () => agentsHandlers(), ready: (p) => p.getByRole('region', { name: 'Run report' }) },
  {
    route: '/test-management',
    path: '/test-management',
    handlers: () => testLibraryWorld().handlers,
    ready: tmReady,
    actions: [
      // POST /test-management/cases: `backend/app/routers/test_management_cases.py:400` (TESTER).
      { name: 'New test case', floor: 'TESTER', trigger: (p) => p.getByRole('button', { name: 'New test case from catalog toolbar' }) },
      // POST /test-management/cases/ai-generate: `backend/app/routers/test_management_ai.py:40` (TESTER).
      { name: 'AI Generate', floor: 'TESTER', trigger: (p) => p.getByRole('button', { name: 'AI Generate', exact: true }) },
      // DELETE /test-management/cases/{id}: `test_management_cases.py:474` (QA_ENGINEER); retiring is a QA lead's
      // (`TestManagementPage.tsx` `canRetire`). Either way above a TESTER.
      {
        name: 'a row\'s Delete',
        floor: 'QA_LEAD',
        trigger: (p) =>
          p.locator(`[data-primary] tr[data-case-row="${tmCaseId(1)}"]`).getByRole('button', { name: new RegExp(`^(Delete|Deprecate) ${escapeRe(tmCaseTitle(1))}$`) }),
      },
    ],
  },
  // GAP-D13 (an owner question, not a leak): plans and strategy writes have no API role guard, so no action is listed.
  { route: '/test-management?tab=plans', path: '/test-management?tab=plans', handlers: () => testLibraryWorld().handlers, ready: tmReady },
  { route: '/test-management?tab=strategy', path: '/test-management?tab=strategy', handlers: () => testLibraryWorld().handlers, ready: tmReady },
  {
    route: '/test-management (evidence gaps)',
    path: '/test-management',
    handlers: () => lifecycleHostsWorld().handlers,
    ready: tmReady,
    prepare: async (p) => {
      await p.getByRole('region', { name: 'Execution evidence gaps' }).getByRole('button', { name: /^Unconfirmed automation retirements/ }).click()
    },
    // POST /canonical-test-cases/{id}/confirm-retirement: `backend/app/routers/suites.py:550` (QA_LEAD).
    actions: [{ name: 'Confirm retirement', floor: 'QA_LEAD', trigger: (p) => p.getByRole('button', { name: 'Confirm retirement' }) }],
  },
  {
    route: '/suites',
    path: '/suites',
    handlers: () => suitesInPageWorld().handlers,
    ready: h1('Test Suites'),
    actions: [
      // POST /suites: `backend/app/routers/suites.py:217` (QA_ENGINEER).
      { name: 'New suite', floor: 'QA_ENGINEER', trigger: (p) => p.getByRole('button', { name: 'New suite' }) },
      // DELETE /suites/{id}: `suites.py:331` (QA_LEAD).
      { name: 'a suite\'s Delete', floor: 'QA_LEAD', trigger: (p) => suiteRow(p).getByRole('button', { name: 'Delete' }) },
    ],
  },
  { route: '/suites/:id', path: `/suites/${SUITE_ID}`, handlers: () => suitesInPageWorld().handlers, ready: (p) => p.locator('[data-suite-tests]') },
  {
    route: '/canonical-test-cases/:id',
    path: `/canonical-test-cases/${CANONICAL_ID}`,
    handlers: () => lifecycleHostsWorld().handlers,
    ready: headerH1,
    // DELETE /canonical-test-cases/{id}/managed-link: `suites.py:529` (QA_ENGINEER).
    actions: [{ name: 'Unlink managed case', floor: 'QA_ENGINEER', trigger: (p) => p.getByRole('button', { name: 'Unlink managed case' }) }],
  },
  {
    route: '/trends',
    path: '/trends',
    handlers: () => [...trendsReportWorld().handlers, ...TRENDS_ON],
    ready: (p) => landmark(p, 'Trend metrics'),
    actions: [
      // POST /projects/{id}/timeline-notes: `backend/app/routers/timeline_notes.py:96` (QA_ENGINEER).
      { name: 'Pin a note', floor: 'QA_ENGINEER', trigger: (p) => p.locator('[data-pin-note-open="cadence"]') },
      // POST /reports/email-trends: `backend/app/routers/reports.py:165` (QA_LEAD).
      {
        name: 'Email report…',
        floor: 'QA_LEAD',
        trigger: (p) => p.getByRole('menuitem', { name: 'Email report…' }),
        before: (p) => p.locator('[data-page-header]').getByRole('button', { name: 'More actions' }).click(),
      },
    ],
  },
  { route: '/value-metrics', path: '/value-metrics', handlers: () => [...METHODOLOGY_HANDLERS, ...VALUE_METRICS], ready: (p) => p.getByRole('heading', { name: 'Hours saved per month' }) },
  { route: '/explore', path: '/explore', handlers: () => EXPLORE, ready: h1('Explorer') },
  { route: '/coverage', path: '/coverage', handlers: () => COVERAGE_ON, ready: (p) => landmark(p, 'Run cadence') },
  {
    route: '/settings/my-api-keys',
    path: '/settings/my-api-keys',
    handlers: () => keysInPageWorld().handlers,
    ready: h1('My API keys'),
    // POST /api-keys and DELETE /api-keys/{id}: `backend/app/routers/api_keys.py:65,322` (QA_ENGINEER).
    actions: [
      { name: 'Generate key', floor: 'QA_ENGINEER', trigger: (p) => p.getByRole('button', { name: 'Generate key' }) },
      { name: 'Revoke key', floor: 'QA_ENGINEER', trigger: (p) => p.getByRole('button', { name: /^Revoke key / }) },
    ],
  },
  { route: '/search', path: '/search', handlers: () => SEARCH_PAGE, ready: (p) => p.getByTestId('searchable-data-summary') },
  { route: '/chat', path: '/chat', handlers: () => chatDeleteWorld().handlers, flags: { ask_ai_chat: true }, ready: (p) => p.getByRole('heading', { name: 'Ask about Checkout' }) },
  { route: '/settings/my-notifications', path: '/settings/my-notifications', handlers: () => notificationsDeleteWorld().handlers, ready: h1('My notifications') },
]

/** react-hot-toast's toasts (its fixed container), never the page's own status lines (`in-page-sweep.spec.ts`). */
const toasts = (page: Page) => page.locator('div[style*="z-index: 9999"] [role="status"]')

async function usable(trigger: Locator): Promise<boolean> {
  if ((await trigger.count()) === 0) return false
  const first = trigger.first()
  return (await first.isVisible()) && (await first.isEnabled())
}

/**
 * Fire-and-forget usage telemetry (`onboardingService.trackEvent`, sent with `suppressToast`; opening the evidence
 * gaps panel sends it) is not a write the role makes on the project; `observed` already leaves out the beacon.
 */
const TELEMETRY = /^POST \/api\/v1\/onboarding\/track(\?|$)/
const writesOf = (lines: string[]) => lines.filter((line) => !line.startsWith('GET ') && !TELEMETRY.test(line))

for (const r of ROUTES) {
  for (const { role, rank, user } of ROLES) {
    test(`${r.route} as ${role}: no toast, no write at load, and only the actions the API lets a ${role} make`, async ({ page }) => {
      const { api, errors } = await openRollout(page, r.path, { handlers: r.handlers(), ready: r.ready, user, flags: r.flags })
      await networkQuiet(page, api)
      await expect(toasts(page), `${r.route} as ${role}: a toast at load`).toHaveCount(0)
      expect(writesOf(observed(api)), `${r.route} as ${role}: a write at load`).toEqual([])
      if (r.prepare) {
        await r.prepare(page)
        await networkQuiet(page, api)
      }
      const offered: string[] = []
      const withheld: string[] = []
      for (const action of r.actions ?? []) {
        const allowed = reaches(rank, action.floor)
        if (action.before) {
          if (allowed) await action.before(page)
          else await action.before(page).catch(() => undefined)
          await networkQuiet(page, api)
        }
        const trigger = action.trigger(page)
        if (allowed) {
          await expect(trigger.first(), `${r.route} as ${role}: ${action.name} (floor ${action.floor}) is offered`).toBeVisible()
          await expect(trigger.first(), `${r.route} as ${role}: ${action.name} is enabled`).toBeEnabled()
          offered.push(action.name)
        } else {
          expect(await usable(trigger), `${r.route} as ${role}: ${action.name} is offered, but its API floor is ${action.floor}`).toBe(false)
          withheld.push(action.name)
        }
        if (action.before) await page.keyboard.press('Escape')
      }
      console.log(`ROLE-CRAWL ${r.route} ${role} ${JSON.stringify({ offered, withheld })}`)
      await networkQuiet(page, api)
      await expect(toasts(page), `${r.route} as ${role}: a toast after the checks`).toHaveCount(0)
      expect(writesOf(observed(api)), `${r.route} as ${role}: a write`).toEqual([])
      assertHermetic(api, errors)
    })
  }
}
