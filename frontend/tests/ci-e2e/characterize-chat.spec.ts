/**
 * W27a PAN-04 CHARACTERIZATION, `/chat` (`docs/deferred-scope-2026-10-13/work/W27a-contract.md` §2.4, rows C1, C2),
 * hermetic, with `ask_ai_chat` ON. Written against the MODAL tree (an answer's suggested actions open the
 * Propose quarantine and Create Jira issue dialogs, rendered inside the message) and kept green, UNEDITED, after
 * the in-page conversion: each form is found inside its message by the name it keeps (`opened`).
 *
 *  - C1 an answer's Propose quarantine → the prefilled test → a reason → `POST /api/v1/quarantine` with the whole
 *    body (`rationale.source: 'ask-ai-copilot'`, the suggested fail count); Cancel sends nothing; a refused write
 *    (403) keeps the form open with what was typed and toasts the server's sentence;
 *  - C2 an answer's Create Jira issue → the preview for the suggested fingerprint → Create issue: F2's POST
 *    (`{ fingerprint, issue_type, jira_project_key, target }`); one action open at a time per message.
 *
 * The conversation itself (stream, follow-up, retry) is `chat-conversation.spec.ts`.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `chatInPageWorld()` (`fixtures-in-page.ts`).
 */
import { expect, test, type Page } from '@playwright/test'
import { opened } from '../lib/inPage'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import { CHAT_TEST, chatInPageWorld, CREATED_JIRA_KEY, PROPOSE_REFUSAL } from '../visual/production/fixtures-in-page'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const ready = (p: Page) => p.getByRole('heading', { name: 'Ask about Checkout' })
const CHAT_ON = { ask_ai_chat: true }
const JIRA = `/api/v1/projects/${P}/defects/jira`

/** Ask one question; the answer, with its two suggested actions, is on screen. */
async function answered(page: Page) {
  await page.getByTestId('chat-input').fill('Why does card declined keep failing?')
  await page.getByTestId('chat-input').press('Enter')
  const message = page.getByTestId('chat-assistant-message').first()
  await expect(message).toContainText(`${CHAT_TEST.name} failed ${CHAT_TEST.fails} times this week`)
  await expect(message.getByRole('button', { name: 'Propose quarantine' })).toBeVisible()
  await expect(message.getByRole('button', { name: 'Create Jira issue' })).toBeVisible()
  return message
}

test('C1 Propose quarantine from an answer: the prefilled test, the whole POST body; Cancel sends nothing', async ({ page }) => {
  const world = chatInPageWorld()
  const { api, errors } = await openRollout(page, '/chat', { handlers: world.handlers, ready, flags: CHAT_ON })
  const message = await answered(page)

  await message.getByRole('button', { name: 'Propose quarantine' }).click()
  let form = opened(message, 'Propose quarantine')
  await expect(form).toBeVisible()
  await expect(form.getByText(CHAT_TEST.name, { exact: true })).toBeVisible()
  await form.getByRole('textbox').fill('never sent')
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  await message.getByRole('button', { name: 'Propose quarantine' }).click()
  form = opened(message, 'Propose quarantine')
  const submit = form.getByRole('button', { name: 'Propose quarantine' })
  await expect(submit, 'a reason is required').toBeDisabled()
  await form.getByRole('textbox').fill('  Flips on the shared runner  ')
  await submit.click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Quarantine proposal created — pending QA Lead approval under Flaky tests › Proposed.')).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    {
      line: 'POST /api/v1/quarantine',
      body: {
        project_id: P,
        test_fingerprint: CHAT_TEST.fingerprint,
        test_name: CHAT_TEST.name,
        suite_name: CHAT_TEST.suite,
        detection_method: 'manual',
        fail_count: CHAT_TEST.fails,
        rationale: { reason: 'Flips on the shared runner', source: 'ask-ai-copilot' },
      },
    },
  ])
  assertHermetic(api, errors)
})

test('C1 a refused proposal keeps the form open with what was typed and shows the server\'s sentence', async ({ page }) => {
  const world = chatInPageWorld({ refuse: true })
  const { api, errors } = await openRollout(page, '/chat', { handlers: world.handlers, ready, flags: CHAT_ON })
  const message = await answered(page)
  await message.getByRole('button', { name: 'Propose quarantine' }).click()
  const form = opened(message, 'Propose quarantine')
  await form.getByRole('textbox').fill('Flips on the shared runner')
  await form.getByRole('button', { name: 'Propose quarantine' }).click()
  await expect(page.getByText(PROPOSE_REFUSAL).first()).toBeVisible()
  await expect(form, 'the form stays open').toBeVisible()
  await expect(form.getByRole('textbox')).toHaveValue('Flips on the shared runner')
  await networkQuiet(page, api)
  expect(world.writes).toHaveLength(1)
  assertHermetic(api, errors)
})

test('C2 Create Jira issue from an answer: the preview for the suggested fingerprint, then the POST', async ({ page }) => {
  const world = chatInPageWorld()
  const { api, errors } = await openRollout(page, '/chat', { handlers: world.handlers, ready, flags: CHAT_ON })
  const message = await answered(page)
  await networkQuiet(page, api)
  expect(requestsTo(api, `${JIRA}/preview`), 'no preview until the form opens').toEqual([])

  await message.getByRole('button', { name: 'Create Jira issue' }).click()
  const form = opened(message, 'Create Jira issue')
  await expect(form.getByLabel('Prefilled description')).toContainText(`Failure: ${CHAT_TEST.fingerprint}`)
  expect(requestsTo(api, `${JIRA}/preview`)).toEqual([`GET ${JIRA}/preview?fingerprint=${CHAT_TEST.fingerprint}`])
  // One action at a time per message: the quarantine form is not open beside it.
  await expect(opened(message, 'Propose quarantine')).toHaveCount(0)
  await form.getByRole('button', { name: 'Create issue' }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByRole('link', { name: CREATED_JIRA_KEY })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: `POST ${JIRA}`, body: { fingerprint: CHAT_TEST.fingerprint, issue_type: 'Bug', jira_project_key: 'SHOP', target: 'jira' } },
  ])
  assertHermetic(api, errors)
})
