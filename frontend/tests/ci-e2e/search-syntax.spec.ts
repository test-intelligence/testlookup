/**
 * W12 SRCH-04 structured query syntax (`docs/deferred-scope-2026-10-13/work/W12-contract.md` §2.3, §4.1, D-10),
 * hermetic, on `/search`:
 *
 *  - after a search answers, ONE chips row sits inside the command bar between the input and the Mode / Scope row:
 *    the eyebrow `Filters`, then one chip per APPLIED filter in source order (`status: failed`, `suite: API
 *    Regression`, `since: 7d`, React text); the raw query goes to the API as typed;
 *  - a chip's × removes exactly `q.slice(start, end)` (whitespace collapsed) and re-runs the search;
 *  - every `parsed.notes` line renders verbatim: an unsupported filter (`branch: is not supported for Defects, so no
 *    defects were searched.`; on All, one condensed sentence per key that keeps two or more kinds out:
 *    `status: applies only to Tests and Runs, so suites, defects, flaky tests and releases were not searched.`), an unknown key (`unknown filter foo: searched as text`), an invalid value
 *    (`flaky is not a status: use type:flaky`), an unresolved release (`No release named "9.9" in your projects.`);
 *  - a plain query has NO chips row (the command bar keeps its height);
 *  - **Syntax** (a closed `Disclosure`, in the page) lists the eight keys with an example and the kinds each applies
 *    to; it fits the command bar at 1280 and 1440.
 *
 * Harness: `tests/lib/production-pages.ts` (fail closed, pinned clock). Fixtures: `fixtures-search.ts` (the
 * `parsed` blocks were produced by G-B's real parser).
 */
import { expect, test, type Page } from '@playwright/test'
import { expectNoOverlay, watchNativeDialogs } from '../lib/inPage'
import { boxOf, inPageLine, ownOverflow } from '../lib/inPageGeometry'
import { assertHermetic, landmark, openProductionPage } from '../lib/production-pages'
import { expectNoHorizontalOverflow, networkQuiet, queryOf } from '../lib/rollout'
import { NOW, PROJECT_ID } from '../visual/production/fixtures'
import {
  GLOBAL_API,
  searchWorld,
  SYNTAX_PARSED,
  SYNTAX_QUERY,
  SYNTAX_QUERY_WITHOUT_SINCE,
  syntaxGlobal,
} from '../visual/production/fixtures-search'

