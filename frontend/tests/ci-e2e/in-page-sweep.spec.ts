/**
 * W27b PAN-07 THE CRAWL (`docs/deferred-scope-2026-10-13/work/W27b-contract.md` §2.5), hermetic: no page opens an
 * overlay any more.
 *
 *  1. Every route of `fold-budget.spec.ts`'s list, plus `/activity`, `/explore`, `/projects`, `/users`, `/ownership`,
 *     `/policies`, `/search`, `/chat`, `/canonical-test-cases/:id` and every `/settings/*` page W27b touches, as
 *     VIEWER, QA ENGINEER, QA LEAD and ADMIN (every role the route lets in: a management route sends a viewer and a QA
 *     engineer elsewhere, so it is crawled as QA LEAD and ADMIN): the page loads with no overlay and no toast; then
 *     every W27b action (§2's tables) the role has today is OPENED (never submitted): no overlay (no `aria-modal`, no
 *     `role="dialog"`, no `[data-side-panel]`, no native dialog: `watchNativeDialogs`), `#main-content` not scrolling
 *     sideways; then closed by its kept control. An action outside the role's gate (`who`) must stay unusable for it:
 *     the conversion changes no gate. Fail closed: opening sends only reads the fixtures answer (`api.unhandled` is
 *     `[]`). One `SWEEP <route> <role> {"opened":[…],"gated":[…]}` line per test.
 *  2. The detail URLs (`/test-management?case=`, `/releases?open=`, `/runs/:id?test=`, `/flaky?test=`,
 *     `/activity?event=`, a catalogue `rows=`): opened, reloaded (still open, exactly one container) and, where opening
 *     pushes, closed by Back; a purged event's id shows its not-found line, a malformed one its own line, with no
 *     request.
 *  3. The OUT popovers (Views, the notifications bell, the Notes chip, the Compare MultiSelect, the run picker): opened
 *     once each, and `expectNoOverlay` holds once each is CLOSED (their `role="dialog"` is OR-03's).
 *
 * It FAILS on the modal tree by design (every action below was an overlay). Harness: `tests/lib/rollout.ts` (fail
 * closed, pinned clock). Fixtures: `fixtures-in-page-b.ts` and the worlds it composes.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { expectNoOverlay, opened, openedByHeading, watchNativeDialogs } from '../lib/inPage'
import { expectMainFits } from '../lib/inPageGeometry'
import { landmark, type ApiHandlers, type ApiRequest, type FlagMap } from '../lib/production-pages'
import { assertHermetic, bringNear, expectDrawn, networkQuiet, openRollout, requestsTo, rowsPanels, section, sectionFrame } from '../lib/rollout'
import {
  CHART_DATA_PATH,
  COVERAGE_ON,
  daysAgo,
  DEFECTS,
  FAILURES_ON,
  GATE_CLUSTERS,
  LAYOUT,
  OVERVIEW_ON,
  PROJECT_ID,
  releaseGateOn,
  RUN_ID,
  RUNS_PAGE,
  SUITE_ID,
  SUMMARY_REPORT_ON,
  TRENDS_ON,
  USER,
  VALUE_METRICS,
} from '../visual/production/fixtures'
import { RUN_PAGE } from '../visual/production/fixtures-run'
import {
  agentsHandlers,
  flakyHandlers,
  RELEASES_PAGE,
  TEST_CASE,
  TEST_CASE_NAME,
  TEST_CASE_PATH,
  withGateBlockers,
} from '../visual/production/fixtures-pages'
import { billingHandlers } from '../visual/production/fixtures-agents-billing-flags'
import {
  ACTIVITY_EVENTS,
  activityDeepLinkWorld,
  CANONICAL_ID,
  chatDeleteWorld,
  digestsDeleteWorld,
  DRAFT_POLICY,
  flagsDeleteWorld,
  gitlabInPageWorld,
  githubInPageWorld,
  keysInPageWorld,
  LEGACY_PROJECT,
  LEGACY_SUITE_NAME,
  lifecycleHostsWorld,
  METHODOLOGY_HANDLERS,
  notificationsDeleteWorld,
  OWNERSHIP_RULE_ROWS,
  ownershipInPageWorld,
  policiesInPageWorld,
  projectDataWorld,
  projectsInPageWorld,
  RECENT_SEARCHES,
  retentionInPageWorld,
  SEARCH_PAGE,
  SSO_ENFORCED,
  ssoInPageWorld,
  suitesInPageWorld,
  teamChannelsWorld,
  testLibraryWorld,
  tmCaseId,
  tmCaseTitle,
  usersInPageWorld,
  WEBHOOK,
  webhooksInPageWorld,
} from '../visual/production/fixtures-in-page-b'
import { compareCountChartData, EXPLORE, fixTaggingWorld, note, notesWorld, trendsReportWorld } from '../visual/production/fixtures-trends-coverage'
import { RUN_COMPARE_PICKER } from '../visual/production/fixtures-runs-failures'
import { ADMIN, QA_ENGINEER, VIEWER } from '../visual/production/fixtures-releases'
import { releasesInPageWorld, runAnalysisWorld } from '../visual/production/fixtures-in-page'
import { attributionRulesWorld } from '../visual/production/fixtures-release-planning'

test.use({ viewport: { width: 1280, height: 1000 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

type Role = 'VIEWER' | 'QA_ENGINEER' | 'QA_LEAD' | 'ADMIN'
const USERS: Record<Role, typeof USER> = { VIEWER, QA_ENGINEER, QA_LEAD: USER, ADMIN }
const EVERY: Role[] = ['VIEWER', 'QA_ENGINEER', 'QA_LEAD', 'ADMIN']
/** `App.tsx` `managementRoutes`: QA lead and administrator only. */
const MANAGEMENT: Role[] = ['QA_LEAD', 'ADMIN']

