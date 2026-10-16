/**
 * W08 TRND-07 (contract §2.5), hermetic, on `/coverage`:
 *
 *   - the Untagged-executions callout's **Fix tagging** (QA engineer and
 *     above) opens a side panel listing the tests behind the callout (asked
 *     only when it opens: `rollout-coverage-map.spec.ts`'s exact inventory is
 *     the other half); a test with no catalogue row is listed, not selectable;
 *     three ticked tests + suite **Checkout** + **Move** → ONE
 *     `POST /canonical-test-cases/bulk-link` naming exactly those three ids and
 *     the suite, `Moved 3 tests to Checkout`, and the list asked again. The
 *     panel always says that moving changes the catalogue, never an
 *     execution's label, and links the reporter docs;
 *   - only a QA lead is offered "Also make Checkout the default suite…"; a
 *     viewer has no Fix tagging at all;
 *   - the **History** tab (`?tab=history`) draws "Coverage history" from the
 *     snapshots, with one read.
 *
 * The default `/analytics/coverage` fixture has no untagged group (its regions
 * must not move), so this spec answers it with one more row, `Unknown suite`.
 * Harness: `tests/lib/rollout.ts` (fail closed). Fixtures: `fixTaggingWorld()`
 * (`fixtures-trends-coverage.ts`), `UNTAGGED_TESTS` (`fixtures.ts`).
 */
import { expect, test, type Page } from '@playwright/test'
import { landmark, type ApiHandlers, type ApiRequest } from '../lib/production-pages'
import { assertHermetic, expectNoErrorFrame, networkQuiet, openRollout, queryOf, requestsTo } from '../lib/rollout'
import { COVERAGE_HISTORY_PATH, COVERAGE_ON, PROJECT_ID, UNTAGGED_TESTS, UNTAGGED_TESTS_PATH } from '../visual/production/fixtures'
import { CHECKOUT_SUITE_ID, fixTaggingWorld, QA_ENGINEER, VIEWER } from '../visual/production/fixtures-trends-coverage'

