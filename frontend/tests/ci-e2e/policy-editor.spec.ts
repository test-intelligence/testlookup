/**
 * UX redesign P5 items 6 and 4, on /policies and /policies/:id, hermetic.
 *
 * - The editor keeps the decision rules open (metadata, thresholds, pass-rate
 *   bands, rules) and the tuning behind them (hard caps, failure-kind
 *   weighting, dimension weights) in collapsed disclosures.
 * - At >= 1280 px the simulator is a right-hand column that stays on screen
 *   (sticky) while the form scrolls; below 1280 px it follows the form.
 * - The list has the template's compact header, the list as primary content.
 *
 * `#main-content` scrollHeight of the editor at 1440 x 900 (FOLD line):
 * 1737 px before P5 (every section open, the simulator last), 1285 px after; 1346 px with W11's closed Versions row.
 *
 * W11 GATE-02 (`docs/deferred-scope-2026-10-13/work/W11-contract.md` §2.7, §6.5: a deliberate extension): the
 * simulator's typed Run ID became the `RunPicker` bound to the POLICY's project (its search asks
 * `GET /runs?project_id=<the policy's>…`; the picked run is what Simulate posts), so the handler set gains the runs
 * search, the picked run's label and the history route; and a published policy's **New version from this**,
 * **Versions** (closed on load) and **Compare** are driven end to end at the end of the file.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { MAIN, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import type { ApiHandlers } from '../lib/production-pages'
import { isoAgo, LAYOUT, PROJECT_ID, runs, RUNS, USER } from '../visual/production/fixtures'
import { POLICY_IDS, policyDocument, policyVersionsWorld } from '../visual/production/fixtures-release-planning'

const POLICY_ID = 'bbbbbbbb-0000-4000-8000-000000000001'
const RUN = '33333333-3333-4333-8333-333333333333'

const DOCUMENT = {
  schema_version: 1,
  thresholds: { go_threshold: 20, no_go_threshold: 55, pass_rate_minimum: 90, pass_rate_hard_floor_factor: 0.7 },
  dimension_weights: {
    user_impact: 0.25, env_sensitivity: 0.1, reproducibility: 0.15, regression_likely: 0.2,
    hist_recurrence: 0.1, blast_radius: 0.15, diagnosis_conf: 0.05,
  },
  rules: [
    { id: 'flaky-1', name: 'Flaky Recurrence Limit', type: 'flaky_recurrence', enabled: true, params: { max_flaky_tests: 10, action: 'BLOCK' } },
    { id: 'defects-1', name: 'Open Defect Limit', type: 'open_defect_limit', enabled: true, params: { max_open_defects: 5, action: 'WARN' } },
  ],
  pass_rate_bands: { orange_min: 90, yellow_min: 95, green_min: 99 },
  hard_caps: { max_p0_defects: 0, max_flaky_count: 10, max_new_failures_24h: 20 },
  kind_rules: { enabled: false },
}

const POLICY = {
  id: POLICY_ID,
  project_id: PROJECT_ID,
  version: 3,
  name: 'Checkout gate',
  description: 'Nightly release gate for Checkout',
  rules: DOCUMENT,
  is_active: false,
  is_draft: true,
  created_by: 'qa_lead',
  activated_by: null,
  activated_at: null,
  created_at: isoAgo(12),
  updated_at: isoAgo(1),
}

const ACTIVE = { ...POLICY, id: 'bbbbbbbb-0000-4000-8000-000000000002', name: 'System gate', project_id: null, version: 7, is_active: true, is_draft: false }

/** What the simulator was sent, per POST. */
const simulated: unknown[] = []

const POLICY_PAGE: ApiHandlers = [
  ['/api/v1/release-gate-policies', () => [POLICY, ACTIVE]],
  [
    '/api/v1/release-gate-policies/simulate',
    ({ route }) => {
      simulated.push(route.request().postDataJSON())
      return {
        original_recommendation: 'NO_GO',
        simulated_recommendation: 'CONDITIONAL_GO',
        original_composite: 61.2,
        simulated_composite: 48.4,
        rule_evaluations: [
          { rule_id: 'flaky-1', rule_name: 'Flaky Recurrence Limit', rule_type: 'flaky_recurrence', passed: true, action: 'BLOCK', message: '3 flaky ≤ 10', actual_value: 3, threshold_value: 10 },
        ],
        diff_summary: 'A NO_GO threshold of 65 turns this NO_GO into a CONDITIONAL_GO.',
      }
    },
    'POST',
  ],
  [`/api/v1/release-gate-policies/${POLICY_ID}`, () => POLICY],
  // W11 GATE-02: the Versions section's read (only once it is opened) and the simulator's run picker.
  [`/api/v1/release-gate-policies/history/${PROJECT_ID}`, () => [{ ...POLICY, created_by_name: 'qa_lead', activated_by_name: null }]],
  [/^\/api\/v1\/runs\/[0-9a-f-]{36}$/, ({ path }) => runs().find((r) => r.id === path.split('/').pop())],
  ...RUNS,
  ...LAYOUT,
]

