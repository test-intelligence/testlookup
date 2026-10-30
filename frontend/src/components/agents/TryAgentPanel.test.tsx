/**
 * W23c AGT-11: `/agents` › Try an agent — the panel, the invocation detail and their pure model.
 *
 * The REAL hooks (`useAgentCatalog`, `useAgentCatalogAgent`, `useInvocation`) and service run in an isolated
 * SWRConfig; only the HTTP helpers (`services/http`) are mocked — the unit-test network guard sees no request,
 * and every assertion on a request is on the path, body and headers the browser would send.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { REPORTS_QUEUE_HREF } from '@/pages/inbox/agentActionModel'
import {
  RG_INVOCATION_ID,
  RG_PROJECT_ID,
  RG_RUN_ID,
  catalogEntry,
  invocationAwaitingReview,
  invocationInProgress,
  mockCatalog,
  summaryDetail,
} from '@/test/reviewGateInlineMocks'
import { INLINE_ERRORS, INLINE_SENTENCES, inlineTemplate, type AgentInvocation } from '@/types/agentInvoke'
import TryAgentPanel from './TryAgentPanel'
import {
  INVOKE_FAILED,
  NO_RUN,
  NOT_AN_OBJECT,
  agentOptionLabel,
  byteCounter,
  inputLine,
  invocationHref,
  invokableAgents,
  overCap,
  parseInline,
  payloadBytes,
  refusalLines,
  requestBody,
  reviewLine,
  statusWord,
} from './tryAgentModel'

const { http } = vi.hoisted(() => ({ http: { getData: vi.fn(), postData: vi.fn() } }))

vi.mock('@/services/http', () => ({
  getData: (...a: unknown[]) => http.getData(...a),
  postData: (...a: unknown[]) => http.postData(...a),
}))

const CATALOG = '/api/v1/agents/catalog'
const DETAIL = '/api/v1/agents/catalog/agent.summary.v1'
const INVOKE = '/api/v1/agents/agent.summary.v1/invoke'
const INVOCATION = `/api/v1/agents/invocations/${RG_INVOCATION_ID}`
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/

/** The answers `GET …/invocations/{id}` gives, in order (the last one repeats). */
let invocationSequence: AgentInvocation[] = []

function axiosRefusal(status: number, detail: unknown) {
  return Object.assign(new Error(`Request failed with status code ${status}`), {
    isAxiosError: true,
    response: { status, data: { detail } },
  })
}

function serve() {
  http.getData.mockImplementation((url: string) => {
    if (url === CATALOG) return Promise.resolve(mockCatalog())
    if (url === DETAIL) return Promise.resolve(summaryDetail())
    if (url === INVOCATION) {
      const next = invocationSequence.length > 1 ? invocationSequence.shift() : invocationSequence[0]
      return next ? Promise.resolve(next) : Promise.reject(new Error('no invocation'))
    }
    return Promise.reject(new Error(`unexpected GET ${url}`))
  })
}

const gets = (url: string) => http.getData.mock.calls.filter((c) => c[0] === url).length

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname + location.search}</div>
}

/** `runId: null` = no run picked on the page. */
function renderPanel({ url = `/agents?run=${RG_RUN_ID}`, runId = RG_RUN_ID as string | null } = {}) {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route
            path="/agents"
            element={(
              <>
                <TryAgentPanel runId={runId ?? undefined} runLabel="API Regression · Run #7" projectId={RG_PROJECT_ID} onClose={() => {}} />
                <LocationProbe />
              </>
            )}
          />
        </Routes>
      </MemoryRouter>
    </SWRConfig>,
  )
}

async function pickSummary() {
  const select = await screen.findByLabelText('Agent')
  await waitFor(() => expect(select).not.toBeDisabled())
  fireEvent.change(select, { target: { value: 'agent.summary.v1' } })
  return (await screen.findByLabelText('Inline input (JSON)', { selector: 'textarea' })) as HTMLTextAreaElement
}

