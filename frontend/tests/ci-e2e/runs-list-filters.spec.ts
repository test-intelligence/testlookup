/**
 * W07 FAIL-05 (`docs/deferred-scope-2026-10-13/work/W07-contract.md` §2.4), hermetic, on `/runs`: the toolbar's
 * search box, Branch select and AI-verdict select are SERVER filters, kept in the URL.
 *
 *  - typing `239` sends ONE list request with `q=239` after the pause (not one per key) and one row remains;
 *  - Branch `main` + AI verdict `No-Go` send `branch=main&recommendation=NO_GO`, the URL reads
 *    `?q=239&branch=main&verdict=NO_GO`, and the empty table says so with "Clear filters";
 *  - a reload restores the three controls and asks the same request;
 *  - `/intelligence?branch=main` (the retired hub) lands on `/runs?branch=main` with the branch selected;
 *  - the verdict filter alone lists exactly the run whose row shows that pill.
 *
 * The fixture's list honours `q`, `branch` and `recommendation` as `list_project_runs` does (`runListOf`,
 * `fixtures.ts`). Harness: `tests/lib/rollout.ts` (fail closed, pinned clock).
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID, RUN_ID, RUNS_PAGE, runs } from '../visual/production/fixtures'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ready = (p: Page) => p.locator('[data-status-banner]')
const rows = (page: Page) => page.locator('[data-primary] tr[id^="run-row-"]')
const search = (page: Page) => page.getByRole('searchbox', { name: 'Search runs' })
const branch = (page: Page) => page.getByRole('combobox', { name: 'Branch' })
const verdict = (page: Page) => page.getByRole('combobox', { name: 'AI verdict' })

/** The list requests' queries (the table's read: `size=500`), in order. */
const listQueries = (lines: string[]) =>
  lines.map((line) => new URLSearchParams(line.split('?')[1] ?? '')).filter((q) => q.get('size') === '500')

test('search, branch and verdict go to the server, live in the URL and survive a reload', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/runs', { handlers: RUNS_PAGE, ready })
  await expect(rows(page)).toHaveCount(runs().length)
  await networkQuiet(page, api)
  // The branch select lists the project's branches in the window.
  expect(requestsTo(api, '/api/v1/runs/branches')).toEqual([`GET /api/v1/runs/branches?project_id=${PROJECT_ID}&days=30`])
  await expect(branch(page).locator('option')).toHaveText(['All branches', 'main', 'release/2.4'])
  await expect(verdict(page).locator('option')).toHaveText(['Any verdict', 'Go', 'Conditional', 'No-Go', 'No verdict'])

  // Typing: one request after the pause, not one per key.
  let mark = api.seen.length
  await search(page).pressSequentially('239', { delay: 40 })
  await expect(rows(page)).toHaveCount(1)
  // Build 239 (the row reads its run sequence, `Run #59`).
  const b239 = runs().find((run) => String(run.build_number) === '239')
  await expect(rows(page).first()).toHaveAttribute('id', `run-row-${b239?.id}`)
  await networkQuiet(page, api)
  const typed = listQueries(requestsTo({ ...api, seen: api.seen.slice(mark) }, '/api/v1/runs'))
  expect(typed.map((q) => q.get('q')), 'one request, with the whole search').toEqual(['239'])
  await expect(page).toHaveURL(/[?&]q=239(&|$)/)

  // Branch + verdict: both on the wire, both in the URL; nothing matches build 239 as a No-Go.
  mark = api.seen.length
  await branch(page).selectOption({ label: 'main' })
  await verdict(page).selectOption({ label: 'No-Go' })
  await expect(page.getByText('No runs match these filters.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Clear filters' })).toBeVisible()
  await networkQuiet(page, api)
  const last = listQueries(requestsTo({ ...api, seen: api.seen.slice(mark) }, '/api/v1/runs')).pop()
  expect(last && Object.fromEntries(['q', 'branch', 'recommendation'].map((k) => [k, last.getAll(k)]))).toEqual({
    q: ['239'], branch: ['main'], recommendation: ['NO_GO'],
  })
  const url = new URL(page.url())
  expect(url.pathname).toBe('/runs')
  expect(Object.fromEntries(['q', 'branch', 'verdict'].map((k) => [k, url.searchParams.get(k)]))).toEqual({
    q: '239', branch: 'main', verdict: 'NO_GO',
  })

  // A reload restores the controls and asks the same filtered list.
  mark = api.seen.length
  await page.reload()
  await expect(ready(page)).toBeVisible()
  await expect(search(page)).toHaveValue('239')
  await expect(branch(page)).toHaveValue('main')
  await expect(verdict(page)).toHaveValue('NO_GO')
  await expect(page.getByText('No runs match these filters.')).toBeVisible()
  await networkQuiet(page, api)
  const reloaded = listQueries(requestsTo({ ...api, seen: api.seen.slice(mark) }, '/api/v1/runs'))
  expect(reloaded.length).toBeGreaterThan(0)
  for (const q of reloaded) {
    expect([q.get('q'), q.getAll('branch'), q.get('recommendation')]).toEqual(['239', ['main'], 'NO_GO'])
  }

  // Clear filters: every build again, and a clean URL.
  await page.getByRole('button', { name: 'Clear filters' }).click()
  await expect(rows(page)).toHaveCount(runs().length)
  await expect(search(page)).toHaveValue('')
  expect(['q', 'branch', 'verdict'].map((k) => new URL(page.url()).searchParams.get(k))).toEqual([null, null, null])
  assertHermetic(api, errors)
})

test('the verdict filter lists exactly the run whose row shows that pill', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/runs', { handlers: RUNS_PAGE, ready })
  await expect(rows(page)).toHaveCount(runs().length)
  await verdict(page).selectOption({ label: 'No-Go' })
  await expect(rows(page)).toHaveCount(1)
  await expect(page.locator(`#run-row-${RUN_ID} [data-ai-verdict="NO_GO"]`)).toBeVisible()
  await expect(page).toHaveURL(/[?&]verdict=NO_GO(&|$)/)
  await verdict(page).selectOption({ label: 'No verdict' })
  await expect(rows(page)).toHaveCount(runs().length - 3)
  await expect(rows(page).locator('[data-ai-verdict="GO"], [data-ai-verdict="NO_GO"], [data-ai-verdict="CONDITIONAL_GO"]')).toHaveCount(0)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('the retired /intelligence?branch=main lands on the filtered Runs list', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/intelligence?branch=main', { handlers: RUNS_PAGE, ready })
  await expect(page).toHaveURL(/\/runs\?branch=main$/)
  await expect(branch(page)).toHaveValue('main')
  await networkQuiet(page, api)
  const asked = listQueries(requestsTo(api, '/api/v1/runs'))
  expect(asked.length).toBeGreaterThan(0)
  expect(asked.every((q) => q.getAll('branch').join(',') === 'main'), 'every list read carries the branch').toBe(true)
  assertHermetic(api, errors)
})
