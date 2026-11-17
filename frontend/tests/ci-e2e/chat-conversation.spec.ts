/**
 * Ask AI (`/chat`), hermetic: a conversation, end to end in the browser.
 *
 * Rebuilt 2026-10-09 after a reference K3s install evaluation (blank 7–10 s waits, tables
 * as raw `|` text, every project's conversations in one list, provider
 * failures saved as answers). Held here:
 *   - the request inventory of one load (the session list is the active
 *     project's: `?project_id=`);
 *   - a starter question streams a grounded answer: a rendered table, the
 *     builds and tests it names linked, how it was answered;
 *   - a follow-up in the same conversation;
 *   - a failed answer says why and Retry answers the same question.
 *
 * The stream is `text/event-stream` served whole (`respondRaw`): Playwright
 * cannot deliver a body in pieces, so incremental rendering is pinned by the
 * unit tests (`chatStream.test.ts`, `useChat.test.tsx`).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock).
 */
import { expect, test, type Page } from '@playwright/test'
import { SHELL_BASE, expectInventory, networkQuiet, openRollout } from '../lib/rollout'
import { respondRaw, type ApiHandlers } from '../lib/production-pages'
import { LAYOUT, PROJECT_ID } from '../visual/production/fixtures'
import { VIEWER } from '../visual/production/fixtures-releases'

const P = PROJECT_ID
const SESSION = '99999999-9999-4999-8999-999999999999'
const RUN = '33333333-3333-4333-8333-333333333333'
const CASE = '44444444-4444-4444-8444-444444444444'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ready = (p: Page) => p.getByRole('heading', { name: 'Ask about Checkout' })

const sse = (events: Array<[string, unknown]>) =>
  respondRaw(events.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join(''), 'text/event-stream')

interface Turn {
  question: string
  answer?: string
  error?: { code: string; message: string; retryable: boolean }
  sources?: unknown[]
}

/** A chat backend in memory: sessions, messages, and scripted turns. */
function chatHandlers(script: Turn[]) {
  const posted: unknown[] = []
  const messages: Array<Record<string, unknown>> = []
  let sessions: Array<Record<string, unknown>> = []
  let n = 0
  const handlers: ApiHandlers = [
    ['/api/v1/settings/ai/mode', () => ({ analysis_mode: 'llm' })],
    ['/api/v1/chat/run-summaries', () => [{
      test_run_id: RUN, project_id: P, build_number: '105', executive_summary: 'Build **105** completed — 2 tests failed.',
      markdown_report: null, anomaly_count: 0, is_regression: false, analysis_count: 0, generated_at: '2026-09-18T10:00:00Z', is_stub: true,
    }]],
    ['/api/v1/chat/sessions', () => sessions],
    ['/api/v1/chat/sessions', () => {
      sessions = [{ id: SESSION, project_id: P, title: 'New conversation', created_at: '2026-09-18T12:00:00Z', updated_at: '2026-09-18T12:00:00Z' }]
      return sessions[0]
    }, 'POST'],
    [`/api/v1/chat/sessions/${SESSION}/messages`, () => messages],
    [`/api/v1/chat/sessions/${SESSION}/messages/stream`, ({ route }) => {
      const body = route.request().postDataJSON() as { message?: string; retry?: boolean }
      posted.push(body)
      const turn = script[n++]
      let questionId = `q-${n}`
      if (body.message) {
        messages.push({ id: questionId, session_id: SESSION, role: 'user', content: body.message, sources: null, created_at: `2026-09-18T12:0${n}:00Z` })
        sessions[0].title = sessions[0].title === 'New conversation' ? body.message.slice(0, 80) : sessions[0].title
      } else {
        questionId = String(messages[messages.length - 1].id)
      }
      const start: [string, unknown] = ['start', { user_message_id: questionId, retry: !!body.retry }]
      if (turn.error) return sse([start, ['status', { label: 'Reading the release-gate verdict…', tool: 'get_release_gate_verdict' }], ['error', turn.error]])
      const answer = {
        id: `a-${n}`, session_id: SESSION, role: 'assistant', content: turn.answer, created_at: `2026-09-18T12:0${n}:30Z`,
        sources: [...(turn.sources ?? []), { type: 'meta', status: 'complete', model: 'mistralai/mistral-nemo', first_token_ms: 1400, total_ms: 4300, tool_calls: 1 }],
      }
      messages.push(answer)
      const words = String(turn.answer).split(' ')
      return sse([
        start,
        ['status', { label: 'Comparing the latest build with the previous one…', tool: 'compare_builds' }],
        ...words.map((w, i): [string, unknown] => ['delta', { text: w + (i < words.length - 1 ? ' ' : '') }]),
        ['done', { message: answer, sources: turn.sources ?? [], tool_trace: [], suggested_actions: [], meta: answer.sources.at(-1) }],
      ])
    }, 'POST'],
    ...LAYOUT,
  ]
  return { handlers, posted }
}

