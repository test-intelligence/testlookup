/**
 * W27a PAN-04 CHARACTERIZATION, `/failures` (`docs/deferred-scope-2026-10-13/work/W27a-contract.md` §2.4, rows F1,
 * F2, F4, F5, F6), hermetic. Written against the MODAL tree (Mute, Create Jira issue, Correct classification,
 * Classification hints and Classify in modal dialogs; the hint delete a native `confirm()`) and kept green,
 * UNEDITED, after the in-page conversion: every container is found by the name it keeps (`opened`), the delete
 * answered whichever way it is asked (`answerConfirm`).
 *
 *  - F1 Mute (QA lead, quarantine on) → a reason → Propose quarantine: `POST /api/v1/quarantine` with the whole
 *    body (the measured flip rate and pass count only for a row the flaky list measured); Cancel sends nothing;
 *    a QA engineer's Mute is disabled `QA Lead role required`, and with the flag off it names the flag;
 *  - F2 Jira → the server-built preview read only once the form opens → Create issue:
 *    `POST /api/v1/projects/{p}/defects/jira` with exactly `{ fingerprint, issue_type, jira_project_key?,
 *    extra_comment?, target }` (another project and type; the webhook target sends no project key); Cancel
 *    sends nothing; a viewer's Jira is disabled `QA Engineer role required`;
 *  - F4 Correct the classification (the headline test) → the analysis lookup read only once it opens → a
 *    category → `POST /api/v1/feedback/{analysis_id}` `{ rating: 'incorrect', corrected_category }`; Cancel
 *    sends nothing;
 *  - F5 Classification hints (QA lead): the toggle → `PATCH …/{hint}` `{ enabled: false }`; Delete → dismissed
 *    nothing, confirmed `DELETE …/{hint}` (the question `Delete this hint?`), the list re-read without it;
 *  - F6 Classify uncategorised failures (from the category card and the overflow menu) → a category →
 *    `POST /api/v1/analytics/classify-uncategorized` `{ project_id, category, days }` and its toast; Cancel sends
 *    nothing; a viewer is offered neither Correct nor Classify.
 *
 * F3 (Suspects) is `fold-failures.spec.ts`; adding a hint is `failures-classification-hints.spec.ts`.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `failuresInPageWorld()`
 * (`fixtures-in-page.ts`, over `hintsWorld()` and `FAILURES_ON`).
 */
import { expect, test, type Page } from '@playwright/test'
import { answerConfirm, opened } from '../lib/inPage'
import { landmark } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import {
  CLASSIFIED_COUNT,
  CREATED_JIRA_URL,
  CREATED_JIRA_KEY,
  failuresInPageWorld,
  SEEDED_HINT,
} from '../visual/production/fixtures-in-page'
import { QA_ENGINEER, VIEWER } from '../visual/production/fixtures-releases'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const ready = (p: Page) => landmark(p, 'Failure verdict')
const QUARANTINE_ON = { flaky_auto_quarantine: true }
const table = (page: Page) => page.locator('[data-primary]').getByRole('table', { name: 'Top failing tests' })
const HEADLINE = { name: 'card declined shows reason', fingerprint: 'fp-top-0', suite: 'Payments', fails: 9 }
const QUARANTINE_OFF =
  'Quarantine is off for this project: the flaky_auto_quarantine flag in Settings › Feature flags is switched off or limited to other projects.'
const JIRA = `/api/v1/projects/${P}/defects/jira`
const HINTS = `/api/v1/projects/${P}/classification-hints`
const HINT_SENTENCE = 'error message contains "ETIMEDOUT" → Infrastructure'