/** The simulator's run picker: open it, pick `runId` from the policy's project's runs. */
async function pickRun(page: Page, runId: string) {
  const simulator = page.locator('[data-policy-simulator]')
  await simulator.locator('[data-run-picker] button[aria-haspopup]').click()
  await simulator.locator(`[data-run-option="${runId}"]`).click()
  await expect(simulator.locator('[data-run-picker] button[aria-haspopup]')).toContainText(/Run #\d+/)
}

const editorReady = (p: Page) => p.getByRole('heading', { name: 'Edit: Checkout gate', level: 1 })

// Policies are edited by an ADMIN (every write route is ADMIN); the layout under
// test is the editor's. A lower role gets the read-only view (PolicyEditorPage.roles.test.tsx).
const ADMIN = { ...USER, id: '00000000-0000-4000-8000-0000000000ad', username: 'admin', email: 'admin@example.test', full_name: 'Admin', role: 'ADMIN' }

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

/** Boxes relative to the top of the scroller (scrolled to `scrollTop`). */
async function boxes(page: Page, scrollTop: number | 'bottom') {
  return page.evaluate(
    ({ selector, scrollTop }) => {
      const main = document.querySelector(selector) as HTMLElement
      main.scrollTop = scrollTop === 'bottom' ? main.scrollHeight : scrollTop
      const top = main.getBoundingClientRect().top
      const box = (el: Element | null) => {
        if (!el) throw new Error('an element the geometry needs is missing')
        const r = el.getBoundingClientRect()
        return { top: Math.round(r.top - top), left: Math.round(r.left), right: Math.round(r.right), bottom: Math.round(r.bottom - top) }
      }
      return {
        scrollHeight: main.scrollHeight,
        clientHeight: main.clientHeight,
        scrolled: main.scrollTop,
        form: box(document.querySelector('[data-policy-form]')),
        simulator: box(document.querySelector('[data-policy-simulator]')),
        primary: box(document.querySelector('[data-primary]')),
      }
    },
    { selector: MAIN, scrollTop },
  )
}

test('the editor: the rules open, the tuning collapsed, the simulator a sticky right column', async ({ page }) => {
  simulated.length = 0
  const { api, errors } = await openRollout(page, `/policies/${POLICY_ID}`, { handlers: POLICY_PAGE, ready: editorReady, user: ADMIN })
  await networkQuiet(page, api)

  const top = await boxes(page, 0)
  console.log(`FOLD /policies/:id ${JSON.stringify(top)}`)
  // W11 GATE-02: the closed Versions disclosure adds ~61 px (1,285 → 1,346 on Windows); the bound keeps the ~115 px margin.
  expect(top.scrollHeight, 'the editor is shorter than before P5 (1737 px)').toBeLessThan(1460)
  // Beside the form, from the same top.
  expect(top.simulator.left).toBeGreaterThan(top.form.right)
  expect(Math.abs(top.simulator.top - top.form.top)).toBeLessThanOrEqual(1)
  expect(top.primary).toEqual(top.form)
  expect(top.form.top, 'the form starts within the fold budget').toBeLessThanOrEqual(300)

  for (const heading of ['Metadata', 'Thresholds', 'Pass-Rate Bands', 'Rules']) {
    await expect(page.getByRole('heading', { name: heading, level: 2 })).toBeVisible()
  }
  for (const title of ['Hard caps', 'Failure-kind weighting', 'Dimension weights']) {
    await expect(page.getByRole('button', { name: new RegExp(`^${title}`) })).toHaveAttribute('aria-expanded', 'false')
  }
  await expect(page.getByRole('button', { name: /^Dimension weights/ })).toContainText('Sum: 1.00')
  await expect(page.locator('[data-policy-rule]')).toHaveCount(2)

  // Scrolled to the bottom, the simulator is still on screen at the top.
  const bottom = await boxes(page, 'bottom')
  expect(bottom.scrolled).toBeGreaterThan(0)
  expect(bottom.simulator.top).toBeGreaterThanOrEqual(0)
  expect(bottom.simulator.top).toBeLessThanOrEqual(24)
  await expect(page.locator('[data-policy-simulator]')).toBeInViewport()

  // Edit a threshold far down the form, simulate from the column beside it (W11: the run is PICKED, from the
  // policy's project's runs).
  await page.getByLabel('NO_GO Threshold').fill('65')
  await pickRun(page, RUN)
  expect(requestsTo(api, '/api/v1/runs').filter((line) => line.includes('size=20')), 'the picker searches the policy\'s project').toEqual([
    `GET /api/v1/runs?project_id=${PROJECT_ID}&page=1&size=20&days=0`,
  ])
  await page.getByRole('button', { name: 'Simulate' }).click()
  const result = page.locator('[data-policy-simulation]')
  await expect(result).toContainText('A NO_GO threshold of 65 turns this NO_GO into a CONDITIONAL_GO.')
  await expect(result).toContainText('CONDITIONAL_GO')
  await expect(result).toBeInViewport()
  expect(simulated).toHaveLength(1)
  expect(simulated[0]).toMatchObject({ run_id: RUN, policy_document: { thresholds: { no_go_threshold: 65 } } })

  // "Back to policies" is in the header's ⋯.
  await page.locator('[data-page-header]').getByRole('button', { name: 'More actions' }).click()
  await page.getByRole('menuitem', { name: 'Back to policies' }).click()
  await expect(page).toHaveURL(/\/policies$/)
  await expect(page.getByRole('heading', { name: 'Release Gate Policies', level: 1 })).toBeVisible()
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('below 1280 px the simulator follows the form', async ({ page }) => {
  await page.setViewportSize({ width: 1279, height: 900 })
  const { api, errors } = await openRollout(page, `/policies/${POLICY_ID}`, { handlers: POLICY_PAGE, ready: editorReady, user: ADMIN })
  await networkQuiet(page, api)
  const top = await boxes(page, 0)
  expect(top.simulator.top).toBeGreaterThan(top.form.bottom)
  expect(top.simulator.left).toBe(top.form.left)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('the list: a compact header, New Policy, the policies as primary content', async ({ page }) => {
  const ready = (p: Page) => p.getByRole('heading', { name: 'Release Gate Policies', level: 1 })
  const { api, errors } = await openRollout(page, '/policies', { handlers: POLICY_PAGE, ready, user: ADMIN })
  await networkQuiet(page, api)
  await expect(page.locator('[data-page-header]')).toHaveAttribute('data-compact', 'true')
  const primary = page.locator('[data-primary]')
  await expect(primary.getByText('Checkout gate')).toBeVisible()
  await expect(primary.getByText('Project: Checkout · Created:', { exact: false })).toBeVisible()
  await expect(primary.getByText('System Default', { exact: false })).toBeVisible()
  // The list has no form and no simulator (`boxes` needs both): its primary
  // content's top, measured on its own.
  const primaryTop = await page.evaluate((selector) => {
    const main = document.querySelector(selector) as HTMLElement
    main.scrollTop = 0
    const primary = document.querySelector('[data-primary]') as HTMLElement
    return Math.round(primary.getBoundingClientRect().top - main.getBoundingClientRect().top)
  }, MAIN)
  expect(primaryTop).toBeLessThanOrEqual(300)
  await page.getByRole('button', { name: 'New Policy' }).click()
  await expect(page.getByRole('heading', { name: 'New Policy', level: 1 })).toBeVisible()
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

// ── W11 GATE-02: New version from this, Versions, Compare ─────────────────────────────────────────────────

const versionsToggle = (page: Page) => page.getByRole('button', { name: /^Versions/ })
const versionsSection = (page: Page): Locator => page.locator('section[data-disclosure]').filter({ has: versionsToggle(page) })
const versionRow = (scope: Locator, label: string): Locator => scope.locator('li, [data-policy-version]').filter({ hasText: label }).first()

test('a published policy: New version from this posts its document, the draft is edited, Versions and Compare name the change', async ({ page }) => {
  const world = policyVersionsWorld()
  const view = (p: Page) => p.getByRole('heading', { name: 'View: Payments gate', level: 1 })
  const { api, errors } = await openRollout(page, `/policies/${POLICY_IDS.v4}`, { handlers: world.handlers, ready: view, user: ADMIN })
  await networkQuiet(page, api)
  await expect(page.locator('[data-page-header]')).toContainText('Published (read-only)')
  await expect(versionsToggle(page), 'Versions is closed on load').toHaveAttribute('aria-expanded', 'false')
  expect(requestsTo(api, /\/release-gate-policies\/(history|system-history)/), 'no history read on load').toEqual([])

  await page.locator('[data-page-header]').getByRole('button', { name: 'New version from this', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Draft v5 created from v4.' })).toBeVisible()
  await expect(page).toHaveURL((url) => url.pathname === `/policies/${POLICY_IDS.v5}`)
  await expect(page.getByRole('heading', { name: 'Edit: Payments gate', level: 1 })).toBeVisible()
  const v4 = world.rows.find((r) => r.id === POLICY_IDS.v4)
  expect(world.writes).toEqual([
    {
      line: 'POST /api/v1/release-gate-policies',
      body: { project_id: PROJECT_ID, name: 'Payments gate', description: v4?.description, rules: policyDocument(20, 97) },
    },
  ])

  await page.getByLabel('GO Threshold', { exact: true }).fill('25')
  await page.getByRole('button', { name: 'Save Draft', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Policy saved' })).toBeVisible()
  expect(world.writes.map((w) => w.line)).toEqual(['POST /api/v1/release-gate-policies', `PATCH /api/v1/release-gate-policies/${POLICY_IDS.v5}`])
  expect((world.writes[1].body as { rules: { thresholds: { go_threshold: number } } }).rules.thresholds.go_threshold).toBe(25)

  await versionsToggle(page).click()
  const versions = versionsSection(page)
  await expect(versionRow(versions, 'v5 · Draft')).toBeVisible()
  await expect(versionRow(versions, 'v4 · Active')).toBeVisible()
  await expect(versionRow(versions, 'v3 · Published')).toBeVisible()
  await expect(versionRow(versions, 'v5 · Draft')).toContainText('This version')
  await expect(versionRow(versions, 'v4 · Active')).toContainText('created by priya')
  await networkQuiet(page, api)
  expect(requestsTo(api, `/api/v1/release-gate-policies/history/${PROJECT_ID}`), 'the history, once opened').toHaveLength(1)

  await versionRow(versions, 'v4 · Active').getByRole('checkbox').check()
  await versionRow(versions, 'v5 · Draft').getByRole('checkbox').check()
  await expect(versionRow(versions, 'v3 · Published').getByRole('checkbox'), 'at most two').toBeDisabled()
  await versions.getByRole('button', { name: 'Compare v4 → v5', exact: true }).click()
  await expect(versions.getByText('GO threshold 20 → 25', { exact: true })).toBeVisible()
  await expect(versions.getByText('No settings differ.', { exact: true })).toHaveCount(0)

  // Simulate the draft against a run picked from the policy's project.
  await pickRun(page, RUN)
  await page.getByRole('button', { name: 'Simulate' }).click()
  await expect(page.locator('[data-policy-simulation]')).toBeVisible()
  expect(world.simulated).toHaveLength(1)
  expect(world.simulated[0]).toMatchObject({ run_id: RUN, policy_document: { thresholds: { go_threshold: 25 } } })
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('as QA LEAD a published policy offers no New version from this, but Versions and Compare read', async ({ page }) => {
  const world = policyVersionsWorld()
  const view = (p: Page) => p.getByRole('heading', { name: 'View: Payments gate', level: 1 })
  const { api, errors } = await openRollout(page, `/policies/${POLICY_IDS.v4}`, { handlers: world.handlers, ready: view, user: USER })
  await networkQuiet(page, api)
  await expect(page.getByRole('button', { name: 'New version from this' })).toHaveCount(0)
  await versionsToggle(page).click()
  const versions = versionsSection(page)
  await versionRow(versions, 'v3 · Published').getByRole('checkbox').check()
  await versionRow(versions, 'v4 · Active').getByRole('checkbox').check()
  await versions.getByRole('button', { name: 'Compare v3 → v4', exact: true }).click()
  await expect(versions.getByText('Pass-rate band yellow_min 95 → 97', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
