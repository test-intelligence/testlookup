/**
 * D-0023 (GPT-6): a run the user cannot open (another project's, or one that does not exist: the server answers both
 * with the same 404) showed "Could not load Run Intelligence" under enabled **Analyze failures**, **Trigger pipeline**,
 * **Deep investigation** and **Refresh AI analysis**. The server refused each of them; the page should not offer them.
 * Now a settled 404 is **Run not found** with no tabs and no actions (owner decision Q6), and while the run is still
 * loading the run's actions are disabled.
 *
 * Also E2E-0022 step 4 (D-0020's non-member control): such a run shows no title, report or pipeline.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock).
 */
import { expect, test, type Page } from '@playwright/test'
import { respond, type ApiHandlers } from '../lib/production-pages'
import { assertHermetic, networkQuiet, observed, openRollout } from '../lib/rollout'
import { RUN_ID } from '../visual/production/fixtures'
import { VIEWER } from '../visual/production/fixtures-releases'
import { RUN, RUN_PAGE } from '../visual/production/fixtures-run'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const FOREIGN_RUN_ID = '34343434-3434-4343-8343-343434343434'
const NOT_FOUND = { detail: 'Test run not found' }
/** Every read of the foreign run, as the server answers a non-member: `require_run_access`'s 404. */
const FOREIGN_READS = new RegExp(`^/api/v1/.*${FOREIGN_RUN_ID}`)
const RUN_ACTIONS = ['Trigger pipeline', 'Deep investigation', 'Refresh AI analysis']
/** Auto mode with deep investigation on (`RUN_PAGE` answers Rules, where Analyze failures gives way to a note). */
const AUTO_MODE: ApiHandlers = [
  ['/api/v1/settings/ai/mode', () => ({ analysis_mode: 'auto', ai_offline_mode: false, deep_investigation_enabled: true })],
]
/** The run page's world in Auto mode. */
const WORLD: ApiHandlers = [...AUTO_MODE, ...RUN_PAGE]

const deepRegion = (p: Page) => p.getByRole('region', { name: 'Deep investigation' })
const analyze = (p: Page) => deepRegion(p).getByRole('button', { name: /Analyze failures/ })
const runNotFound = (p: Page) => p.getByText('Run not found')

/**
 * Nothing but reads went out: no run action, and no other write. A loaded run's page also completes the onboarding
 * step for opening a run (`POST /onboarding/<project>/complete`): the page's own record, not an action.
 */
function expectNoWrites(api: Parameters<typeof observed>[0]) {
  const writes = observed(api).filter((line) => !line.startsWith('GET ') && !line.startsWith('POST /api/v1/onboarding/'))
  expect(writes).toEqual([])
}

test('D-0023: a run that answers 404 opens as Run not found, with no tabs and no actions', async ({ page }) => {
  const handlers: ApiHandlers = [[FOREIGN_READS, () => respond(404, NOT_FOUND)], ...WORLD]
  const { api, errors } = await openRollout(page, `/runs/${FOREIGN_RUN_ID}?tab=analysis`, { handlers, ready: runNotFound })
  await expect(page.getByText('It may have been deleted, or it belongs to a project you are not a member of.')).toBeVisible()
  await expect(page.getByRole('heading', { level: 1, name: 'Run', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { level: 1, name: /^Run #/ })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'More actions' })).toHaveCount(0)
  await expect(page.getByRole('tablist', { name: 'Run sections' })).toHaveCount(0)
  await expect(deepRegion(page)).toHaveCount(0)
  await expect(page.getByText('Could not load Run Intelligence')).toHaveCount(0)
  await expect(page.getByRole('region', { name: 'AI report' })).toHaveCount(0)
  await networkQuiet(page, api)
  expectNoWrites(api)
  assertHermetic(api, errors)
})

test('D-0023: while the run is loading, its actions are disabled; the 404 then shows Run not found', async ({ page }) => {
  let release = () => {}
  const answered = new Promise<void>((resolve) => { release = resolve })
  const handlers: ApiHandlers = [
    [`/api/v1/runs/${FOREIGN_RUN_ID}`, async () => { await answered; return respond(404, NOT_FOUND) }],
    [FOREIGN_READS, () => respond(404, NOT_FOUND)],
    ...WORLD,
  ]
  const { api, errors } = await openRollout(page, `/runs/${FOREIGN_RUN_ID}?tab=analysis`, {
    handlers, ready: (p) => p.getByRole('heading', { level: 1, name: 'Run', exact: true }),
  })
  await page.getByRole('button', { name: 'More actions' }).click()
  for (const name of RUN_ACTIONS) await expect(page.getByRole('menuitem', { name })).toBeDisabled()
  await page.keyboard.press('Escape')
  await expect(analyze(page)).toBeDisabled()
  await expect(analyze(page)).toHaveAttribute('title', 'Waiting for the run to load')
  await analyze(page).click({ force: true })

  release()
  await expect(runNotFound(page)).toBeVisible()
  await expect(page.getByRole('button', { name: 'More actions' })).toHaveCount(0)
  await networkQuiet(page, api)
  expectNoWrites(api)
  assertHermetic(api, errors)
})

test("a member's run with failures offers Analyze failures and the run's actions", async ({ page }) => {
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=analysis`, { handlers: WORLD, ready: deepRegion })
  await expect(page.getByRole('heading', { level: 1, name: `Run #${RUN.build_number}` })).toBeVisible()
  await expect(analyze(page)).toBeEnabled()
  await page.getByRole('button', { name: 'More actions' }).click()
  for (const name of RUN_ACTIONS) await expect(page.getByRole('menuitem', { name })).toBeEnabled()
  await page.keyboard.press('Escape')
  await networkQuiet(page, api)
  expectNoWrites(api)
  assertHermetic(api, errors)
})

test('a run with no failures says Analyze failures has nothing to analyze', async ({ page }) => {
  const handlers: ApiHandlers = [[`/api/v1/runs/${RUN_ID}`, () => ({ ...RUN, failed_tests: 0, broken_tests: 0 })], ...WORLD]
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=analysis`, { handlers, ready: deepRegion })
  await expect(analyze(page)).toBeDisabled()
  await expect(analyze(page)).toHaveAttribute('title', 'This run has no failures to analyze')
  await networkQuiet(page, api)
  expectNoWrites(api)
  assertHermetic(api, errors)
})

test("a Viewer's denial is the role's, not the run's", async ({ page }) => {
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=analysis`, { handlers: WORLD, user: VIEWER, ready: deepRegion })
  await expect(analyze(page)).toBeDisabled()
  await expect(analyze(page)).toHaveAttribute('title', 'QA Engineer role required')
  await page.getByRole('button', { name: 'More actions' }).click()
  await expect(page.getByRole('menuitem', { name: 'Trigger pipeline' })).toHaveCount(0)
  await expect(page.getByRole('menuitem', { name: 'Deep investigation' })).toHaveCount(0)
  await page.keyboard.press('Escape')
  await networkQuiet(page, api)
  expectNoWrites(api)
  assertHermetic(api, errors)
})