test.use({ viewport: { width: 1280, height: 1000 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const heading = (p: Page) => p.getByRole('heading', { level: 1, name: 'Search' })
const bar = (p: Page) => landmark(p, 'Search')
const chipsRow = (p: Page) => p.locator('[data-search-chips]')
const chip = (p: Page, key: string) => chipsRow(p).locator(`[data-search-chip="${key}"]`)
const notes = (p: Page) => p.locator('[data-search-notes] li')
const box = (p: Page) => p.getByLabel('Search across the workspace')

async function open(page: Page, path: string) {
  watchNativeDialogs(page)
  const world = searchWorld({ global: syntaxGlobal() })
  const opened = await openProductionPage(page, path, {
    theme: 'signal',
    now: NOW,
    me: world.user,
    user: world.user,
    projectId: P,
    handlers: world.handlers,
    ready: heading,
  })
  return { ...opened, world }
}
const at = (q: string, scope = 'all') => `/search?q=${encodeURIComponent(q)}&scope=${scope}`

test('three filters on Tests: the Filters row between the input and Mode, chips in source order as text, the raw query sent', async ({ page }) => {
  const { api, errors } = await open(page, at(SYNTAX_QUERY, 'tests'))
  await expect(chipsRow(page)).toBeVisible()
  await expect(chipsRow(page).getByText('Filters', { exact: true })).toBeVisible()
  const texts = await chipsRow(page).locator('[data-search-chip]').allTextContents()
  expect(texts.map((t) => t.trim())).toEqual(['status: failed', 'suite: API Regression', 'since: 7d'])
  await expect(chip(page, 'suite')).toHaveAttribute('title', 'suite:"API Regression"')
  await expect(notes(page), 'nothing ignored, nothing unsupported').toHaveCount(0)
  // Inside the command bar, between the input and the Mode / Scope row.
  const [input, row, mode] = [await boxOf(box(page)), await boxOf(chipsRow(page)), await boxOf(page.getByRole('radiogroup', { name: 'Search mode' }))]
  expect(await bar(page).locator('[data-search-chips]').count(), 'inside the command bar').toBe(1)
  expect(input.top < row.top && row.top < mode.top, 'input → chips → mode').toBe(true)
  await networkQuiet(page, api)
  const sent = queryOf(api, GLOBAL_API).filter((q) => q.get('q') === SYNTAX_QUERY)
  expect(sent.length, 'the raw query, as typed').toBeGreaterThanOrEqual(1)
  expect(sent[0].get('entity_types')).toBe('test_case')
  await expectNoOverlay(page)
  assertHermetic(api, errors)
})

test('a chip\'s × removes exactly its token and re-runs the search', async ({ page }) => {
  const { api, errors } = await open(page, at(SYNTAX_QUERY, 'tests'))
  await expect(chip(page, 'since')).toBeVisible()
  await networkQuiet(page, api)
  const before = queryOf(api, GLOBAL_API).length
  await chip(page, 'since').getByRole('button', { name: 'Remove since: 7d' }).click()
  await expect(box(page)).toHaveValue(SYNTAX_QUERY_WITHOUT_SINCE)
  await expect(chip(page, 'since')).toHaveCount(0)
  await expect(chipsRow(page).locator('[data-search-chip]')).toHaveCount(2)
  await networkQuiet(page, api)
  const rerun = queryOf(api, GLOBAL_API).slice(before)
  expect(rerun.map((q) => [q.get('q'), q.get('entity_types')])).toEqual([[SYNTAX_QUERY_WITHOUT_SINCE, 'test_case']])
  await expect(page).toHaveURL((url) => url.searchParams.get('q') === SYNTAX_QUERY_WITHOUT_SINCE)
  assertHermetic(api, errors)
})

const NOTES: { q: string; scope: string; note: string; chips: string[]; tone: 'warning' | 'muted' }[] = [
  { q: 'branch:main', scope: 'defects', note: 'branch: is not supported for Defects, so no defects were searched.', chips: ['branch: main'], tone: 'warning' },
  // On All, ONE sentence per key that keeps two or more kinds out (G-B's condensed rule); one kind keeps the line above.
  { q: 'status:failed', scope: 'all', note: 'status: applies only to Tests and Runs, so suites, defects, flaky tests and releases were not searched.', chips: ['status: failed'], tone: 'warning' },
  { q: 'foo:bar checkout', scope: 'all', note: 'unknown filter foo: searched as text', chips: [], tone: 'muted' },
  { q: 'status:flaky checkout', scope: 'tests', note: 'flaky is not a status: use type:flaky', chips: [], tone: 'muted' },
  { q: 'release:"9.9"', scope: 'tests', note: 'No release named "9.9" in your projects.', chips: ['release: 9.9'], tone: 'warning' },
]

for (const { q, scope, note, chips, tone } of NOTES) {
  test(`${q} on ${scope}: the note "${note}" verbatim`, async ({ page }) => {
    const { api, errors } = await open(page, at(q, scope))
    await expect(notes(page)).toHaveText([note])
    const texts = await chipsRow(page).locator('[data-search-chip]').allTextContents()
    expect(texts.map((t) => t.trim())).toEqual(chips)
    // The ink says what kind of note it is: an ignored token is muted, an unsupported / unresolved one a warning.
    const ink = await notes(page).first().evaluate((el) => getComputedStyle(el).color)
    const expected = await page.evaluate((token) => {
      const probe = document.createElement('span')
      probe.style.color = `var(${token})`
      document.body.appendChild(probe)
      const color = getComputedStyle(probe).color
      probe.remove()
      return color
    }, tone === 'warning' ? '--ui-warning-ink' : '--color-text-muted')
    expect(ink).toBe(expected)
    expect(SYNTAX_PARSED[q], 'the fixture answered this query').toBeTruthy()
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })
}

test('a plain query has no chips row: the command bar keeps its height', async ({ page }) => {
  const plain = await open(page, at('checkout'))
  await expect(page.getByRole('heading', { name: /^Results/ })).toBeVisible()
  await networkQuiet(page, plain.api)
  await expect(chipsRow(page)).toHaveCount(0)
  await expect(bar(page).getByText('Filters', { exact: true })).toHaveCount(0)
  const height = await bar(page).evaluate((el) => Math.round(el.getBoundingClientRect().height))
  inPageLine('/search command-bar plain', 1280, { height })
  assertHermetic(plain.api, plain.errors)
})

test('Syntax: closed by default, then the eight keys with an example and the kinds each applies to', async ({ page }) => {
  const { api, errors } = await open(page, at(SYNTAX_QUERY, 'tests'))
  const toggle = chipsRow(page).getByRole('button', { name: /^Syntax/ })
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await expect(page.locator('[data-search-syntax]')).toHaveCount(0)
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  const table = page.locator('[data-search-syntax] table')
  await expect(table.locator('thead th')).toHaveText(['Key', 'Example', 'Applies to'])
  await expect(table.locator('tbody tr')).toHaveCount(8)
  await expect(table.locator('tbody tr td:first-child')).toHaveText(['status:', 'suite:', 'branch:', 'env:', 'type:', 'since:', 'release:', 'owner:'])
  await expect(table.locator('[data-syntax-key="owner"]')).toContainText('Tests, Defects')
  await expect(table.locator('[data-syntax-key="since"]')).toContainText('Every kind')
  await expectNoOverlay(page)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

for (const width of [1280, 1440]) {
  test(`@${width}: the chips row, its notes and the open Syntax table fit the command bar`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 })
    const { api, errors } = await open(page, at(SYNTAX_QUERY, 'tests'))
    await chipsRow(page).getByRole('button', { name: /^Syntax/ }).click()
    await expect(page.locator('[data-search-syntax] table')).toBeVisible()
    await networkQuiet(page, api)
    await expectNoHorizontalOverflow(page, `/search syntax @${width}`)
    expect(await ownOverflow(chipsRow(page)), 'the chips row scrolls sideways').toBeLessThanOrEqual(1)
    const [barBox, table] = [await boxOf(bar(page)), await boxOf(page.locator('[data-search-syntax] table'))]
    expect(table.right, 'the table stays inside the command bar').toBeLessThanOrEqual(barBox.right)
    inPageLine('/search syntax', width, { bar: barBox.width, table: table.width, slack: barBox.right - table.right })
    assertHermetic(api, errors)
  })
}
