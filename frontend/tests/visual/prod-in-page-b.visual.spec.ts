/**
 * W27b "in-page panels b" baselines (`docs/deferred-scope-2026-10-13/work/W27b-contract.md` §6.5, the 24 NEW regions),
 * both themes: every form, strip and detail that used to be an overlay, as it now sits IN the page.
 *
 *  - the test library: `tm-insights-inline` (Insights under the banner), `tm-new-case-inline` (New test case above the
 *    cases row), `tm-row-deprecate` (a case row's Deprecate form, lifecycle v2), `suites-row-delete-confirm` (a suite's
 *    Delete strip under its row), `suite-row-move` (a test's Move under its row), `inbox-case-approval-notes` (a
 *    case's Approve notes under its Inbox row);
 *  - analytics and reports: `trends-pin-note-inline` (Pin a note after the cadence card), `trends-email-inline` (Email
 *    report under the header), `activity-event-detail` (an event under its row), `value-methodology-inline` (the
 *    methodology after the hero), `catalogue-rows-inline` (a heatmap cell's rows in its section),
 *    `failures-group-inline` (a failure group after its frame), `explore-customise-inline` (Customise under the
 *    header), `coverage-fix-tagging-inline` (Fix tagging after the untagged callout), `saved-views-delete-confirm` (a
 *    view's Delete strip inside the Views popover);
 *  - projects and access: `projects-edit-inline` (Edit after its card), `users-row-edit` (Edit user under its row),
 *    `api-key-created-inline` (the generated FIXTURE key where the form was), `billing-row-quota` (the quota under its
 *    project row), `flags-row-edit` (a flag's Edit under its row), `project-data-typed-confirm` (the typed
 *    confirmation in the Danger zone);
 *  - settings: `retention-purge-inline` (Purge now in the Danger zone), `sso-confirm-inline` (Activate's strip in its
 *    card), `team-channels-row-confirm` (Remove's strip under the team's row).
 *
 * The one CHANGED region, `failures-on-drill-rows`, is shot by the UNEDITED `prod-failures-on.visual.spec.ts` (its rows
 * now open in the section, wider). Each region is the in-page container itself; the focus ring is not part of a
 * baseline (blurred). The 4000 px viewport (`PINNED_TALL`) keeps every region on screen with nothing scrolled, as
 * `visualRegion` requires; `/failures` uses 6000 px as `prod-failures-on.visual.spec.ts` does. Hermetic fixtures:
 * `production/fixtures-in-page-b.ts` and the worlds it composes. PNGs are rendered by CI only
 * (`visual-baselines.yml`, Linux); nobody regenerates them locally.
 */
import { expect, test, type Page } from '@playwright/test'
import { opened } from '../lib/inPage'
import { assertHermetic, cardByHeading, landmark, PINNED_TALL, THEMES, visualRegion, type ApiHandlers, type ApiRequest } from '../lib/production-pages'
import { expectDrawn, networkQuiet, openRollout, rowsPanels, section, sectionFrame } from '../lib/rollout'
import { COVERAGE_ON, FAILURES_ON, SUITE_ID, TRENDS_ON, VALUE_METRICS } from './production/fixtures'
import { BILLING_PROJECT, billingHandlers } from './production/fixtures-agents-billing-flags'
import {
  ACTIVITY_EVENTS,
  activityDeepLinkWorld,
  flagsDeleteWorld,
  keysInPageWorld,
  LEGACY_PROJECT,
  LEGACY_SUITE_NAME,
  lifecycleHostsWorld,
  METHODOLOGY_HANDLERS,
  MY_VIEW,
  projectDataWorld,
  projectsInPageWorld,
  retentionInPageWorld,
  savedViewsDeleteWorld,
  SSO_ENFORCED,
  ssoInPageWorld,
  suitesInPageWorld,
  teamChannelsWorld,
  testLibraryWorld,
  tmCaseId,
  tmCaseTitle,
  UM_USERS,
  usersInPageWorld,
} from './production/fixtures-in-page-b'
import { EXPLORE, fixTaggingWorld, notesWorld, trendsReportWorld } from './production/fixtures-trends-coverage'
import { ADMIN } from './production/fixtures-releases'