interface SweepAction {
  name: string
  /**
   * The roles that see the control today (default: every role the route admits). A role in the list MUST find it
   * visible and enabled and open it; a role outside it must NOT be able to use it (absent, hidden or disabled): the
   * conversion keeps every role's gate as it is.
   */
  who?: Role[]
  /** The control that opens it. */
  trigger: (page: Page) => Locator
  /** Steps before the trigger is looked for (hover a card, open a menu). */
  before?: (page: Page) => Promise<void>
  /** The opened container. */
  container: (page: Page) => Locator
  /** Close it with its kept control. */
  close: (container: Locator, page: Page) => Promise<void>
}

interface SweepRoute {
  route: string
  path: string
  roles: Role[]
  handlers: () => ApiHandlers
  ready: (page: Page) => Locator
  /** A role that sees another drawn state of the page (an admin-only page's notice, a read-only view). */
  readyFor?: Partial<Record<Role, (page: Page) => Locator>>
  flags?: FlagMap
  init?: (page: Page) => Promise<void>
  /** Steps after the load, before the actions (pick a project, expand a card). */
  prepare?: (page: Page) => Promise<void>
  actions?: SweepAction[]
}

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const h1 = (name: string) => (p: Page) => p.getByRole('heading', { level: 1, name, exact: true })
const headerH1 = (p: Page) => p.locator('[data-page-header] h1')
const tmReady = (p: Page) => p.getByRole('heading', { name: 'Test Case Management', level: 1 })
const trendsReady = (p: Page) => landmark(p, 'Trend metrics')
const button = (name: string) => (c: Locator) => c.getByRole('button', { name, exact: true }).click()
const cancel = button('Cancel')
const strip = (p: Page) => p.locator('[data-inline-confirm]')
/** A question asked in an inline strip, cancelled. */
const ask = (name: string, trigger: SweepAction['trigger'], before?: SweepAction['before']): SweepAction => ({
  name,
  trigger,
  before,
  container: strip,
  close: cancel,
})
/** A form or section found by its kept name, closed by Cancel (or `close`). */
const form = (
  name: string,
  trigger: SweepAction['trigger'],
  container: string | RegExp,
  close: SweepAction['close'] = cancel,
  before?: SweepAction['before'],
): SweepAction => ({ name, trigger, before, container: (p) => opened(p, container), close })
/** `action`, seen only by `who`. */
const only = (who: Role[], action: SweepAction): SweepAction => ({ ...action, who })
const ENGINEER_UP: Role[] = ['QA_ENGINEER', 'QA_LEAD', 'ADMIN']
const LEAD_UP: Role[] = ['QA_LEAD', 'ADMIN']
const ADMIN_ONLY: Role[] = ['ADMIN']
const adminNotice = (p: Page) => p.getByRole('heading', { name: 'Admin access required', exact: true })

/** Trends' chart-data with the Compare section's series (`in-page-reports.spec.ts`), so its toolbar draws. */
function compareHandlers(): ApiHandlers {
  const shared = TRENDS_ON.find(([m]) => m === CHART_DATA_PATH)?.[1]
  if (!shared) throw new Error('TRENDS_ON answers chart-data')
  return [[CHART_DATA_PATH, compareCountChartData((request: ApiRequest) => shared(request))], ...TRENDS_ON]
}

/** The Compare section mounted (it is lazy) and its chart drawn. */
async function compareDrawn(page: Page) {
  // Its placeholder (or the section) is in the DOM before `bringNear` scrolls to it.
  await expect(page.locator('[data-lazy-section="trends-compare"], [data-catalogue-section="trends-compare"]').first()).toBeAttached({ timeout: 15_000 })
  await bringNear(page, 'trends-compare')
  await expect(page.locator('[data-catalogue-section="trends-compare"] [data-chart-frame]')).toHaveAttribute('data-chart-state', /^(ready|truncated)$/, { timeout: 20_000 })
}

/** `/analytics/coverage` with one untagged group, so Fix tagging shows (`coverage-fix-tagging.spec.ts`). */
function withUntaggedGroup(): ApiHandlers {
  const fixture = COVERAGE_ON.find(([m]) => m === '/api/v1/analytics/coverage')?.[1]
  if (!fixture) throw new Error('COVERAGE_ON answers /analytics/coverage')
  return [
    [
      '/api/v1/analytics/coverage',
      (request: ApiRequest) => {
        const body = fixture(request) as { summary: Record<string, unknown>; suites: Record<string, unknown>[] }
        const untagged = { suite_name: 'Unknown suite', unique_tests: 5, passed: 30, failed: 9, skipped: 4, pass_rate: 76.92, last_run_at: null, last_run_id: null }
        return { ...body, suites: [...body.suites, untagged] }
      },
    ],
  ]
}

const seedRecents = async (page: Page) => {
  await page.addInitScript((recents) => {
    if (!sessionStorage.getItem('w27b-seeded')) {
      localStorage.setItem('tl.search.recent', JSON.stringify(recents))
      sessionStorage.setItem('w27b-seeded', '1')
    }
  }, RECENT_SEARCHES)
}

const projectCard = (p: Page) => p.locator('.card').filter({ has: p.getByText(LEGACY_PROJECT.name, { exact: true }) })
/** W11: the first rule's row of the attribution rules table. */
const attributionRow = (p: Page) => p.locator('[data-primary]').getByRole('row').filter({ hasText: 'Hotfix branches' })
const EDITABLE_NOTE = note('99999999-9999-4999-8999-0000000000f1', daysAgo(11), daysAgo(5), 'CI runners paused for the datacentre move')