test('F1 Mute → a reason → Propose quarantine: the whole POST body; Cancel sends nothing', async ({ page }) => {
  const world = failuresInPageWorld()
  const { api, errors } = await openRollout(page, '/failures', { handlers: world.handlers, ready, flags: QUARANTINE_ON })
  const mute = table(page).getByRole('button', { name: `Mute test ${HEADLINE.name}` })
  await expect(mute).toBeEnabled()

  await mute.click()
  let form = opened(page, 'Mute test (propose quarantine)')
  await expect(form).toBeVisible()
  await form.getByRole('textbox').fill('never sent')
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  await mute.click()
  form = opened(page, 'Mute test (propose quarantine)')
  await expect(form.getByRole('textbox'), 'a reopened form starts empty').toHaveValue('')
  const propose = form.getByRole('button', { name: 'Propose quarantine' })
  await expect(propose, 'a reason is required').toBeDisabled()
  await form.getByRole('textbox').fill('  Flaky on the shared runner  ')
  await propose.click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Quarantine proposal created — pending QA Lead approval on /quarantine.')).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    {
      line: 'POST /api/v1/quarantine',
      body: {
        project_id: P,
        test_fingerprint: HEADLINE.fingerprint,
        test_name: HEADLINE.name,
        suite_name: HEADLINE.suite,
        detection_method: 'manual',
        fail_count: HEADLINE.fails,
        rationale: { reason: 'Flaky on the shared runner', source: 'failure-analysis-page' },
      },
    },
  ])

  // A row the flaky list measured (4 of 12 runs failed): its flip rate and pass count travel too.
  await table(page).getByRole('button', { name: 'Mute test token refresh' }).click()
  form = opened(page, 'Mute test (propose quarantine)')
  await expect(form).toContainText('Flake history: failed 4 of 12 runs (33% in this window).')
  await form.getByRole('textbox').fill('Timing-sensitive wait')
  await form.getByRole('button', { name: 'Propose quarantine' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes.slice(1)).toEqual([
    {
      line: 'POST /api/v1/quarantine',
      body: {
        project_id: P,
        test_fingerprint: 'fp-flaky-1',
        test_name: 'token refresh',
        suite_name: 'Auth',
        detection_method: 'manual',
        fail_count: 4,
        flip_rate: 33.3 / 100, // `failure_rate_pct / 100`, as a float (0.33299999999999996)
        pass_count: 8,
        rationale: { reason: 'Timing-sensitive wait', source: 'failure-analysis-page' },
      },
    },
  ])
  assertHermetic(api, errors)
})