const LOAD = [
  ...SHELL_BASE,
  // The page's own mode read (the composer is off in Rules/ML mode), as on
  // the release gate: the lazy page mounts after the shell's read settled.
  'GET /api/v1/settings/ai/mode',
  `GET /api/v1/chat/sessions?project_id=${P}`,
  `GET /api/v1/chat/run-summaries?project_id=${P}&days=5`,
]

test('a conversation: a starter question, a follow-up, a failure and Retry', async ({ page }) => {
  const { handlers, posted } = chatHandlers([
    {
      question: 'What changed since the previous build? Any new failures?',
      answer: 'Build 105 has one new failure:\n\n| Test | Before | Now |\n|---|---|---|\n| test_refund_flow | PASSED | FAILED |',
      sources: [
        { type: 'test_run', id: RUN, build: '105' },
        { type: 'test_case', id: CASE, run_id: RUN, name: 'test_refund_flow' },
      ],
    },
    { question: 'Has it failed before?', answer: 'No. test_refund_flow failed only in build 105.', sources: [{ type: 'test_run', id: RUN, build: '105' }] },
    { question: 'Is it ready to release?', error: { code: 'timeout', message: 'The AI provider took too long to answer. Try again, or ask a narrower question.', retryable: true } },
    { question: '(retry)', answer: 'The release gate says NO_GO for build 105.' },
  ])
  const { api, errors } = await openRollout(page, '/chat', { handlers, flags: { ask_ai_chat: true }, ready })
  await networkQuiet(page, api)
  expectInventory(api, errors, LOAD, '/chat')

  // A starter question.
  await page.getByTestId('chat-starter').filter({ hasText: 'What changed since the previous build?' }).click()
  const first = page.getByTestId('chat-assistant-message').first()
  await expect(first.getByRole('table')).toBeVisible()
  await expect(first.getByRole('cell', { name: 'test_refund_flow' })).toBeVisible()
  await expect(first).not.toContainText('|---|')
  await expect(first.getByRole('link', { name: 'Build 105' })).toHaveAttribute('href', `/runs/${RUN}`)
  await expect(first.getByRole('link', { name: 'test_refund_flow' })).toHaveAttribute('href', `/runs/${RUN}/tests/${CASE}`)
  await expect(first.getByTestId('chat-answer-meta')).toHaveText('mistral-nemo · first words in 1.4 s · answered in 4.3 s · 1 lookup')
  // The conversation is listed under its first question.
  await expect(page.getByTestId('chat-session')).toHaveText('What changed since the previous build? Any new failures?')

  // A follow-up in the same conversation.
  await page.getByTestId('chat-input').fill('Has it failed before?')
  await page.getByTestId('chat-input').press('Enter')
  await expect(page.getByTestId('chat-assistant-message').nth(1)).toContainText('failed only in build 105')
  await expect(page.getByTestId('chat-user-message')).toHaveCount(2)

  // A failure says why; Retry answers the same question, sent once.
  await page.getByTestId('chat-input').fill('Is it ready to release?')
  await page.getByTestId('chat-send').click()
  await expect(page.getByTestId('chat-error')).toContainText('The AI provider took too long to answer.')
  await page.getByTestId('chat-error').getByRole('button', { name: 'Retry' }).click()
  await expect(page.getByTestId('chat-assistant-message').nth(2)).toContainText('NO_GO for build 105')
  await expect(page.getByTestId('chat-user-message')).toHaveCount(3)
  await expect(page.getByTestId('chat-error')).toHaveCount(0)

  expect(posted).toEqual([
    { message: 'What changed since the previous build? Any new failures?' },
    { message: 'Has it failed before?' },
    { message: 'Is it ready to release?' },
    { retry: true },
  ])
  expect(errors).toEqual([])
  expect(api.unhandled).toEqual([])
})