const ROUTES: SweepRoute[] = [
  // ── fold-budget's routes that W27b does not convert: the load only ──
  { route: '/overview', path: '/overview', roles: EVERY, handlers: () => OVERVIEW_ON, ready: h1('Dashboard') },
  { route: '/runs', path: '/runs', roles: EVERY, handlers: () => RUNS_PAGE, ready: (p) => p.locator('[data-status-banner]') },
  { route: '/runs/:id', path: `/runs/${RUN_ID}`, roles: EVERY, handlers: () => RUN_PAGE, ready: (p) => p.locator('[data-primary] table') },
  { route: '/runs/:id/tests/:testId', path: TEST_CASE_PATH, roles: EVERY, handlers: () => TEST_CASE, ready: h1(TEST_CASE_NAME) },
  { route: '/failures', path: '/failures', roles: EVERY, handlers: () => FAILURES_ON, ready: (p) => landmark(p, 'Failure verdict') },
  { route: '/defects', path: '/defects', roles: EVERY, handlers: () => DEFECTS, ready: (p) => landmark(p, 'Defect KPIs') },
  { route: '/reports/summary', path: '/reports/summary', roles: EVERY, handlers: () => SUMMARY_REPORT_ON, ready: (p) => p.getByText('Total tests', { exact: true }) },
  { route: '/flaky', path: '/flaky', roles: EVERY, handlers: () => flakyHandlers(), ready: headerH1 },
  {
    route: '/release-gate',
    path: `/release-gate/${RUN_ID}`,
    roles: EVERY,
    handlers: () => withGateBlockers(releaseGateOn({ clusters: GATE_CLUSTERS })),
    ready: (p) => p.getByRole('meter', { name: 'Risk Score' }),
  },
  { route: '/releases', path: '/releases', roles: MANAGEMENT, handlers: () => RELEASES_PAGE, ready: h1('Releases') },
  // ── W11 REL-09: the attribution rules page, every role it lets in; its forms and its Delete are a QA lead's ──
  {
    route: '/releases/attribution-rules',
    path: '/releases/attribution-rules',
    roles: EVERY,
    handlers: () => attributionRulesWorld().handlers,
    ready: h1('Attribution rules'),
    actions: [
      only(LEAD_UP, form('New rule', (p) => p.getByRole('button', { name: 'New rule', exact: true }), 'New attribution rule')),
      only(LEAD_UP, form('a rule\'s Edit', (p) => attributionRow(p).getByRole('button', { name: /^Edit/ }), 'Edit attribution rule')),
      only(LEAD_UP, ask('a rule\'s Delete', (p) => attributionRow(p).getByRole('button', { name: /^Delete/ }))),
    ],
  },
  { route: '/agents', path: `/agents?run=${RUN_ID}`, roles: EVERY, handlers: () => agentsHandlers(), ready: (p) => p.getByRole('region', { name: 'AI report' }) },
  { route: '/settings', path: '/settings', roles: MANAGEMENT, handlers: () => LAYOUT, ready: (p) => p.locator('[data-settings-index]') },

  // ── PAN-05a the test library (G-A) ──
  {
    route: '/test-management',
    path: '/test-management',
    roles: EVERY,
    handlers: () => testLibraryWorld().handlers,
    ready: tmReady,
    actions: [
      form('Insights', (p) => p.getByRole('button', { name: /^Insights/ }), 'Insights', button('Close panel')),
      only(ENGINEER_UP, form('New test case', (p) => p.getByRole('button', { name: 'New test case from catalog toolbar' }), 'New Test Case')),
      only(ENGINEER_UP, form('AI Generate', (p) => p.getByRole('button', { name: 'AI Generate', exact: true }).first(), 'AI Generate Test Cases', button('Close'))),
      // W28 AMENDMENT: a row's Delete / Deprecate retires the case, which stays QA_LEAD or above.
      only(LEAD_UP, form(
        'a row\'s Delete',
        (p) => p.locator(`[data-primary] tr[data-case-row="${tmCaseId(1)}"]`).getByRole('button', { name: new RegExp(`^(Delete|Deprecate) ${escapeRe(tmCaseTitle(1))}$`) }),
        /^(Delete|Deprecate) test case$/,
      )),
    ],
  },
  {
    route: '/test-management?tab=plans',
    path: '/test-management?tab=plans',
    roles: EVERY,
    handlers: () => testLibraryWorld().handlers,
    ready: tmReady,
    actions: [
      form('New Plan', (p) => p.getByRole('button', { name: 'New Plan' }).first(), 'New Test Plan'),
      form('Link Suite', (p) => p.getByRole('button', { name: 'Link Suite', exact: true }).first(), 'Link Test Suite to Plan', cancel,
        async (p) => {
          await p.getByRole('heading', { name: 'Sprint 42 regression' }).click()
          await expect(p.getByText('No test cases in this plan yet. Link a suite or add test cases.')).toBeVisible()
        }),
    ],
  },
  {
    route: '/test-management?tab=suites',
    path: '/test-management?tab=suites',
    roles: EVERY,
    handlers: () => testLibraryWorld().handlers,
    ready: tmReady,
    actions: [only(LEAD_UP, form('Add test suite', (p) => p.getByRole('button', { name: 'Add test suite' }), 'Add test suite'))],
  },
  {
    route: '/test-management?tab=approvals',
    path: '/test-management?tab=approvals',
    roles: EVERY,
    handlers: () => testLibraryWorld().handlers,
    ready: tmReady,
    actions: [form('Request changes', (p) => p.locator('[data-tab-panel="approvals"] .card').first().getByRole('button', { name: 'Request changes', exact: true }).first(), 'Request changes')],
  },
  {
    route: '/test-management?tab=strategy',
    path: '/test-management?tab=strategy',
    roles: EVERY,
    handlers: () => testLibraryWorld().handlers,
    ready: tmReady,
    actions: [form('Generate Strategy', (p) => p.getByRole('button', { name: 'Generate Strategy' }).first(), 'Generate Test Strategy with AI')],
  },
  {
    route: '/test-management (evidence gaps)',
    path: '/test-management',
    roles: EVERY,
    handlers: () => lifecycleHostsWorld().handlers,
    ready: tmReady,
    prepare: async (p) => {
      await p.getByRole('region', { name: 'Execution evidence gaps' }).getByRole('button', { name: /^Unconfirmed automation retirements/ }).click()
    },
    actions: [only(LEAD_UP, form('Confirm retirement', (p) => p.getByRole('button', { name: 'Confirm retirement' }).first(), 'Confirm automation retirement'))],
  },
  {
    route: '/suites',
    path: '/suites',
    roles: EVERY,
    handlers: () => suitesInPageWorld().handlers,
    ready: h1('Test Suites'),
    actions: [
      only(ENGINEER_UP, form('New suite', (p) => p.getByRole('button', { name: 'New suite' }), 'New Test Suite')),
      only(LEAD_UP, ask('a suite\'s Delete', (p) => p.locator('[data-primary] tbody > tr:not([data-in-page-row])').filter({ has: p.locator('[data-suite-name]', { hasText: LEGACY_SUITE_NAME }) }).getByRole('button', { name: 'Delete' }))),
    ],
  },
  {
    route: '/suites/:id',
    path: `/suites/${SUITE_ID}`,
    roles: EVERY,
    handlers: () => suitesInPageWorld().handlers,
    ready: (p) => p.locator('[data-suite-tests]'),
    actions: [only(ENGINEER_UP, form('a test\'s Move', (p) => p.locator('[data-suite-tests] tbody > tr:not([data-in-page-row])').filter({ hasText: 'token refresh' }).getByRole('button', { name: 'Move', exact: true }), 'Move test case'))],
  },
  {
    route: '/canonical-test-cases/:id',
    path: `/canonical-test-cases/${CANONICAL_ID}`,
    roles: EVERY,
    handlers: () => lifecycleHostsWorld().handlers,
    ready: headerH1,
    actions: [only(ENGINEER_UP, form('Unlink managed case', (p) => p.getByRole('button', { name: 'Unlink managed case' }), 'Unlink managed case'))],
  },
  {
    route: '/my-failures?tab=approvals&source=cases',
    path: '/my-failures?tab=approvals&source=cases',
    roles: EVERY,
    handlers: () => lifecycleHostsWorld().handlers,
    ready: headerH1,
    actions: [form('a case\'s Approve', (p) => p.getByTestId('case-approval-row').first().getByRole('button', { name: 'Approve', exact: true }), 'Approve')],
  },
  {
    route: '/my-failures?tab=approvals&source=defects',
    path: '/my-failures?tab=approvals&source=defects',
    roles: EVERY,
    handlers: () => lifecycleHostsWorld().handlers,
    ready: headerH1,
    actions: [only(LEAD_UP, form('a defect\'s Reject', (p) => p.getByTestId('defect-reject').first(), 'Reject this defect?'))],
  },

  // ── PAN-05b analytics and reports (G-B) ──
  {
    route: '/trends',
    path: '/trends',
    roles: EVERY,
    handlers: () => [...trendsReportWorld().handlers, ...notesWorld([EDITABLE_NOTE]).handlers, ...TRENDS_ON],
    ready: trendsReady,
    actions: [
      only(ENGINEER_UP, form('Pin a note (cadence)', (p) => p.locator('[data-pin-note-open="cadence"]'), 'Pin a note', button('Close note dialog'))),
      only(ENGINEER_UP, form('Pin a note (run gap)', (p) => p.locator('[data-pin-note-open="gap"]'), 'Pin a note', button('Close note dialog'))),
      form('a note\'s Edit', (p) => p.locator(`[data-timeline-note-edit="${EDITABLE_NOTE.id}"]`), 'Edit note', cancel,
        (p) => p.locator('[data-catalogue-section="trends-pass-rate"] [data-timeline-notes-chip]').click()),
      only(LEAD_UP, form('Email report…', (p) => p.getByRole('menuitem', { name: 'Email report…' }), /Email report/, button('Close email dialog'),
        (p) => p.locator('[data-page-header]').getByRole('button', { name: 'More actions' }).click())),
    ],
  },
  {
    route: '/trends?tab=by-suite',
    path: '/trends?tab=by-suite',
    roles: EVERY,
    handlers: compareHandlers,
    ready: trendsReady,
    actions: [{
      name: 'Compare\'s Customise',
      before: compareDrawn,
      trigger: (p) => p.locator('[data-catalogue-section="trends-compare"] [data-chart-customise]'),
      container: (p) => p.locator('[data-chart-customise-panel]'),
      close: button('Close customise panel'),
    }],
  },
  {
    route: '/activity',
    path: '/activity',
    roles: MANAGEMENT,
    handlers: () => activityDeepLinkWorld().handlers,
    ready: h1('Activity'),
    actions: [form('an event', (p) => p.getByRole('feed').getByRole('button', { name: ACTIVITY_EVENTS[0].summary, exact: true }), ACTIVITY_EVENTS[0].summary, button('Close activity details'))],
  },
  {
    route: '/value-metrics',
    path: '/value-metrics',
    roles: EVERY,
    handlers: () => [...METHODOLOGY_HANDLERS, ...VALUE_METRICS],
    ready: (p) => p.getByRole('heading', { name: 'Hours saved per month' }),
    actions: [form('How is this calculated?', (p) => p.getByRole('button', { name: 'How is this calculated?' }), 'How hours saved is calculated', button('Close'))],
  },
  {
    route: '/explore',
    path: '/explore',
    roles: EVERY,
    handlers: () => EXPLORE,
    ready: h1('Explorer'),
    actions: [{
      name: 'Customise',
      trigger: (p) => p.getByRole('button', { name: 'Customise', exact: true }),
      container: (p) => p.locator('[data-chart-customise-panel]'),
      close: button('Close customise panel'),
    }],
  },
  {
    route: '/coverage',
    path: '/coverage',
    roles: EVERY,
    handlers: () => [...withUntaggedGroup(), ...fixTaggingWorld().handlers, ...COVERAGE_ON],
    ready: (p) => landmark(p, 'Run cadence'),
    actions: [only(ENGINEER_UP, form('Fix tagging', (p) => p.locator('[data-fix-tagging-open]'), 'Fix tagging', button('Close the fix tagging panel')))],
  },

  // ── PAN-05c / PAN-06a projects and access (G-C) ──
  {
    route: '/projects',
    path: '/projects',
    roles: MANAGEMENT,
    handlers: () => projectsInPageWorld().handlers,
    ready: h1('Projects'),
    actions: [
      form('New Project', (p) => p.getByRole('button', { name: 'New Project' }), 'New Project', button('Close new project dialog')),
      form('a card\'s Edit', (p) => projectCard(p).getByRole('button', { name: 'Edit' }), 'Edit Project', cancel, (p) => projectCard(p).hover()),
      ask('a card\'s Archive', (p) => projectCard(p).getByRole('button', { name: 'Archive' }), (p) => projectCard(p).hover()),
    ],
  },
  {
    route: '/ownership',
    path: '/ownership',
    roles: MANAGEMENT,
    handlers: () => ownershipInPageWorld().handlers,
    ready: h1('Ownership'),
    actions: [
      ask('a rule\'s Delete', (p) => p.locator('div.grid-cols-7:not([data-in-page-row])').filter({ has: p.getByText(OWNERSHIP_RULE_ROWS[1].match_pattern, { exact: true }) }).getByRole('button', { name: 'Delete', exact: true })),
      { name: 'Import CODEOWNERS', trigger: (p) => p.getByRole('button', { name: 'Import CODEOWNERS' }), container: (p) => openedByHeading(p, 'Import CODEOWNERS'), close: cancel },
    ],
  },
  {
    route: '/users',
    path: '/users',
    roles: MANAGEMENT,
    handlers: () => usersInPageWorld().handlers,
    ready: h1('User Management'),
    actions: [
      only(ADMIN_ONLY, form('Add User', (p) => p.getByRole('button', { name: 'Add User' }), 'Add User')),
      only(ADMIN_ONLY, form('Invite User', (p) => p.getByRole('button', { name: 'Invite User' }), 'Invite User')),
      only(ADMIN_ONLY, form('Edit user', (p) => p.getByRole('button', { name: 'Edit user' }).first(), 'Edit User')),
    ],
  },
  {
    route: '/users?tab=project-members',
    path: '/users?tab=project-members',
    roles: MANAGEMENT,
    handlers: () => usersInPageWorld().handlers,
    ready: h1('User Management'),
    prepare: async (p) => {
      await p.getByLabel('Select Project:').selectOption(PROJECT_ID)
    },
    actions: [
      only(ADMIN_ONLY, form('Add Member', (p) => p.getByRole('button', { name: 'Add Member' }), 'Add Member to Checkout')),
      only(ADMIN_ONLY, ask('Remove from project', (p) => p.getByRole('button', { name: 'Remove from project' }).first())),
      ask('Reset QA Lead password', (p) => p.getByRole('button', { name: 'Reset QA Lead password' })),
    ],
  },
  {
    route: '/settings/api-keys',
    path: '/settings/api-keys',
    roles: MANAGEMENT,
    handlers: () => keysInPageWorld().handlers,
    ready: h1('Streaming API keys'),
    actions: [ask('a key\'s Revoke', (p) => p.locator('#main-content table').first().locator('tbody > tr:not([data-in-page-row])').first().getByRole('button', { name: 'Revoke' }))],
  },
  {
    route: '/settings/my-api-keys',
    path: '/settings/my-api-keys',
    roles: EVERY,
    handlers: () => keysInPageWorld().handlers,
    ready: h1('My API keys'),
    actions: [
      only(ENGINEER_UP, form('Generate key', (p) => p.getByRole('button', { name: 'Generate key' }), 'Generate my API key')),
      only(ENGINEER_UP, ask('Revoke key', (p) => p.getByRole('button', { name: /^Revoke key / }).first())),
    ],
  },
  {
    route: '/settings/billing',
    path: '/settings/billing',
    roles: MANAGEMENT,
    handlers: () => billingHandlers().handlers,
    ready: (p) => p.locator('[data-billing-table]'),
    actions: [form('a project\'s quota', (p) => p.locator('tr[data-billing-project]').first().getByRole('button', { name: /^(Edit|View)$/ }), 'Edit LLM cost budget', button('Close'))],
  },
  {
    route: '/settings/feature-flags',
    path: '/settings/feature-flags',
    roles: MANAGEMENT,
    handlers: () => flagsDeleteWorld().handlers,
    ready: h1('Feature Flags'),
    actions: [
      only(ADMIN_ONLY, form('a flag\'s Edit', (p) => p.locator('tr[data-flag-key="defect_commander"]').getByRole('button', { name: 'Edit' }), 'Edit defect_commander')),
      only(ADMIN_ONLY, ask('a flag\'s Delete', (p) => p.getByRole('button', { name: 'Delete flag defect_commander' }))),
    ],
  },
  {
    route: '/settings/project-data',
    path: '/settings/project-data',
    roles: MANAGEMENT,
    handlers: () => projectDataWorld().handlers,
    ready: h1('Project Data'),
    readyFor: { QA_LEAD: adminNotice },
    actions: [only(ADMIN_ONLY, form('Delete test runs', (p) => p.getByRole('button', { name: 'Delete test runs' }), 'Delete test runs'))],
  },

  // ── PAN-05d / PAN-06b policy, search, chat and the settings pages (G-D) ──
  {
    route: '/policies',
    path: '/policies',
    roles: MANAGEMENT,
    handlers: () => policiesInPageWorld().handlers,
    ready: h1('Release Gate Policies'),
    actions: [only(ADMIN_ONLY, ask('Deactivate', (p) => p.getByRole('button', { name: 'Deactivate', exact: true }).first()))],
  },
  {
    route: '/policies/:id',
    path: `/policies/${DRAFT_POLICY.id}`,
    roles: MANAGEMENT,
    handlers: () => policiesInPageWorld().handlers,
    ready: h1(`Edit: ${DRAFT_POLICY.name}`),
    readyFor: { QA_LEAD: h1(`View: ${DRAFT_POLICY.name}`) },
    actions: [only(ADMIN_ONLY, ask('Publish', (p) => p.getByRole('button', { name: 'Publish', exact: true })))],
  },
  {
    route: '/search',
    path: '/search',
    roles: EVERY,
    handlers: () => SEARCH_PAGE,
    ready: (p) => p.getByTestId('searchable-data-summary'),
    init: seedRecents,
    actions: [ask('Clear history', (p) => p.getByRole('button', { name: 'Clear history' }))],
  },
  {
    route: '/chat',
    path: '/chat',
    roles: EVERY,
    handlers: () => chatDeleteWorld().handlers,
    flags: { ask_ai_chat: true },
    ready: (p) => p.getByRole('heading', { name: 'Ask about Checkout' }),
    actions: [ask('Delete conversation', (p) => p.getByTestId('chat-session').first().getByRole('button', { name: 'Delete conversation' }), (p) => p.getByTestId('chat-session').first().hover())],
  },
  {
    route: '/settings/retention',
    path: '/settings/retention',
    roles: MANAGEMENT,
    handlers: () => retentionInPageWorld().handlers,
    ready: h1('Retention & Purge'),
    readyFor: { QA_LEAD: adminNotice },
    actions: [only(ADMIN_ONLY, form('Purge now', (p) => p.getByRole('button', { name: 'Purge now' }), 'Purge old data now'))],
  },
  {
    route: '/settings/digests',
    path: '/settings/digests',
    roles: MANAGEMENT,
    handlers: () => digestsDeleteWorld().handlers,
    ready: h1('Digests & Saved Views'),
    actions: [ask('Unsubscribe', (p) => p.getByRole('button', { name: 'Unsubscribe' }).first())],
  },
  {
    route: '/settings/sso',
    path: '/settings/sso',
    roles: MANAGEMENT,
    handlers: () => ssoInPageWorld().handlers,
    ready: h1('SSO & Identity Management'),
    actions: [
      only(ADMIN_ONLY, ask('Activate', (p) => p.locator('div.space-y-2').filter({ has: p.getByRole('heading', { name: SSO_ENFORCED.display_name, exact: true, level: 3 }) }).last().getByRole('button', { name: 'Activate', exact: true }))),
      only(ADMIN_ONLY, ask('Delete', (p) => p.locator('div.space-y-2').filter({ has: p.getByRole('heading', { name: SSO_ENFORCED.display_name, exact: true, level: 3 }) }).last().getByRole('button', { name: 'Delete', exact: true }))),
    ],
  },
  { route: '/settings/github', path: '/settings/github', roles: MANAGEMENT, handlers: () => githubInPageWorld().handlers, ready: h1('GitHub Integration'), actions: [ask('Remove', (p) => p.getByRole('button', { name: 'Remove', exact: true }))] },
  { route: '/settings/gitlab', path: '/settings/gitlab', roles: MANAGEMENT, handlers: () => gitlabInPageWorld().handlers, ready: h1('GitLab Integration'), actions: [ask('Remove token', (p) => p.getByRole('button', { name: 'Remove token' }))] },
  {
    route: '/settings/my-notifications',
    path: '/settings/my-notifications',
    roles: EVERY,
    handlers: () => notificationsDeleteWorld().handlers,
    ready: h1('My notifications'),
    actions: [ask('Remove', (p) => p.locator('[data-channel-card="slack"]').getByRole('button', { name: 'Remove', exact: true }))],
  },
  { route: '/settings/webhooks', path: '/settings/webhooks', roles: MANAGEMENT, handlers: () => webhooksInPageWorld().handlers, ready: h1('Outbound Webhooks'), actions: [ask('Delete', (p) => p.getByRole('button', { name: `Delete ${WEBHOOK.name}` }))] },
  { route: '/settings/team-channels', path: '/settings/team-channels', roles: MANAGEMENT, handlers: () => teamChannelsWorld().handlers, ready: h1('Team channels'), actions: [ask('Remove', (p) => p.locator('[data-team-row="Identity"]').getByRole('button', { name: 'Remove' }))] },
]