test.use(PINNED_TALL)

const h1 = (name: string) => (p: Page) => p.getByRole('heading', { level: 1, name, exact: true })
const tmReady = (p: Page) => p.getByRole('heading', { name: 'Test Case Management', level: 1 })
const trendsReady = (p: Page) => landmark(p, 'Trend metrics')
const blur = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())

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

for (const theme of THEMES) {
  // ── the test library ──
  test(`Insights under the banner and New test case above the cases row — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, '/test-management', { theme, handlers: testLibraryWorld().handlers, ready: tmReady })
    await expect(page.locator('[data-cases-layout]')).toBeVisible()
    await page.getByRole('button', { name: /^Insights/ }).click()
    const insights = opened(page, 'Insights')
    await expect(insights.locator('[data-insights] > *')).toHaveCount(5)
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'tm-insights-inline', theme, insights)
    await insights.getByRole('button', { name: 'Close panel' }).click()

    await page.getByRole('button', { name: 'New test case from catalog toolbar' }).click()
    const create = opened(page, 'New Test Case')
    await expect(create.getByLabel('Title *')).toBeVisible()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'tm-new-case-inline', theme, create)
    assertHermetic(api, errors)
  })

  test(`a case row's Deprecate form under its row (lifecycle v2) — ${theme}`, async ({ page }) => {
    const world = testLibraryWorld({ lifecycleV2: true })
    const { api, errors } = await openRollout(page, '/test-management', { theme, handlers: world.handlers, ready: tmReady, flags: { test_case_lifecycle_v2: true } })
    await expect(page.locator(`[data-primary] tr[data-case-row="${tmCaseId(1)}"]`)).toBeVisible()
    await page.getByRole('button', { name: `Deprecate ${tmCaseTitle(1)}` }).click()
    const form = opened(page, 'Deprecate test case')
    await expect(form.getByLabel('Reason')).toBeVisible()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'tm-row-deprecate', theme, form)
    assertHermetic(api, errors)
  })

  test(`a suite's Delete strip and a test's Move under their rows — ${theme}`, async ({ page }) => {
    const world = suitesInPageWorld()
    const { api, errors } = await openRollout(page, '/suites', { theme, handlers: world.handlers, ready: h1('Test Suites') })
    const row = page.locator('[data-primary] tbody > tr:not([data-in-page-row])').filter({ has: page.locator('[data-suite-name]', { hasText: LEGACY_SUITE_NAME }) })
    await row.getByRole('button', { name: 'Delete' }).click()
    const strip = page.locator('[data-inline-confirm]')
    await expect(strip).toContainText(`Delete suite "${LEGACY_SUITE_NAME}"?`)
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'suites-row-delete-confirm', theme, strip)
    await strip.getByRole('button', { name: 'Cancel' }).click()

    await page.goto(`/suites/${SUITE_ID}`)
    const testRow = page.locator('[data-suite-tests] tbody > tr:not([data-in-page-row])').filter({ hasText: 'token refresh' })
    await testRow.getByRole('button', { name: 'Move', exact: true }).click()
    const move = opened(page, 'Move test case')
    await expect(move.getByLabel('Target suite')).toBeVisible()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'suite-row-move', theme, move)
    assertHermetic(api, errors)
  })

  test(`a case's Approve notes under its Inbox row — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, '/my-failures?tab=approvals&source=cases', {
      theme,
      handlers: lifecycleHostsWorld().handlers,
      ready: (p) => p.locator('[data-page-header] h1'),
    })
    const row = page.getByTestId('case-approval-row').filter({ hasText: 'Checkout case c1' })
    await row.getByRole('button', { name: 'Approve', exact: true }).click()
    const notes = opened(page, 'Approve')
    await expect(notes).toBeVisible()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'inbox-case-approval-notes', theme, notes)
    assertHermetic(api, errors)
  })

  // ── analytics and reports ──
  test(`Pin a note after the cadence card — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, '/trends', { theme, handlers: [...notesWorld([]).handlers, ...TRENDS_ON], ready: trendsReady })
    await expect(cardByHeading(page, /^Run cadence — last/)).toBeVisible()
    await page.locator('[data-pin-note-open="cadence"]').click()
    const form = opened(page, 'Pin a note')
    await expect(form.locator('[data-pin-note="text"]')).toBeVisible()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'trends-pin-note-inline', theme, form)
    assertHermetic(api, errors)
  })

  test(`Email report under the Trends header — ${theme}`, async ({ page }) => {
    const report = trendsReportWorld()
    const { api, errors } = await openRollout(page, '/trends', { theme, handlers: [...report.handlers, ...TRENDS_ON], ready: trendsReady })
    await networkQuiet(page, api)
    await page.locator('[data-page-header]').getByRole('button', { name: 'More actions' }).click()
    await page.getByRole('menuitem', { name: 'Email report…' }).click()
    const email = opened(page, /Email report/)
    await expect(email).toBeVisible()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'trends-email-inline', theme, email)
    assertHermetic(api, errors)
  })

  test(`an Activity event's detail under its row — ${theme}`, async ({ page }) => {
    const [FLAG] = ACTIVITY_EVENTS
    const { api, errors } = await openRollout(page, '/activity?category=configuration', { theme, handlers: activityDeepLinkWorld().handlers, ready: h1('Activity') })
    await page.getByRole('feed').getByRole('button', { name: FLAG.summary, exact: true }).click()
    const detail = opened(page, FLAG.summary)
    await expect(detail).toBeVisible()
    await networkQuiet(page, api)
    await blur(page)
    await visualRegion(page, 'activity-event-detail', theme, detail)
    assertHermetic(api, errors)
  })

  test(`the value methodology after the hero — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, '/value-metrics', {
      theme,
      handlers: [...METHODOLOGY_HANDLERS, ...VALUE_METRICS],
      ready: (p) => p.getByRole('heading', { name: 'Hours saved per month' }),
    })
    await networkQuiet(page, api)
    await page.getByRole('button', { name: 'How is this calculated?' }).click()
    const methodology = opened(page, 'How hours saved is calculated')
    await expect(methodology).toBeVisible()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'value-methodology-inline', theme, methodology)
    assertHermetic(api, errors)
  })

  test(`a heatmap cell's rows in its section — ${theme}`, async ({ page }) => {
    const HEAT = { id: 'trends-heatmap', title: 'Suite pass rate by day' }
    const { api, errors } = await openRollout(page, '/trends?tab=heatmap', { theme, handlers: TRENDS_ON, ready: trendsReady })
    await expectDrawn(sectionFrame(page, HEAT.id, HEAT.title), HEAT.id)
    await section(page, HEAT.id).locator('[data-chart-keyboard="heatmap"]').focus()
    await page.keyboard.press('ArrowRight')
    await page.keyboard.press('Enter')
    const rows = rowsPanels(page)
    await expect(rows.locator('[data-rows-state]')).toHaveAttribute('data-rows-state', /^(ready|empty)$/, { timeout: 15_000 })
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'catalogue-rows-inline', theme, rows)
    assertHermetic(api, errors)
  })

  test(`Customise under the Explorer header — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, '/explore', { theme, handlers: EXPLORE, ready: h1('Explorer') })
    await expect(page.locator('[data-explore-panel]').first()).toBeVisible()
    await networkQuiet(page, api)
    await page.getByRole('button', { name: 'Customise', exact: true }).click()
    const customise = page.locator('[data-chart-customise-panel]')
    await expect(customise).toBeVisible()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'explore-customise-inline', theme, customise)
    assertHermetic(api, errors)
  })

  test(`Fix tagging after the untagged callout — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, '/coverage', {
      theme,
      handlers: [...withUntaggedGroup(), ...fixTaggingWorld().handlers, ...COVERAGE_ON],
      ready: (p) => landmark(p, 'Run cadence'),
    })
    await expect(page.getByRole('region', { name: 'Untagged executions' })).toBeVisible()
    await networkQuiet(page, api)
    await page.locator('[data-fix-tagging-open]').click()
    const panel = page.getByRole('complementary', { name: 'Fix tagging' })
    await expect(panel.locator('[data-untagged-test]').first()).toBeVisible()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'coverage-fix-tagging-inline', theme, panel)
    assertHermetic(api, errors)
  })

  test(`a view's Delete strip inside the Views popover — ${theme}`, async ({ page }) => {
    const world = savedViewsDeleteWorld()
    const { api, errors } = await openRollout(page, '/trends', { theme, handlers: [...world.handlers, ...TRENDS_ON], ready: trendsReady })
    await networkQuiet(page, api)
    await page.locator('[data-saved-views-trigger]').first().click()
    const popover = page.locator('[data-saved-views-panel]')
    await popover.locator(`li[data-saved-view="${MY_VIEW.id}"] [data-saved-view-delete]`).click()
    const strip = popover.locator('[data-inline-confirm]')
    await expect(strip).toContainText(`Delete the view "${MY_VIEW.name}"?`)
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'saved-views-delete-confirm', theme, strip)
    assertHermetic(api, errors)
  })

  // ── projects and access ──
  test(`a project's Edit after its card — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, '/projects', { theme, handlers: projectsInPageWorld().handlers, ready: h1('Projects') })
    const card = page.locator('.card').filter({ has: page.getByText(LEGACY_PROJECT.name, { exact: true }) })
    await card.hover()
    await card.getByRole('button', { name: 'Edit' }).click()
    const edit = opened(page, 'Edit Project')
    await expect(edit).toBeVisible()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'projects-edit-inline', theme, edit)
    assertHermetic(api, errors)
  })

  test(`Edit user under its row — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, '/users', { theme, handlers: usersInPageWorld().handlers, ready: h1('User Management'), user: ADMIN })
    const row = page.locator('#main-content table').first().locator('tbody > tr:not([data-in-page-row])').filter({ has: page.getByText(UM_USERS.priya.full_name ?? '', { exact: true }) })
    await row.getByRole('button', { name: 'Edit user' }).click()
    const edit = opened(page, 'Edit User')
    await expect(edit.getByText('Checkout', { exact: true })).toBeVisible()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'users-row-edit', theme, edit)
    assertHermetic(api, errors)
  })

  test(`the generated (fixture) key where the form was — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, '/settings/api-keys', { theme, handlers: keysInPageWorld().handlers, ready: h1('Streaming API keys'), user: ADMIN })
    await networkQuiet(page, api)
    await page.getByRole('button', { name: 'Generate streaming key' }).click()
    await page.getByPlaceholder('ci-runner-prod').fill('ci-runner-staging')
    await page.getByRole('button', { name: 'Generate', exact: true }).click()
    const created = opened(page, 'API key generated')
    await expect(created).toBeVisible()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'api-key-created-inline', theme, created)
    assertHermetic(api, errors)
  })

  test(`a project's quota under its billing row — ${theme}`, async ({ page }) => {
    const billing = billingHandlers()
    const { api, errors } = await openRollout(page, '/settings/billing', { theme, handlers: billing.handlers, ready: (p) => p.locator('[data-billing-table]'), user: ADMIN })
    await networkQuiet(page, api)
    await page.locator(`tr[data-billing-project="${BILLING_PROJECT.payments}"]`).getByRole('button', { name: 'Edit' }).click()
    const quota = opened(page, 'Edit LLM cost budget')
    await expect(quota.getByLabel('Included per month ($)')).toBeVisible()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'billing-row-quota', theme, quota)
    assertHermetic(api, errors)
  })

  test(`a flag's Edit under its row — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, '/settings/feature-flags', { theme, handlers: flagsDeleteWorld().handlers, ready: h1('Feature Flags'), user: ADMIN })
    await networkQuiet(page, api)
    await page.locator('tr[data-flag-key="defect_commander"]').getByRole('button', { name: 'Edit' }).click()
    const edit = opened(page, 'Edit defect_commander')
    await expect(edit).toBeVisible()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'flags-row-edit', theme, edit)
    assertHermetic(api, errors)
  })

  test(`the typed confirmation in the Danger zone — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, '/settings/project-data', { theme, handlers: projectDataWorld().handlers, ready: h1('Project Data'), user: ADMIN })
    await networkQuiet(page, api)
    await page.getByRole('button', { name: 'Delete test runs' }).click()
    const reset = opened(page, 'Delete test runs')
    await expect(reset).toBeVisible()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'project-data-typed-confirm', theme, reset)
    assertHermetic(api, errors)
  })

  // ── settings ──
  test(`Purge now in the Danger zone — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, '/settings/retention', { theme, handlers: retentionInPageWorld().handlers, ready: h1('Retention & Purge'), user: ADMIN })
    await networkQuiet(page, api)
    await page.getByRole('button', { name: 'Purge now' }).click()
    const purge = opened(page, 'Purge old data now')
    await expect(purge).toBeVisible()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'retention-purge-inline', theme, purge)
    assertHermetic(api, errors)
  })

  test(`Activate's question inside the SSO card — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, '/settings/sso', { theme, handlers: ssoInPageWorld().handlers, ready: h1('SSO & Identity Management'), user: ADMIN })
    await networkQuiet(page, api)
    const card = page.locator('div.space-y-2').filter({ has: page.getByRole('heading', { name: SSO_ENFORCED.display_name, exact: true, level: 3 }) }).last()
    await card.getByRole('button', { name: 'Activate', exact: true }).click()
    const strip = card.locator('[data-inline-confirm]')
    await expect(strip).toContainText('Activate SSO enforcement?')
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'sso-confirm-inline', theme, strip)
    assertHermetic(api, errors)
  })

  test(`Remove's question under a team's row — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, '/settings/team-channels', { theme, handlers: teamChannelsWorld().handlers, ready: h1('Team channels') })
    await networkQuiet(page, api)
    await page.getByRole('table', { name: 'Team channels' }).locator('[data-team-row="Identity"]').getByRole('button', { name: 'Remove' }).click()
    const strip = page.locator('[data-inline-confirm]')
    await expect(strip).toContainText('Remove the notification channel for Identity?')
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'team-channels-row-confirm', theme, strip)
    assertHermetic(api, errors)
  })
}

test.describe('the failures page (6000 px, as prod-failures-on)', () => {
  test.use({ viewport: { width: 1280, height: 6000 } })

  for (const theme of THEMES) {
    test(`a failure group after its frame — ${theme}`, async ({ page }) => {
      const GROUPS = { id: 'failures-groups', title: 'Failures grouped by error message' }
      const { api, errors } = await openRollout(page, '/failures', { theme, handlers: FAILURES_ON, ready: (p) => landmark(p, 'Failure verdict') })
      await expectDrawn(sectionFrame(page, GROUPS.id, GROUPS.title), GROUPS.id)
      await section(page, GROUPS.id).locator('[data-group-plot]').focus()
      await page.keyboard.press('ArrowRight')
      await page.keyboard.press('Enter')
      const group = page.getByRole('complementary', { name: 'Failure group #1' })
      await expect(group).toBeVisible()
      await blur(page)
      await networkQuiet(page, api)
      await visualRegion(page, 'failures-group-inline', theme, group)
      assertHermetic(api, errors)
    })
  }
})
