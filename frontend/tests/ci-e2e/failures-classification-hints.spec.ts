/**
 * W07 FAIL-10 (`docs/deferred-scope-2026-10-13/work/W07-contract.md` §2.9), hermetic, on
 * `/failures?tab=categories`:
 *
 *  - a QA lead opens **Classification hints** from the Categories card; the hint list is asked only then (never at
 *    page load);
 *  - typing `ECONNREFUSED` asks the preview once the typing pauses (`match_field=error_message&pattern=ECONNREFUSED`)
 *    and shows `7 of the last 200 failures match` with its examples as text;
 *  - **Add hint** POSTs exactly the form (`error_message`, `ECONNREFUSED`, `INFRASTRUCTURE`) and the list then shows
 *    `error message contains "ECONNREFUSED" → Infrastructure`, set by the QA lead;
 *  - a QA engineer sees no button.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `hintsWorld()` (`fixtures-runs-failures.ts`,
 * over `FAILURES_ON`).
 */
import { expect, test, type Page } from '@playwright/test'
import { opened } from '../lib/inPage'
import { landmark } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { FAILURES_ON, PROJECT_ID } from '../visual/production/fixtures'
import { QA_ENGINEER } from '../visual/production/fixtures-releases'
import { HINT_PREVIEW, hintsWorld } from '../visual/production/fixtures-runs-failures'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ready = (p: Page) => landmark(p, 'Failure verdict')
const HINTS = `/api/v1/projects/${PROJECT_ID}/classification-hints`
const openButton = (page: Page) => page.getByTestId('open-classification-hints')

test('a QA lead previews and adds a hint; the list shows it', async ({ page }) => {
  const world = hintsWorld()
  const { api, errors } = await openRollout(page, '/failures?tab=categories', { handlers: world.handlers, ready })
  await expect(openButton(page)).toBeVisible()
  await expect(openButton(page)).toHaveText('Classification hints')
  await networkQuiet(page, api)
  expect(requestsTo(api, HINTS), 'no hint read before the modal opens').toEqual([])

  await openButton(page).click()
  const dialog = opened(page, 'Classification hints')
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('Hints classify new failures before the AI does.')
  await expect(dialog.locator('[data-hints-empty]')).toHaveText('No hints yet.')
  expect(requestsTo(api, HINTS)).toEqual([`GET ${HINTS}`])

  const form = dialog.getByRole('form', { name: 'Add a hint' })
  await expect(form.getByRole('button', { name: 'Add hint' })).toBeDisabled()
  await form.getByLabel('Contains').pressSequentially('ECONNREFUSED', { delay: 30 })
  await expect(dialog.locator('[data-hint-preview]')).toContainText('7 of the last 200 failures match')
  await expect(dialog.locator('[data-hint-preview]')).toContainText(`${HINT_PREVIEW.examples[0].test_name}: ${HINT_PREVIEW.examples[0].value}`)
  await networkQuiet(page, api)
  const previews = requestsTo(api, `${HINTS}/preview`).map((line) => new URLSearchParams(line.split('?')[1] ?? ''))
  expect(previews.length, 'the preview is asked').toBeGreaterThan(0)
  const lastPreview = previews[previews.length - 1]
  expect([lastPreview.get('match_field'), lastPreview.get('pattern')]).toEqual(['error_message', 'ECONNREFUSED'])
  expect(previews.every((q) => (q.get('pattern') ?? '').length >= 3), 'never below three characters').toBe(true)

  await form.getByRole('button', { name: 'Add hint' }).click()
  const row = dialog.locator('[data-hint-row]')
  await expect(row).toHaveCount(1)
  await expect(row.locator('[data-hint-sentence]')).toHaveText('error message contains "ECONNREFUSED" → Infrastructure')
  await expect(row).toContainText('set by QA Lead')
  expect(world.writes).toEqual([
    { line: `POST ${HINTS}`, body: { match_field: 'error_message', pattern: 'ECONNREFUSED', category: 'INFRASTRUCTURE' } },
  ])
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('a QA engineer has no Classification hints button', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/failures?tab=categories', { handlers: FAILURES_ON, ready, user: QA_ENGINEER })
  await expect(page.getByRole('heading', { name: 'Failure category distribution' })).toBeVisible()
  await networkQuiet(page, api)
  await expect(openButton(page)).toHaveCount(0)
  expect(requestsTo(api, HINTS)).toEqual([])
  assertHermetic(api, errors)
})