/** react-hot-toast's toasts (its fixed container), never the page's own status lines. */
const toasts = (page: Page) => page.locator('div[style*="z-index: 9999"] [role="status"]')

async function usable(trigger: Locator): Promise<boolean> {
  if ((await trigger.count()) === 0) return false
  const first = trigger.first()
  return (await first.isVisible()) && (await first.isEnabled())
}

/** No overlay of any kind: no `aria-modal`, no `role="dialog"`, no native dialog, no `SidePanel` (its only users left are OUT). */
async function expectInPage(page: Page, what: string) {
  await expectNoOverlay(page)
  await expect(page.locator('[data-side-panel]'), `${what}: no side panel`).toHaveCount(0)
}

for (const r of ROUTES) {
  for (const role of r.roles) {
    test(`${r.route} as ${role}: loads with no overlay; every action the role sees opens in the page`, async ({ page }) => {
      watchNativeDialogs(page)
      if (r.init) await r.init(page)
      const ready = r.readyFor?.[role] ?? r.ready
      const { api, errors } = await openRollout(page, r.path, { handlers: r.handlers(), ready, user: USERS[role], flags: r.flags })
      await networkQuiet(page, api)
      await expectInPage(page, `${r.route} as ${role} at load`)
      await expect(toasts(page), `${r.route} as ${role}: a toast at load`).toHaveCount(0)
      await expectMainFits(page, `${r.route} as ${role}`)
      if (r.prepare) {
        await r.prepare(page)
        await networkQuiet(page, api)
      }
      const openedNames: string[] = []
      const gated: string[] = []
      for (const action of r.actions ?? []) {
        const sees = (action.who ?? r.roles).includes(role)
        const trigger = action.trigger(page)
        if (!sees) {
          // Today's gate: the role cannot use the control (a `before` may itself need one it lacks: a menu without it).
          if (action.before) await action.before(page).catch(() => undefined)
          await networkQuiet(page, api)
          expect(await usable(trigger), `${r.route} as ${role}: ${action.name} is not this role's (unchanged gate)`).toBe(false)
          if (action.before) await page.keyboard.press('Escape')
          gated.push(action.name)
          continue
        }
        if (action.before) await action.before(page)
        await expect(trigger.first(), `${r.route} as ${role}: ${action.name}'s control`).toBeVisible()
        await expect(trigger.first()).toBeEnabled()
        await trigger.first().click()
        const container = action.container(page)
        await expect(container, `${r.route} as ${role}: ${action.name} opened`).toHaveCount(1)
        await expect(container).toBeVisible()
        await expectInPage(page, `${r.route} as ${role}: ${action.name}`)
        await expectMainFits(page, `${r.route} as ${role}: ${action.name}`)
        await action.close(container, page)
        await expect(container, `${r.route} as ${role}: ${action.name} closed`).toHaveCount(0)
        openedNames.push(action.name)
      }
      console.log(`SWEEP ${r.route} ${role} ${JSON.stringify({ opened: openedNames, gated })}`)
      await networkQuiet(page, api)
      await expectInPage(page, `${r.route} as ${role} after the actions`)
      await expect(toasts(page), `${r.route} as ${role}: a toast after the actions`).toHaveCount(0)
      assertHermetic(api, errors)
    })
  }
}