test('F1 role gating: a QA engineer\'s Mute is disabled "QA Lead role required" (Jira stays enabled)', async ({ page }) => {
  const world = failuresInPageWorld()
  const { api, errors } = await openRollout(page, '/failures', { handlers: world.handlers, ready, flags: QUARANTINE_ON, user: QA_ENGINEER })
  const mute = table(page).getByRole('button', { name: `Mute test ${HEADLINE.name}` })
  await expect(mute).toBeDisabled()
  await expect(mute).toHaveAttribute('title', 'QA Lead role required')
  // A QA engineer may still file Jira.
  await expect(table(page).getByRole('button', { name: `Create Jira issue for ${HEADLINE.name}` })).toBeEnabled()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

test('F1 with quarantine off: a QA lead\'s Mute is disabled with the flag\'s sentence', async ({ page }) => {
  const world = failuresInPageWorld()
  const { api, errors } = await openRollout(page, '/failures', { handlers: world.handlers, ready })
  const mute = table(page).getByRole('button', { name: `Mute test ${HEADLINE.name}` })
  await expect(mute).toBeDisabled()
  await expect(mute).toHaveAttribute('title', QUARANTINE_OFF)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

test('F2 Jira → the preview read on open → Create issue: exactly its body; another project and type; the webhook; Cancel', async ({ page }) => {
  const world = failuresInPageWorld()
  const { api, errors } = await openRollout(page, '/failures', { handlers: world.handlers, ready })
  const jira = table(page).getByRole('button', { name: `Create Jira issue for ${HEADLINE.name}` })
  await networkQuiet(page, api)
  expect(requestsTo(api, `${JIRA}/preview`), 'no preview until the form opens').toEqual([])

  // Cancel: nothing.
  await jira.click()
  let form = opened(page, 'Create Jira issue')
  await expect(form.getByLabel('Prefilled description')).toContainText(`Failure: ${HEADLINE.fingerprint}`)
  expect(requestsTo(api, `${JIRA}/preview`)).toEqual([`GET ${JIRA}/preview?fingerprint=${HEADLINE.fingerprint}`])
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  // The defaults (Jira, SHOP, Bug) and a comment.
  await jira.click()
  form = opened(page, 'Create Jira issue')
  await expect(form.getByRole('radio', { name: 'Jira' })).toHaveAttribute('aria-checked', 'true')
  await expect(form.getByLabel('Jira project')).toHaveValue('SHOP')
  await expect(form.getByLabel('Issue type')).toHaveValue('Bug')
  await form.getByRole('textbox').fill('  Seen on every build since 238  ')
  await form.getByRole('button', { name: 'Create issue' }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByRole('link', { name: CREATED_JIRA_KEY })).toHaveAttribute('href', CREATED_JIRA_URL)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    {
      line: `POST ${JIRA}`,
      body: { fingerprint: HEADLINE.fingerprint, issue_type: 'Bug', jira_project_key: 'SHOP', extra_comment: 'Seen on every build since 238', target: 'jira' },
    },
  ])

  // Another project and type, no comment.
  await jira.click()
  form = opened(page, 'Create Jira issue')
  await form.getByLabel('Jira project').selectOption('QA')
  await form.getByLabel('Issue type').selectOption('Task')
  await form.getByRole('button', { name: 'Create issue' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes.slice(1)).toEqual([
    { line: `POST ${JIRA}`, body: { fingerprint: HEADLINE.fingerprint, issue_type: 'Task', jira_project_key: 'QA', target: 'jira' } },
  ])

  // The webhook target: no Jira project key.
  await jira.click()
  form = opened(page, 'Create Jira issue')
  await form.getByRole('radio', { name: 'Webhook event' }).click()
  await expect(form.getByLabel('Jira project'), 'no Jira pickers for the webhook').toHaveCount(0)
  await form.getByRole('button', { name: 'Emit webhook event' }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('defect.create_requested sent to 1 webhook subscription.')).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes.slice(2)).toEqual([{ line: `POST ${JIRA}`, body: { fingerprint: HEADLINE.fingerprint, issue_type: 'Bug', target: 'webhook' } }])
  assertHermetic(api, errors)
})

test('F2 role gating: a viewer\'s Jira and Mute are disabled and nothing is read or sent', async ({ page }) => {
  const world = failuresInPageWorld()
  const { api, errors } = await openRollout(page, '/failures', { handlers: world.handlers, ready, flags: QUARANTINE_ON, user: VIEWER })
  const jira = table(page).getByRole('button', { name: `Create Jira issue for ${HEADLINE.name}` })
  await expect(jira).toBeDisabled()
  await expect(jira).toHaveAttribute('title', 'QA Engineer role required')
  await expect(table(page).getByRole('button', { name: `Mute test ${HEADLINE.name}` })).toHaveAttribute('title', 'QA Lead role required')
  await networkQuiet(page, api)
  expect(requestsTo(api, `${JIRA}/preview`)).toEqual([])
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

test('F4 Correct the classification: the lookup read on open, then the feedback POST; Cancel sends nothing', async ({ page }) => {
  const world = failuresInPageWorld()
  const { api, errors } = await openRollout(page, '/failures?tab=categories', { handlers: world.handlers, ready })
  const LOOKUP = `/api/v1/projects/${P}/analyses/lookup`
  const correct = page.getByRole('button', { name: 'Correct the classification →' })
  await networkQuiet(page, api)
  expect(requestsTo(api, LOOKUP), 'no lookup until the form opens').toEqual([])

  await correct.click()
  let form = opened(page, 'Correct classification')
  await expect(form.getByText(HEADLINE.name, { exact: true })).toBeVisible()
  await expect(form.getByText('UNKNOWN', { exact: true })).toBeVisible()
  expect(requestsTo(api, LOOKUP)).toEqual([`GET ${LOOKUP}?fingerprint=${HEADLINE.fingerprint}`])
  await form.getByRole('radio', { name: /^Product Bug/ }).click()
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  await correct.click()
  form = opened(page, 'Correct classification')
  await form.getByRole('radio', { name: /^Product Bug/ }).click()
  await form.getByRole('button', { name: 'Record correction' }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Correction recorded — feeds the next training export.')).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: `POST /api/v1/feedback/analysis-${HEADLINE.fingerprint}`, body: { rating: 'incorrect', corrected_category: 'PRODUCT_BUG' } },
  ])
  assertHermetic(api, errors)
})

test('F5 Classification hints: the toggle PATCHes { enabled: false }; Delete dismissed sends nothing, confirmed DELETEs it', async ({ page }) => {
  const world = failuresInPageWorld()
  const { api, errors } = await openRollout(page, '/failures?tab=categories', { handlers: world.handlers, ready })
  await page.getByTestId('open-classification-hints').click()
  const hints = opened(page, 'Classification hints')
  const row = hints.locator(`[data-hint-row="${SEEDED_HINT.id}"]`)
  await expect(row.locator('[data-hint-sentence]')).toHaveText(HINT_SENTENCE)
  const enabled = row.getByRole('checkbox', { name: `Enabled: ${HINT_SENTENCE}` })
  await expect(enabled).toBeChecked()
  const reads = requestsTo(api, HINTS).length

  await enabled.click()
  await expect(page.getByText('Hint updated')).toBeVisible()
  await expect(enabled).not.toBeChecked()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `PATCH ${HINTS}/${SEEDED_HINT.id}`, body: { enabled: false } }])
  expect(requestsTo(api, HINTS).length, 'the list re-reads').toBeGreaterThan(reads)

  const del = row.getByRole('button', { name: `Delete hint: ${HINT_SENTENCE}` })
  const dismissed = await answerConfirm(page, del, 'dismiss', { confirmLabel: 'Delete' })
  expect(dismissed.message).toBe('Delete this hint?')
  await networkQuiet(page, api)
  expect(world.writes).toHaveLength(1)
  await expect(row).toBeVisible()

  await answerConfirm(page, del, 'accept', { confirmLabel: 'Delete' })
  await expect(page.getByText('Hint deleted')).toBeVisible()
  await expect(row).toHaveCount(0)
  await expect(hints.locator('[data-hints-empty]')).toHaveText('No hints yet.')
  await networkQuiet(page, api)
  expect(world.writes.slice(1)).toEqual([{ line: `DELETE ${HINTS}/${SEEDED_HINT.id}`, body: null }])
  assertHermetic(api, errors)
})