test.use({ viewport: { width: 1440, height: 1000 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ready = (p: Page) => landmark(p, 'Run cadence')
const COVERAGE_PATH = '/api/v1/analytics/coverage'
const BULK_LINK = '/api/v1/canonical-test-cases/bulk-link'

/** `/analytics/coverage` with one untagged group, so the callout (and its Fix tagging) shows. */
function withUntaggedGroup(): ApiHandlers {
  const fixture = COVERAGE_ON.find(([m]) => m === COVERAGE_PATH)?.[1]
  if (!fixture) throw new Error('COVERAGE_ON answers /analytics/coverage')
  return [
    [
      COVERAGE_PATH,
      (request: ApiRequest) => {
        const body = fixture(request) as { summary: Record<string, unknown>; suites: Record<string, unknown>[] }
        const untagged = { suite_name: 'Unknown suite', unique_tests: 5, passed: 30, failed: 9, skipped: 4, pass_rate: 76.92, last_run_at: null, last_run_id: null }
        return { ...body, suites: [...body.suites, untagged] }
      },
    ],
  ]
}

const panel = (page: Page) => page.getByRole('complementary', { name: 'Fix tagging' })
const openButton = (page: Page) => page.locator('[data-fix-tagging-open]')
const item = (page: Page, i: number) => panel(page).locator(`[data-untagged-test="${UNTAGGED_TESTS[i].test_fingerprint}"]`)
const checkbox = (page: Page, i: number) => panel(page).getByRole('checkbox', { name: `Select ${UNTAGGED_TESTS[i].test_name}` })

test('a QA lead moves three untagged tests to Checkout: one bulk-link POST with their ids, the outcome, the list asked again', async ({ page }) => {
  const world = fixTaggingWorld()
  const { api, errors } = await openRollout(page, '/coverage', { handlers: [...withUntaggedGroup(), ...world.handlers, ...COVERAGE_ON], ready })
  await networkQuiet(page, api)
  expect(requestsTo(api, UNTAGGED_TESTS_PATH), 'the untagged tests on load').toEqual([])
  expect(requestsTo(api, COVERAGE_HISTORY_PATH), 'the history on load').toEqual([])

  await openButton(page).click()
  await expect(panel(page)).toBeVisible()
  await expect(panel(page).locator('[data-fix-tagging-copy]')).toContainText(
    'Its executions — past and future — keep the suite label your test reporter sends',
  )
  await expect(panel(page).locator('[data-fix-tagging-copy]').getByRole('link')).toHaveAttribute('href', '/docs/ingestion#key-behaviours')
  await expect(panel(page).locator('[data-untagged-test]')).toHaveCount(UNTAGGED_TESTS.length)
  // The test with no catalogue row is listed, not selectable, and says why.
  const notInCatalogue = UNTAGGED_TESTS.findIndex((t) => t.canonical_id === null)
  await expect(checkbox(page, notInCatalogue)).toBeDisabled()
  await expect(item(page, notInCatalogue)).toHaveAttribute('title', 'Not in the catalogue yet: it is added when its run is finalised')
  const firstReads = requestsTo(api, UNTAGGED_TESTS_PATH).length
  expect(firstReads, 'asked once the panel opened').toBeGreaterThan(0)
  expect(queryOf(api, UNTAGGED_TESTS_PATH)[0].get('project_id')).toBe(PROJECT_ID)

  for (const i of [0, 1, 2]) await checkbox(page, i).check()
  await panel(page).locator('[data-fix-tagging-target]').selectOption({ label: 'Checkout' })
  await expect(panel(page).getByText('Also make Checkout the default suite for new unlabelled tests')).toBeVisible()
  const move = panel(page).locator('[data-fix-tagging-move]')
  await expect(move).toHaveText('Move 3 tests to Checkout')
  await move.click()
  await expect(panel(page).locator('[data-fix-tagging-outcome]')).toHaveText('Moved 3 tests to Checkout')
  expect(world.writes).toEqual([
    {
      line: `POST ${BULK_LINK}`,
      body: { target_test_suite_id: CHECKOUT_SUITE_ID, canonical_ids: [0, 1, 2].map((i) => UNTAGGED_TESTS[i].canonical_id) },
    },
  ])
  await expect.poll(() => requestsTo(api, UNTAGGED_TESTS_PATH).length, { message: 'the list asked again after the move' }).toBeGreaterThan(firstReads)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('a QA engineer moves but is not offered the default suite', async ({ page }) => {
  const world = fixTaggingWorld()
  const { api, errors } = await openRollout(page, '/coverage', {
    handlers: [...withUntaggedGroup(), ...world.handlers, ...COVERAGE_ON],
    ready,
    user: QA_ENGINEER,
  })
  await openButton(page).click()
  await expect(panel(page).locator('[data-untagged-test]')).toHaveCount(UNTAGGED_TESTS.length)
  await panel(page).locator('[data-fix-tagging-target]').selectOption({ label: 'Checkout' })
  await expect(panel(page).locator('[data-fix-tagging-move]')).toHaveText('Move 0 tests to Checkout')
  await expect(panel(page).getByText(/the default suite for new unlabelled tests/)).toHaveCount(0)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('a viewer sees the untagged callout without Fix tagging, and nothing is asked', async ({ page }) => {
  const world = fixTaggingWorld()
  const { api, errors } = await openRollout(page, '/coverage', {
    handlers: [...withUntaggedGroup(), ...world.handlers, ...COVERAGE_ON],
    ready,
    user: VIEWER,
  })
  await expect(page.getByText(/untagged execution/i).first()).toBeVisible()
  await expect(openButton(page)).toHaveCount(0)
  await networkQuiet(page, api)
  expect(requestsTo(api, UNTAGGED_TESTS_PATH)).toEqual([])
  assertHermetic(api, errors)
})

test('the History tab draws Coverage history from the snapshots, with one read', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/coverage', { handlers: COVERAGE_ON, ready })
  await page.getByRole('tablist', { name: 'Coverage views' }).getByRole('tab', { name: 'History', exact: true }).click()
  await expect(page).toHaveURL(/[?&]tab=history(&|$)/)
  const history = page.getByRole('region', { name: 'Coverage history' })
  await expect(history).toHaveAttribute('data-coverage-history', 'drawn', { timeout: 20_000 })
  await expect(history.locator('[data-chart-frame]')).toHaveAttribute('data-chart-state', /^(ready|truncated)$/)
  await expect(history.locator('.recharts-wrapper svg.recharts-surface path').first()).toBeVisible()
  await expectNoErrorFrame(page)
  await networkQuiet(page, api)
  const reads = queryOf(api, COVERAGE_HISTORY_PATH)
  expect(reads, 'one history read').toHaveLength(1)
  expect(reads[0].get('project_id')).toBe(PROJECT_ID)
  assertHermetic(api, errors)
})