// ── 2. The detail URLs ─────────────────────────────────────────────────────

const pathAndSearch = (page: Page) => {
  const url = new URL(page.url())
  return `${url.pathname}${url.search}`
}

test.describe('the detail URLs: a reload keeps them open once, Back closes those that push, a bad id is said so', () => {
  test('/test-management?case= (replace): a reload reopens the case, once', async ({ page }) => {
    watchNativeDialogs(page)
    const { api, errors } = await openRollout(page, '/test-management', { handlers: testLibraryWorld().handlers, ready: tmReady })
    await page.locator(`[data-primary] tr[data-case-row="${tmCaseId(0)}"]`).click()
    await expect(page.locator('[data-case-panel]')).toHaveCount(1)
    await expect.poll(() => pathAndSearch(page)).toBe(`/test-management?case=${tmCaseId(0)}`)
    await page.reload()
    await expect(page.locator('[data-case-panel]'), 'still open after a reload, once').toHaveCount(1)
    await expectInPage(page, '/test-management?case=')
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })

  test('/releases?open= (push): opened from a card, reloaded, closed by Back', async ({ page }) => {
    watchNativeDialogs(page)
    const { handlers } = releasesInPageWorld()
    const { api, errors } = await openRollout(page, '/releases', { handlers, ready: h1('Releases') })
    await page.getByRole('article', { name: /^2026\.11 / }).click()
    const pane = page.getByRole('complementary', { name: '2026.11', exact: true })
    await expect(pane).toHaveCount(1)
    await expect.poll(() => pathAndSearch(page)).toMatch(/^\/releases\?open=[^&]+$/)
    await page.reload()
    await expect(pane, 'still open, once').toHaveCount(1)
    await expectInPage(page, '/releases?open=')
    await page.goBack()
    await expect(pane).toHaveCount(0)
    await expect.poll(() => pathAndSearch(page)).toBe('/releases')
    await expectNoOverlay(page)
    assertHermetic(api, errors)
  })

  test('/runs/:id?tab=tests&test= (push): a failing test opened, reloaded, closed by Back', async ({ page }) => {
    watchNativeDialogs(page)
    const world = runAnalysisWorld()
    const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=tests`, { handlers: world.handlers, ready: (p) => p.locator('[data-primary] table') })
    const first = world.tests.find((t) => t.status === 'BROKEN')
    expect(first, 'the fixture has a failing test').toBeTruthy()
    await page.locator(`[data-test-row="${first?.id}"]`).click()
    const pane = page.getByRole('complementary', { name: String(first?.test_name), exact: true })
    await expect(pane).toHaveCount(1)
    await expect.poll(() => pathAndSearch(page)).toContain(`test=${first?.id}`)
    await page.reload()
    await expect(pane, 'still open, once').toHaveCount(1)
    await expectInPage(page, '/runs/:id?test=')
    await page.goBack()
    await expect(pane).toHaveCount(0)
    await expect.poll(() => pathAndSearch(page)).not.toContain('test=')
    await expectNoOverlay(page)
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })

  test('/flaky?test= (push): a flaky test\'s details opened, reloaded, closed by Back', async ({ page }) => {
    watchNativeDialogs(page)
    const { api, errors } = await openRollout(page, '/flaky', { handlers: flakyHandlers(), ready: headerH1 })
    const details = page.getByRole('complementary', { name: 'test_checkout_flow_0', exact: true })
    await page.getByRole('button', { name: 'test_checkout_flow_0', exact: true }).click()
    await expect(details).toHaveCount(1)
    await expect.poll(() => pathAndSearch(page)).toBe('/flaky?test=fp-00')
    await page.reload()
    await expect(details, 'still open, once').toHaveCount(1)
    await expectInPage(page, '/flaky?test=')
    await page.goBack()
    await expect(details).toHaveCount(0)
    await expect.poll(() => pathAndSearch(page)).toBe('/flaky')
    await expectNoOverlay(page)
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })

  test('/activity?event= (push): opened from a row, reloaded, closed by Back', async ({ page }) => {
    watchNativeDialogs(page)
    const { api, errors } = await openRollout(page, '/activity', { handlers: activityDeepLinkWorld().handlers, ready: h1('Activity') })
    const [FLAG] = ACTIVITY_EVENTS
    await page.getByRole('feed').getByRole('button', { name: FLAG.summary, exact: true }).click()
    const detail = opened(page, FLAG.summary)
    await expect(detail).toHaveCount(1)
    await expect.poll(() => pathAndSearch(page)).toBe(`/activity?event=${FLAG.id}`)
    await page.reload()
    await expect(detail, 'still open, once').toHaveCount(1)
    await expectInPage(page, '/activity?event=')
    await page.goBack()
    await expect(detail).toHaveCount(0)
    await expect.poll(() => pathAndSearch(page)).toBe('/activity')
    await expectNoOverlay(page)
    assertHermetic(api, errors)
  })

  test('/activity?event= with a purged id shows its not-found line in the page; a malformed one its own line, with no request', async ({ page }) => {
    watchNativeDialogs(page)
    const GONE = ACTIVITY_EVENTS.find((e) => e.id === 'ev-gone')
    expect(GONE, 'the fixture has a purged event').toBeTruthy()
    const { api, errors } = await openRollout(page, `/activity?event=${GONE?.id}`, { handlers: activityDeepLinkWorld().handlers, ready: h1('Activity') })
    const notFound = page.getByText('This event no longer exists. It may have been removed by the retention purge.')
    await expect(notFound).toBeVisible()
    // One detail, in the page: the purged event is in the feed, so its detail is the list item right after its row.
    const detail = opened(page, /^(Activity event|API key ci-uploader revoked)$/)
    await expect(detail, 'one detail').toHaveCount(1)
    await expect(detail.locator(notFound)).toHaveCount(1)
    await expect(page.locator('li[data-in-page-row]').filter({ has: notFound }), 'the list item after its row').toHaveCount(1)
    await expectInPage(page, '/activity not-found')
    await expectMainFits(page, '/activity not-found')
    await networkQuiet(page, api)
    expect(requestsTo(api, `/api/v1/projects/${PROJECT_ID}/activity/${GONE?.id}`).length, 'the purged id was asked').toBeGreaterThan(0)

    const from = api.seen.length + api.unhandled.length
    await page.goto('/activity?event=../../x')
    await expect(page.getByText('That is not a valid event link.')).toBeVisible()
    await expectNoOverlay(page)
    await networkQuiet(page, api)
    const lines = [...api.seen, ...api.unhandled].slice(from)
    // The page's own reads come again (a fresh load); no event read, and nothing carries the id.
    expect(lines.filter((l) => /\/projects\/[^/]+\/activity\/|\.\.%2F|\.\.\//.test(l)), 'no request for the malformed id').toEqual([])
    assertHermetic(api, errors)
  })

  test('a catalogue rows= (push): a heatmap cell\'s rows opened, reloaded, closed by Back', async ({ page }) => {
    watchNativeDialogs(page)
    const HEAT = { id: 'trends-heatmap', title: 'Suite pass rate by day' }
    const { api, errors } = await openRollout(page, '/trends?tab=heatmap', { handlers: TRENDS_ON, ready: trendsReady })
    await expectDrawn(sectionFrame(page, HEAT.id, HEAT.title), HEAT.id)
    await section(page, HEAT.id).locator('[data-chart-keyboard="heatmap"]').focus()
    await page.keyboard.press('ArrowRight')
    await page.keyboard.press('Enter')
    await expect(rowsPanels(page)).toHaveCount(1)
    await expect(page).toHaveURL(/[?&]rows=/)
    await page.reload()
    await expect(rowsPanels(page), 'still open, once').toHaveCount(1)
    await expect(section(page, HEAT.id).locator(rowsPanels(page)), 'still in its section').toHaveCount(1)
    await expectInPage(page, 'rows=')
    await page.goBack()
    await expect(rowsPanels(page)).toHaveCount(0)
    await expect(page).not.toHaveURL(/[?&]rows=/)
    await expectNoOverlay(page)
    assertHermetic(api, errors)
  })
})

// ── 3. The OUT popovers: opened once each, no overlay once closed ──────────

test.describe('the popovers that stay popovers (OR-03): no overlay once each is closed', () => {
  test('Views, the notifications bell and the Notes chip on /trends; the Compare MultiSelect on By suite', async ({ page }) => {
    watchNativeDialogs(page)
    const { api, errors } = await openRollout(page, '/trends', { handlers: [...notesWorld([EDITABLE_NOTE]).handlers, ...compareHandlers()], ready: trendsReady })
    await networkQuiet(page, api)

    await page.locator('[data-saved-views-trigger]').first().click()
    await expect(page.locator('[data-saved-views-panel]')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.locator('[data-saved-views-panel]')).toHaveCount(0)
    await expectNoOverlay(page)

    await page.getByRole('button', { name: 'Notifications', exact: true }).click()
    await expect(page.getByRole('dialog', { name: 'Notifications' })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expectNoOverlay(page)

    await page.locator('[data-catalogue-section="trends-pass-rate"] [data-timeline-notes-chip]').click()
    await expect(page.getByRole('dialog', { name: 'Timeline notes' })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expectNoOverlay(page)

    await page.goto('/trends?tab=by-suite')
    await expect(trendsReady(page)).toBeVisible()
    await compareDrawn(page)
    await page.locator('[data-compare-picker="suite"]').click()
    await expect(page.getByRole('dialog')).toHaveCount(1)
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expectNoOverlay(page)
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })

  test('the run picker on /runs/compare', async ({ page }) => {
    watchNativeDialogs(page)
    const { api, errors } = await openRollout(page, '/runs/compare', { handlers: RUN_COMPARE_PICKER, ready: h1('Run Compare') })
    await networkQuiet(page, api)
    await page.getByTestId('compare-left-picker').click()
    await expect(page.getByRole('dialog', { name: 'Left run (baseline)' })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expectNoOverlay(page)
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })
})
