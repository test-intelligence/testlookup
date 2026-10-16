/**
 * W06 DEF-05, hermetic (`docs/deferred-scope-2026-10-13/work/W06-contract.md` §2.3, §4.2), on `/defects` with a
 * release picked in the top bar (`?release=`):
 *
 *  - off (the default), the page is today's all-releases view: no `release_id` / `release_match` on any defect
 *    read, whatever the top bar holds, and the banner's "All releases" badge says so;
 *  - "Only this release" → the list, the trend and the escape rate ask for the release (`release_match=affects`
 *    on the list and the trend; the escape rate takes the release only), the table shows the fixture's 2 affecting
 *    defects, "Open defects" reads 2, the chip names the release, `?scope=release` is in the URL, and a reload
 *    keeps all of it;
 *  - with no release picked the toggle is not offered.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-defects.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { landmark, type MockedApi } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { AFFECTING_SCOPE_RELEASE, defectsWorld, SCOPE_RELEASE } from '../visual/production/fixtures-defects'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const R = SCOPE_RELEASE.id
const ready = (p: Page) => landmark(p, 'Defect KPIs')
const toggle = (page: Page) => page.getByTestId('defects-release-scope')
const bodyRows = (page: Page) => page.getByRole('table', { name: 'Defects' }).locator('tbody tr')
const chip = (page: Page) => page.locator('[data-defects-scope-chip]')
const verdict = (page: Page) => landmark(page, 'Defect queue verdict')
const tileValue = (page: Page, title: string) =>
  landmark(page, 'Defect KPIs').locator('[data-metric-card]').filter({ hasText: title }).locator('p.tabular-nums')

/** The query of every read of `path`. */
const queries = (api: MockedApi, path: string) => requestsTo(api, path).map((line) => new URLSearchParams(line.split('?')[1] ?? ''))
const LIST = '/api/v1/analytics/defects'
const TREND = '/api/v1/analytics/defects/trend'
const ESCAPE = '/api/v1/analytics/defects/escape-rate'
const affects = (q: URLSearchParams) => q.get('release_match') === 'affects' && q.getAll('release_id').includes(R)

test('Only this release: affects-scoped reads, the 2 affecting defects, Open defects 2, the chip, and a reload keeps it', async ({ page }) => {
  const { handlers } = defectsWorld({ releases: true })
  const { api, errors } = await openRollout(page, `/defects?release=${R}`, { handlers, ready })

  // Off: every release's defects, and nothing release-shaped on the wire.
  await expect(toggle(page)).toHaveText('Only this release')
  await expect(toggle(page)).toHaveAttribute('aria-pressed', 'false')
  await expect(bodyRows(page)).toHaveCount(9)
  await expect(verdict(page).getByText('All releases', { exact: true })).toBeVisible()
  await expect(chip(page)).toHaveCount(0)
  await networkQuiet(page, api)
  for (const path of [LIST, TREND, ESCAPE]) {
    const qs = queries(api, path)
    expect(qs.length, `${path} was read`).toBeGreaterThan(0)
    expect(qs.filter((q) => q.has('release_id') || q.has('release_match')), `${path}: no release while the toggle is off`).toEqual([])
  }
  const before = api.seen.length

  await toggle(page).click()
  await expect(toggle(page)).toHaveAttribute('aria-pressed', 'true')
  await expect(page).toHaveURL(/[?&]scope=release(&|$)/)
  await expect(bodyRows(page)).toHaveCount(AFFECTING_SCOPE_RELEASE.length)
  await expect(bodyRows(page).filter({ hasText: 'checkout total includes tax' })).toHaveCount(1)
  await expect(bodyRows(page).filter({ hasText: 'login rejects bad password' })).toHaveCount(1)
  await expect(tileValue(page, 'Open defects')).toHaveText('2')
  await expect(chip(page)).toHaveText(`Affecting ${SCOPE_RELEASE.name}`)
  await expect(verdict(page).getByText('All releases', { exact: true })).toHaveCount(0)
  await networkQuiet(page, api)
  const after = { ...api, seen: api.seen.slice(before) }
  expect(queries(after, LIST).filter(affects).length, 'the list asks for the affecting defects').toBeGreaterThan(0)
  expect(queries(after, TREND).filter(affects).length, 'the trend asks for the same scope').toBeGreaterThan(0)
  const escape = queries(after, ESCAPE)
  expect(escape.filter((q) => q.getAll('release_id').includes(R)).length, 'the escape rate asks for the release').toBeGreaterThan(0)
  expect(escape.filter((q) => q.has('release_match') || q.has('suite_name')), 'the escape rate takes the release only').toEqual([])

  // A reload keeps the toggle, the release and the narrowed numbers.
  await page.reload()
  await expect(ready(page)).toBeVisible()
  await expect(toggle(page)).toHaveAttribute('aria-pressed', 'true')
  await expect(bodyRows(page)).toHaveCount(AFFECTING_SCOPE_RELEASE.length)
  await expect(tileValue(page, 'Open defects')).toHaveText('2')
  await expect(chip(page)).toHaveText(`Affecting ${SCOPE_RELEASE.name}`)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('Only this release is offered only while one release is picked', async ({ page }) => {
  const { handlers } = defectsWorld({ releases: true })
  const { api, errors } = await openRollout(page, '/defects', { handlers, ready })
  await expect(bodyRows(page)).toHaveCount(9)
  await expect(toggle(page)).toHaveCount(0)
  await expect(chip(page)).toHaveCount(0)
  await networkQuiet(page, api)
  for (const path of [LIST, TREND, ESCAPE]) {
    expect(queries(api, path).filter((q) => q.has('release_id') || q.has('release_match'))).toEqual([])
  }
  assertHermetic(api, errors)
})
