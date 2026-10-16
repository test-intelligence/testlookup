/**
 * W07 FAIL-06 (`docs/deferred-scope-2026-10-13/work/W07-contract.md` §2.5), hermetic, on `/runs/compare`: the
 * Left / Right run controls are pickers that ask the SERVER (not the page's newest 100 runs):
 *
 *  - opening the Left picker asks `/runs?page=1&size=20&days=0` (every release, all time) and says
 *    `Showing 20 of 43 · all releases`; **Load older** asks `page=2`;
 *  - typing `228` asks `q=228&page=1&size=20&days=0` once the typing pauses and lists build 228 (`Run #48`);
 *  - **Paste run ID**: `abc` is refused locally (no request), an unknown UUID answered 404 reads
 *    `Run not found or not accessible.`, a fixture run's id is selected (checked with `GET /runs/{id}`);
 *  - **Compare** writes `?mode=manual&left=…&right=…`;
 *  - the latest-suite section states its runs as text (`13 runs in suite · latest Run #… · main`), no disabled
 *    select anywhere, and **Pick a different baseline** opens the Left picker narrowed to that suite.
 *
 * Fixtures: `RUN_COMPARE_PICKER` (`fixtures-runs-failures.ts`): `RUN_COMPARE_PAGE` over a 43-run history.
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock).
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID, RUN_ID, runs } from '../visual/production/fixtures'
import { longHistoryRuns, RUN_COMPARE_PICKER } from '../visual/production/fixtures-runs-failures'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ready = (p: Page) => p.getByRole('heading', { level: 1, name: 'Run Compare' })
const leftButton = (page: Page) => page.getByTestId('compare-left-picker')
const rightButton = (page: Page) => page.getByTestId('compare-right-picker')
const leftDialog = (page: Page) => page.getByRole('dialog', { name: 'Left run (baseline)' })
const rightDialog = (page: Page) => page.getByRole('dialog', { name: 'Right run (target)' })

/** The picker's search reads (`size=20`), their queries in order. */
const searches = (lines: string[]) =>
  lines.map((line) => new URLSearchParams(line.split('?')[1] ?? '')).filter((q) => q.get('size') === '20')

const B228 = runs().find((run) => String(run.build_number) === '228')
const UNKNOWN = '00000000-0000-4000-8000-00000000dead'