// D-0010 (GPT-6): a Viewer asked for "a link to the run and one link per failure"; the saved answer said it could not
// give links and to search Runs, above the three links the app had attached. The model is now told those links exist
// (`chat_system` v5, pinned in backend/tests/test_chat_agent_stream.py, where the system message is read); here, the
// saved answer reopened shows the run and each failure it names as links, and reopening it generates nothing.
const CASE_2 = '55555555-5555-4555-8555-555555555555'

test('D-0010: a saved answer that names a build and its failures links each of them, for a Viewer', async ({ page }) => {
  const question = "List build 105's failing tests with a link to the run and one link per failure."
  const sources = [
    { type: 'test_run', id: RUN, build: '105' },
    { type: 'test_case', id: CASE, run_id: RUN, name: 'test_refund_flow' },
    { type: 'test_case', id: CASE_2, run_id: RUN, name: 'test_tax_rounding' },
  ]
  const posted: string[] = []
  const handlers: ApiHandlers = [
    ['/api/v1/settings/ai/mode', () => ({ analysis_mode: 'llm' })],
    ['/api/v1/chat/run-summaries', () => []],
    ['/api/v1/chat/sessions', () => [{ id: SESSION, project_id: P, title: question, created_at: '2026-09-18T12:00:00Z', updated_at: '2026-09-18T12:01:00Z' }]],
    [`/api/v1/chat/sessions/${SESSION}/messages`, () => [
      { id: 'q-1', session_id: SESSION, role: 'user', content: question, sources: null, created_at: '2026-09-18T12:00:00Z' },
      {
        id: 'a-1', session_id: SESSION, role: 'assistant', created_at: '2026-09-18T12:00:30Z',
        content: 'Build 105 has 2 failing tests: test_refund_flow and test_tax_rounding. The run and each test are linked under this answer.',
        sources: [...sources, { type: 'meta', status: 'complete', model: 'anthropic/claude-haiku-5.5', first_token_ms: 900, total_ms: 3100, tool_calls: 1 }],
      },
    ]],
    [`/api/v1/chat/sessions/${SESSION}/messages/stream`, ({ route }) => {
      posted.push(route.request().url())
      return sse([['error', { code: 'unexpected', message: 'no turn was asked for', retryable: false }]])
    }, 'POST'],
    ...LAYOUT,
  ]
  const { api, errors } = await openRollout(page, '/chat', { handlers, flags: { ask_ai_chat: true }, user: VIEWER, ready })
  await networkQuiet(page, api)

  await page.getByTestId('chat-session').filter({ hasText: question }).click()
  const answer = page.getByTestId('chat-assistant-message')
  await expect(answer).toHaveCount(1)
  const links = answer.getByTestId('chat-sources').getByRole('link')
  await expect(links).toHaveCount(3)
  await expect(links.nth(0)).toHaveText('Build 105')
  await expect(links.nth(0)).toHaveAttribute('href', `/runs/${RUN}`)
  await expect(links.nth(1)).toHaveText('test_refund_flow')
  await expect(links.nth(1)).toHaveAttribute('href', `/runs/${RUN}/tests/${CASE}`)
  await expect(links.nth(2)).toHaveText('test_tax_rounding')
  await expect(links.nth(2)).toHaveAttribute('href', `/runs/${RUN}/tests/${CASE_2}`)
  await networkQuiet(page, api)

  expect(posted, 'reopening a saved answer asks the model nothing').toEqual([])
  expect(api.seen.filter((line) => line.startsWith('POST '))).toEqual([])
  expect(errors).toEqual([])
  expect(api.unhandled).toEqual([])
})