test('F6 Classify uncategorised failures, from the card and from the overflow menu: the POST and its toast; Cancel sends nothing', async ({ page }) => {
  const world = failuresInPageWorld()
  const { api, errors } = await openRollout(page, '/failures?tab=categories', { handlers: world.handlers, ready })

  // From the overflow menu: Cancel.
  await page.locator('[data-page-header]').getByRole('button', { name: 'More actions' }).click()
  await page.getByRole('menuitem', { name: 'Classify uncategorised failures' }).click()
  let form = opened(page, 'Classify uncategorised failures')
  await expect(form).toBeVisible()
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  // From the category card: a category.
  await page.getByRole('button', { name: 'Classify them so owners can be auto-routed →' }).click()
  form = opened(page, 'Classify uncategorised failures')
  await expect(form).toContainText('Every failing test in the last 30 days that has no category yet will be tagged with the selected category.')
  await form.getByRole('button', { name: /^Infrastructure/ }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText(`Tagged ${CLASSIFIED_COUNT} failures as infrastructure.`)).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: 'POST /api/v1/analytics/classify-uncategorized', body: { project_id: P, category: 'INFRASTRUCTURE', days: 30 } },
  ])
  assertHermetic(api, errors)
})

test('F4 / F6 role gating: a viewer is offered neither Correct nor Classify', async ({ page }) => {
  const world = failuresInPageWorld()
  const { api, errors } = await openRollout(page, '/failures?tab=categories', { handlers: world.handlers, ready, user: VIEWER })
  await expect(page.getByRole('heading', { name: 'Failure category distribution' })).toBeVisible()
  await expect(page.getByText(/Classifier confidence low/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Correct the classification →' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Classify them so owners can be auto-routed →' })).toHaveCount(0)
  await expect(page.getByTestId('open-classification-hints')).toHaveCount(0)
  await page.locator('[data-page-header]').getByRole('button', { name: 'More actions' }).click()
  await expect(page.getByRole('menuitem', { name: 'Classify uncategorised failures' })).toHaveCount(0)
  await page.keyboard.press('Escape')
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})
