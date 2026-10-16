/**
 * W07 FAIL-08 (`docs/deferred-scope-2026-10-13/work/W07-contract.md` §2.8), hermetic, on `/ownership`: a QA lead
 * lists the project's Java source roots, tests the edited list on recent failures, and saves it; the CODEOWNERS
 * coverage badge then counts the Java failures the roots locate.
 *
 *  - the project starts with no roots: an all-Java sample, so the badge reads `n/a` (not 0 %) and its tooltip
 *    says Java traces are not located;
 *  - adding `src/test/java` and **Test on recent failures** POSTs the EDITED list to the preview and reads
 *    `12 of 15 recent Java failures map to a path; 9 match a path rule (last 30 days).` (nothing saved);
 *  - **Save** PUTs `{roots: [...]}` once, and the badge is asked again: `64%`, its tooltip naming 1 source root;
 *  - removing every root and saving PUTs `{roots: []}`: the badge's `n/a` returns;
 *  - saved roots load as saved, and a refused save shows the server's 422 text once and keeps the edit.
 *
 * Below QA lead the route itself is not reachable (a management page: the shell sends the reader home), so the
 * section's read-only mode is pinned by `src/pages/OwnershipEditorPage.sourceRoots.test.tsx`, not here.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `ownershipWorld()`
 * (`fixtures-runs-failures.ts`).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { respond, type ApiHandlers } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import { ownershipWorld } from '../visual/production/fixtures-runs-failures'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ready = (p: Page) => p.getByRole('region', { name: 'Java source roots' })
const section = ready
const badge = (page: Page): Locator => page.locator('[data-page-header] span').filter({ hasText: /^Coverage/ })
const BASE = `/api/v1/projects/${PROJECT_ID}/ownership`
const COVERAGE = `${BASE}/codeowners/coverage`
const ROOTS = `${BASE}/source-roots`

test('a QA lead previews, saves and removes Java source roots; the coverage badge follows', async ({ page }) => {
  const world = ownershipWorld()
  const { api, errors } = await openRollout(page, '/ownership', { handlers: world.handlers, ready })
  await expect(section(page)).toContainText('Java stack traces name a class and a file, not a path.')
  await expect(section(page)).toContainText('No source roots: Java failures are not located.')
  await expect(badge(page)).toContainText('n/a')
  await expect(badge(page)).toHaveAttribute('title', /Java stack traces are not located\./)
  await networkQuiet(page, api)
  expect(requestsTo(api, ROOTS)).toEqual([`GET ${ROOTS}`])
  const coverageReads = requestsTo(api, COVERAGE).length

  // Add a root, then test the EDITED list: a preview POST, nothing written.
  await section(page).getByRole('textbox', { name: 'Source root' }).fill('src/test/java')
  await section(page).getByRole('button', { name: 'Add' }).click()
  await expect(section(page).locator('[data-source-root-row]')).toHaveCount(1)
  await expect(section(page).locator('[data-source-root-row]')).toContainText('any package')
  await section(page).getByRole('button', { name: 'Test on recent failures' }).click()
  await expect(section(page).locator('[data-source-roots-preview]')).toContainText(
    '12 of 15 recent Java failures map to a path; 9 match a path rule (last 30 days).',
  )
  await expect(section(page).locator('[data-source-roots-preview]')).toContainText(
    'CheckoutTest.pays → src/test/java/com/acme/pay/CheckoutTest.java',
  )
  expect(world.writes).toEqual([{ line: `POST ${ROOTS}/preview`, body: { roots: [{ prefix: '', root: 'src/test/java' }] } }])
  expect(world.state.roots, 'the preview saves nothing').toEqual([])

  // Save: one PUT with the whole list, then the badge is asked again.
  await section(page).getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText('Java source roots saved')).toBeVisible()
  await expect(badge(page)).toContainText('64%')
  await expect(badge(page)).toHaveAttribute('title', /Java traces are located through 1 source root\(s\)\./)
  expect(world.writes.filter((w) => w.line.startsWith('PUT'))).toEqual([
    { line: `PUT ${ROOTS}`, body: { roots: [{ prefix: '', root: 'src/test/java' }] } },
  ])
  await networkQuiet(page, api)
  expect(requestsTo(api, COVERAGE).length, 'the badge refreshed after the save').toBeGreaterThan(coverageReads)
  await expect(section(page).getByRole('button', { name: 'Save' })).toBeDisabled()

  // Remove every root and save: the old behaviour, and the badge's n/a.
  await section(page).getByRole('button', { name: 'Remove source root src/test/java' }).click()
  await expect(section(page)).toContainText('No source roots: Java failures are not located.')
  await section(page).getByRole('button', { name: 'Save' }).click()
  await expect(badge(page)).toContainText('n/a')
  expect(world.writes.filter((w) => w.line.startsWith('PUT')).pop()).toEqual({ line: `PUT ${ROOTS}`, body: { roots: [] } })
  expect(world.state.roots).toEqual([])
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('saved roots load as saved; a refused save shows the server text once and keeps the edit', async ({ page }) => {
  const world = ownershipWorld([{ prefix: 'com.acme.pay', root: 'payments/src/test/java' }])
  const detail = 'Source root "../etc" must be a relative path inside the repository (letters, digits, . _ - /; no ..).'
  const handlers: ApiHandlers = [[ROOTS, () => respond(422, { detail }), 'PUT'], ...world.handlers]
  const { api, errors } = await openRollout(page, '/ownership', { handlers, ready })
  const rows = section(page).locator('[data-source-root-row]')
  await expect(rows).toHaveCount(1)
  await expect(rows).toContainText('com.acme.pay')
  await expect(rows).toContainText('payments/src/test/java')
  await expect(badge(page)).toContainText('64%')
  await expect(badge(page)).toHaveAttribute('title', /Java traces are located through 1 source root\(s\)\./)

  await section(page).getByRole('textbox', { name: 'Source root' }).fill('../etc')
  await section(page).getByRole('button', { name: 'Add' }).click()
  await section(page).getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText(detail)).toHaveCount(1)
  await expect(rows).toHaveCount(2)
  await expect(section(page)).toContainText('Unsaved changes')
  await networkQuiet(page, api)
  expect(requestsTo(api, ROOTS).filter((line) => line.startsWith('PUT'))).toHaveLength(1)
  assertHermetic(api, errors)
})