test('the pickers search and page on the server, check a pasted id, and Compare writes the URL', async ({ page }) => {
  expect(B228, 'the fixture has build 228').toBeTruthy()
  const total = longHistoryRuns().length
  const { api, errors } = await openRollout(page, '/runs/compare', { handlers: RUN_COMPARE_PICKER, ready })
  await networkQuiet(page, api)
  let mark = api.seen.length

  // Open: the newest 20 of every run, all releases, all time.
  await leftButton(page).click()
  await expect(leftButton(page)).toHaveAttribute('aria-expanded', 'true')
  await expect(leftDialog(page).locator('[data-run-option]')).toHaveCount(20)
  await expect(leftDialog(page).locator('[data-run-picker-count]')).toHaveText(`Showing 20 of ${total} · all releases`)
  await networkQuiet(page, api)
  const opened = searches(requestsTo({ ...api, seen: api.seen.slice(mark) }, '/api/v1/runs'))
  expect(opened.map((q) => [q.get('project_id'), q.get('page'), q.get('days'), q.get('q')])).toEqual([[PROJECT_ID, '1', '0', null]])

  // Load older: page 2, appended.
  mark = api.seen.length
  await leftDialog(page).getByRole('button', { name: 'Load older' }).click()
  await expect(leftDialog(page).locator('[data-run-option]')).toHaveCount(40)
  await expect(leftDialog(page).locator('[data-run-picker-count]')).toHaveText(`Showing 40 of ${total} · all releases`)
  await networkQuiet(page, api)
  expect(searches(requestsTo({ ...api, seen: api.seen.slice(mark) }, '/api/v1/runs')).map((q) => q.get('page'))).toEqual(['2'])

  // Search: one request with q, from page 1.
  mark = api.seen.length
  await leftDialog(page).getByRole('searchbox', { name: 'Search runs' }).pressSequentially('228', { delay: 40 })
  await expect(leftDialog(page).locator('[data-run-option]')).toHaveCount(1)
  await expect(leftDialog(page).locator(`[data-run-option="${B228?.id}"]`)).toContainText('Run #48')
  await networkQuiet(page, api)
  const typed = searches(requestsTo({ ...api, seen: api.seen.slice(mark) }, '/api/v1/runs'))
  expect(typed.map((q) => [q.get('q'), q.get('page'), q.get('size'), q.get('days')])).toEqual([['228', '1', '20', '0']])
  await leftDialog(page).locator(`[data-run-option="${B228?.id}"]`).click()
  await expect(leftDialog(page)).toHaveCount(0)
  await expect(leftButton(page)).toContainText('Run #48')

  // Paste: refused locally, refused by the server (one text), accepted.
  await rightButton(page).click()
  const paste = rightDialog(page).getByRole('textbox', { name: 'Paste run ID' })
  mark = api.seen.length
  await paste.fill('abc')
  await rightDialog(page).getByRole('button', { name: 'Use' }).click()
  await expect(rightDialog(page).locator('[data-run-picker-error]')).toHaveText('Not a run ID (expected a UUID).')
  expect(requestsTo({ ...api, seen: api.seen.slice(mark) }, /^\/api\/v1\/runs\/[0-9a-f-]{36}$/), 'no request for a non-UUID').toEqual([])
  await paste.fill(UNKNOWN)
  await rightDialog(page).getByRole('button', { name: 'Use' }).click()
  await expect(rightDialog(page).locator('[data-run-picker-error]')).toHaveText('Run not found or not accessible.')
  await paste.fill(RUN_ID)
  await rightDialog(page).getByRole('button', { name: 'Use' }).click()
  await expect(rightDialog(page)).toHaveCount(0)
  await expect(rightButton(page)).toContainText('Run #60')
  expect(requestsTo(api, `/api/v1/runs/${UNKNOWN}`)).toHaveLength(1)

  // Compare: the URL contract.
  await page.getByRole('button', { name: 'Compare', exact: true }).click()
  await expect(page).toHaveURL(/\/runs\/compare\?/)
  const url = new URL(page.url())
  expect([url.searchParams.get('mode'), url.searchParams.get('left'), url.searchParams.get('right')]).toEqual(['manual', B228?.id, RUN_ID])
  await networkQuiet(page, api)
  // No disabled run select is left in the page (the header's release filter is the shell's).
  await expect(page.locator('#main-content select[disabled]')).toHaveCount(0)
  assertHermetic(api, errors)
})

test('the latest-suite section states its runs as text and opens the baseline picker on that suite', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/runs/compare', { handlers: RUN_COMPARE_PICKER, ready })
  await expect(page.locator('[data-runs-in-suite-text]')).toHaveText('Select a suite to view runs')
  await page.locator('select').filter({ has: page.locator('option', { hasText: 'Select suite' }) }).selectOption('Auth')
  const auth = longHistoryRuns().filter((run) => run.primary_suite_name === 'Auth')
  await expect(page.locator('[data-runs-in-suite-text]')).toHaveText(`${auth.length} runs in suite · latest Run #${auth[0].run_seq} · main`)
  await expect(page.locator('#main-content select[disabled]')).toHaveCount(0)

  await page.getByRole('button', { name: 'Pick a different baseline' }).click()
  await expect(leftDialog(page)).toBeVisible()
  await expect(page).toHaveURL(/mode=manual/)
  // The picker lists the suite's runs only: the server's answer to `suite_name=Auth` (the summary above asked the
  // same first page, so the picker reads it from the cache).
  const options = leftDialog(page).locator('[data-run-option]')
  await expect(options).toHaveCount(Math.min(20, auth.length))
  const shown = await options.evaluateAll((els) => els.map((el) => el.getAttribute('data-run-option')))
  expect(shown.every((id) => auth.some((run) => run.id === id)), 'every option is an Auth run').toBe(true)
  await expect(leftDialog(page).locator('[data-run-picker-count]')).toHaveText(`Showing ${Math.min(20, auth.length)} of ${auth.length} · all releases`)
  await networkQuiet(page, api)
  const asked = searches(requestsTo(api, '/api/v1/runs'))
  expect(asked.some((q) => q.get('suite_name') === 'Auth' && q.get('days') === '0'), 'the suite search went to the server').toBe(true)
  assertHermetic(api, errors)
})