beforeEach(() => {
  vi.clearAllMocks()
  invocationSequence = [invocationAwaitingReview()]
  serve()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('tryAgentModel', () => {
  it('lists the invokable agents and names each, saying which accept inline input', () => {
    const agents = invokableAgents(mockCatalog())
    expect(agents.map((a) => a.agent_id)).toEqual(['agent.summary.v1', 'agent.root_cause_analysis.v1'])
    expect(agents.map(agentOptionLabel)).toEqual([
      'agent.summary.v1 — Summary · accepts inline input',
      'agent.root_cause_analysis.v1 — Root cause analysis',
    ])
    expect(agentOptionLabel(catalogEntry({ inline_input_supported: undefined }))).toBe('agent.summary.v1 — Summary')
  })

  it('parses the editor, measures what the server measures and applies its cap', () => {
    expect(parseInline('{"a": 1,}')).toEqual({ ok: false, message: expect.stringMatching(/^Not valid JSON: .+\.$/) })
    expect(parseInline('[1, 2]')).toEqual({ ok: false, message: NOT_AN_OBJECT })
    const pretty = '{\n  "schema_version": 1,\n  "note": "é"\n}'
    const parsed = parseInline(pretty)
    expect(parsed).toEqual({ ok: true, value: { schema_version: 1, note: 'é' } })
    expect(payloadBytes(pretty, parsed)).toBe(new TextEncoder().encode('{"schema_version":1,"note":"é"}').length)
    expect(payloadBytes('{oops', parseInline('{oops'))).toBe(5)
    expect(overCap(262_144)).toBe(false)
    expect(overCap(262_145)).toBe(true)
    expect(byteCounter(12_698)).toBe('12.4 KB of 256 KB')
  })

  it('anchors the body on the picked run and adds inline_input only when given', () => {
    expect(requestBody('p-1', 'agent.summary.v1', 'r-1')).toEqual({
      project_id: 'p-1', input: { agent_id: 'agent.summary.v1', payload: { test_run_id: 'r-1' } }, mode: 'async',
    })
    expect(requestBody('p-1', 'agent.summary.v1', 'r-1', { schema_version: 1 }).inline_input).toEqual({ schema_version: 1 })
  })

  it('reads a refusal as the server\'s sentence plus each validation error, never the status text', () => {
    const invalid = axiosRefusal(422, {
      code: 'inline_input_invalid',
      message: INLINE_ERRORS.inline_input_invalid,
      errors: [{ loc: ['failures', 0, 'confidence_score'], msg: 'Input should be less than or equal to 100', type: 'less_than_equal' }],
    })
    expect(refusalLines(invalid)).toEqual([
      INLINE_ERRORS.inline_input_invalid,
      'failures.0.confidence_score: Input should be less than or equal to 100',
    ])
    expect(refusalLines(axiosRefusal(413, { code: 'inline_input_too_large', message: INLINE_ERRORS.inline_input_too_large })))
      .toEqual([INLINE_ERRORS.inline_input_too_large])
    expect(refusalLines(axiosRefusal(404, { code: 'anchor_run_not_found', message: INLINE_ERRORS.anchor_run_not_found })))
      .toEqual([INLINE_ERRORS.anchor_run_not_found])
    expect(refusalLines(axiosRefusal(403, 'Insufficient permissions'))).toEqual(['Insufficient permissions'])
    expect(refusalLines(new Error('Network Error'))).toEqual([INVOKE_FAILED])
  })

  it('names the status, the input and the review, and links an invocation', () => {
    expect(statusWord({ status: 'in_progress' })).toBe('In progress')
    expect(statusWord({ status: 'completed' })).toBe('Completed')
    expect(inputLine(invocationInProgress())).toBe('Inline (sha256 1a2b3c4d5e6f)')
    expect(inputLine({ input_kind: undefined, input_sha256: null })).toBe('Stored run')
    expect(reviewLine(invocationAwaitingReview())).toBe(INLINE_SENTENCES.awaiting_review)
    expect(reviewLine({ requires_human_review: true, review: { state: 'accepted', message: '', reviewed_at: '2026-11-02T15:00:00Z' } }))
      .toMatch(/^Accepted by a reviewer · /)
    expect(invocationHref('r 1', 'i/2')).toBe('/agents?run=r%201&invocation=i%2F2')
  })
})

describe('TryAgentPanel', () => {
  it('reads nothing until it is opened, then the catalog ONCE (no agent detail until one is picked)', async () => {
    function Page() {
      const [open, setOpen] = useState(false)
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>Try an agent</button>
          {open && <TryAgentPanel runId={RG_RUN_ID} projectId={RG_PROJECT_ID} onClose={() => setOpen(false)} />}
        </>
      )
    }
    render(
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
        <MemoryRouter initialEntries={[`/agents?run=${RG_RUN_ID}`]}><Page /></MemoryRouter>
      </SWRConfig>,
    )
    expect(http.getData).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Try an agent' }))
    expect(await screen.findByRole('region', { name: 'Try an agent' })).toBeInTheDocument()
    await waitFor(() => expect(gets(CATALOG)).toBe(1))
    expect(http.getData).toHaveBeenCalledTimes(1)
  })

  it('no run picked: Run is disabled and the panel says why', async () => {
    renderPanel({ url: '/agents', runId: null })
    await pickSummary()
    expect(screen.getByText(NO_RUN)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run' })).toBeDisabled()
  })

  it('picking agent.summary.v1 pre-fills the editor from its schema, with the byte counter', async () => {
    renderPanel()
    const editor = await pickSummary()
    expect(editor.value).toBe(inlineTemplate(summaryDetail()))
    expect(gets(DETAIL)).toBe(1)
    expect(screen.getByRole('radio', { name: 'Inline input (JSON)' })).toBeChecked()
    expect(document.querySelector('[data-try-agent-bytes]')?.textContent).toMatch(/^[\d.]+ KB of 256 KB$/)
    expect(screen.getByText('API Regression · Run #7')).toBeInTheDocument()
  })

  it('invalid JSON: the parse line, Run disabled, no request', async () => {
    renderPanel()
    const editor = await pickSummary()
    fireEvent.change(editor, { target: { value: '{"schema_version": 1,' } })
    expect(document.querySelector('[data-try-agent-parse-error]')?.textContent).toMatch(/^Not valid JSON: /)
    const run = screen.getByRole('button', { name: 'Run' })
    expect(run).toBeDisabled()
    fireEvent.click(run)
    expect(http.postData).not.toHaveBeenCalled()
  })

  it('262 145 bytes: the cap sentence, Run disabled, no request', async () => {
    renderPanel()
    const editor = await pickSummary()
    const shell = (pad: string) => JSON.stringify({ schema_version: 1, failures: [{ test_name: 'x', confidence_score: 60, log_excerpt: pad }] })
    const text = shell('a'.repeat(262_145 - shell('').length))
    expect(new TextEncoder().encode(text).length).toBe(262_145)
    fireEvent.change(editor, { target: { value: text } })
    expect(screen.getByText(INLINE_ERRORS.inline_input_too_large)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run' })).toBeDisabled()
    expect(http.postData).not.toHaveBeenCalled()
  })

  it('Run posts inline_input anchored on the picked run with a fresh Idempotency-Key, then shows the invocation', async () => {
    http.postData.mockResolvedValue(invocationInProgress())
    invocationSequence = [invocationAwaitingReview()]
    renderPanel()
    const editor = await pickSummary()
    const typed = { schema_version: 1, failures: [{ test_name: 'checkout pays sentinel', confidence_score: 70, log_excerpt: 'token ghp_TypedSampleTokenTypedSampleToken0123' }] }
    fireEvent.change(editor, { target: { value: JSON.stringify(typed, null, 2) } })
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    await waitFor(() => expect(http.postData).toHaveBeenCalledTimes(1))
    const [url, body, config] = http.postData.mock.calls[0]
    expect(url).toBe(INVOKE)
    expect(body).toEqual({
      project_id: RG_PROJECT_ID,
      input: { agent_id: 'agent.summary.v1', payload: { test_run_id: RG_RUN_ID } },
      mode: 'async',
      inline_input: typed,
    })
    expect(config.suppressToast).toBe(true)
    expect(config.headers['Idempotency-Key']).toMatch(UUID)
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe(`/agents?run=${RG_RUN_ID}&invocation=${RG_INVOCATION_ID}`))
    expect(await screen.findByText('Completed')).toBeInTheDocument()
    // A second click is a new request: a new key.
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    await waitFor(() => expect(http.postData).toHaveBeenCalledTimes(2))
    expect(http.postData.mock.calls[1][2].headers['Idempotency-Key']).not.toBe(config.headers['Idempotency-Key'])
  })

  it('a 422 with errors renders the sentence and each loc: msg line under the editor', async () => {
    http.postData.mockRejectedValue(axiosRefusal(422, {
      code: 'inline_input_invalid',
      message: INLINE_ERRORS.inline_input_invalid,
      errors: [{ loc: ['failures', 0, 'confidence_score'], msg: 'Input should be less than or equal to 100', type: 'less_than_equal' }],
    }))
    renderPanel()
    await pickSummary()
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    const alert = await screen.findByRole('alert')
    expect(within(alert).getByText(INLINE_ERRORS.inline_input_invalid)).toBeInTheDocument()
    expect(within(alert).getByText('failures.0.confidence_score: Input should be less than or equal to 100')).toBeInTheDocument()
    expect(screen.queryByText(/Request failed/)).toBeNull()
  })

  it('?invocation= shows the detail on load: awaiting review, the queue link, the excluded line, the SERVER\'s redacted input', async () => {
    renderPanel({ url: `/agents?run=${RG_RUN_ID}&invocation=${RG_INVOCATION_ID}` })
    const detail = await waitFor(() => {
      const el = document.querySelector('[data-invocation-status="completed"]')
      expect(el).not.toBeNull()
      return el as HTMLElement
    })
    expect(within(detail).getByText('Completed')).toBeInTheDocument()
    expect(within(detail).getByText('Inline (sha256 1a2b3c4d5e6f)')).toBeInTheDocument()
    expect(within(detail).getByText(INLINE_SENTENCES.awaiting_review, { exact: false })).toBeInTheDocument()
    expect(within(detail).getByRole('link', { name: 'Open the review queue' })).toHaveAttribute('href', REPORTS_QUEUE_HREF)
    expect(within(detail).getByText(INLINE_SENTENCES.excluded)).toBeInTheDocument()
    expect(within(detail).getByRole('link', { name: 'API Regression · Run #7' })).toHaveAttribute('href', `/runs/${RG_RUN_ID}`)
    const stored = detail.querySelector('[data-invocation-stored-input]') as HTMLElement
    expect(stored.textContent).toContain('[REDACTED]')
    expect(stored.textContent).not.toContain('ghp_')
  })

  it('an output carrying a <script> renders as text', async () => {
    const sentinel = '<script>window.__pwned = 1</script><img src=x onerror="window.__pwned = 2">'
    invocationSequence = [{ ...invocationAwaitingReview(), output: { executive_summary: sentinel, notes: sentinel } }]
    renderPanel({ url: `/agents?run=${RG_RUN_ID}&invocation=${RG_INVOCATION_ID}` })
    const output = await waitFor(() => {
      const el = document.querySelector('[data-invocation-output]')
      expect(el).not.toBeNull()
      return el as HTMLElement
    })
    expect(output.textContent).toContain('<script>window.__pwned = 1</script>')
    expect(screen.getByText(sentinel)).toBeInTheDocument()
    expect(document.querySelector('[data-invocation-detail] script')).toBeNull()
    expect(document.querySelector('[data-invocation-detail] img')).toBeNull()
    expect((window as { __pwned?: number }).__pwned).toBeUndefined()
  })

  it('the detail polls while in progress and stops once completed', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    invocationSequence = [invocationInProgress(), invocationAwaitingReview()]
    renderPanel({ url: `/agents?run=${RG_RUN_ID}&invocation=${RG_INVOCATION_ID}` })
    expect(await screen.findByText('In progress')).toBeInTheDocument()
    expect(gets(INVOCATION)).toBe(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(5_100) })
    expect(await screen.findByText('Completed')).toBeInTheDocument()
    expect(gets(INVOCATION)).toBe(2)
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000) })
    expect(gets(INVOCATION)).toBe(2)
  })
})
