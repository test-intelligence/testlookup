/**
 * W23c AGT-10: Settings › AI › Review gate, and its pure model (`components/reviewGate/reviewGateModel.ts`).
 *
 * The REAL hook (`useReviewGate`) and service run in an isolated SWRConfig; only the HTTP helpers
 * (`services/http`) are mocked — the unit-test network guard sees no request, and every assertion on a request
 * is on the path and body the browser would send. Unit tests run in America/Chicago (vitest.config.ts), so an
 * offset is asserted as the browser's offset AT that instant (CST -06:00 in November, CDT -05:00 in July).
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ADMIN_ONLY,
  LOAD_ERROR,
  channelRows,
  formatInstant,
  isPast,
  measuredSinceLine,
  stateLines,
  toLocalInput,
  toOffsetIso,
} from '@/components/reviewGate/reviewGateModel'
import {
  RG_CHANNELS,
  gateDate,
  gateEnv,
  gateNotMeasured,
  gateScheduled,
  gateUnset,
  reviewGate,
  wouldRefuseMeasured,
} from '@/test/reviewGateInlineMocks'
import { GATE_SENTENCES, REVIEW_GATE_ERRORS, type ReviewGateRead } from '@/types/reviewGate'
import ReviewGatePage from './ReviewGatePage'

const { http, perms } = vi.hoisted(() => ({
  http: { getData: vi.fn(), putData: vi.fn() },
  perms: { isAdmin: true },
}))

vi.mock('@/services/http', () => ({
  getData: (...a: unknown[]) => http.getData(...a),
  putData: (...a: unknown[]) => http.putData(...a),
}))

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ isAdmin: perms.isAdmin, canAccessManagement: true, isQaLead: true }),
}))

const URL = '/api/v1/settings/review-gate'
const OFFSET_ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$/

let answer: ReviewGateRead = gateUnset()

function renderPage() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter>
        <ReviewGatePage />
      </MemoryRouter>
    </SWRConfig>,
  )
}

const card = (name: string) => document.querySelector(`[data-review-gate-${name}]`) as HTMLElement
const days = (n: number) => new Date(Date.now() + n * 86_400_000)

/** A refusal shaped as axios rejects it. */
function refusal(status: number, detail: unknown) {
  return Object.assign(new Error(`Request failed with status code ${status}`), {
    isAxiosError: true,
    response: { status, data: { detail } },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  perms.isAdmin = true
  answer = gateUnset()
  http.getData.mockImplementation((url: string) =>
    url === URL ? Promise.resolve(answer) : Promise.reject(new Error(`unexpected GET ${url}`)),
  )
})

describe('reviewGateModel', () => {
  it('fills each source\'s sentence verbatim with the server\'s numbers', () => {
    expect(stateLines(gateUnset())).toEqual([
      "Audit only — nothing is withheld. 12 runs' AI reports would have been withheld in the last 30 days (42 distribution attempts).",
    ])
    const scheduled = gateScheduled()
    expect(stateLines(scheduled)).toEqual([
      `Audit only until ${formatInstant(scheduled.enforced_from)} — then unreviewed AI reports are withheld. 12 runs' AI reports would have been withheld in the last 30 days (42 distribution attempts).`,
    ])
    const enforced = gateDate()
    expect(stateLines(enforced)).toEqual([
      `Enforced since ${formatInstant(enforced.enforced_from)}: unreviewed AI reports are withheld on the channels below.`,
    ])
    expect(stateLines(gateEnv())).toEqual([GATE_SENTENCES.env])
  })

  it('reads one run and one attempt in the singular (W23c demo: "1 runs\' AI reports")', () => {
    const one = { ...gateUnset(), would_refuse: wouldRefuseMeasured({ runs: 1, decisions: 1 }) }
    expect(stateLines(one)).toEqual([
      "Audit only — nothing is withheld. 1 run's AI reports would have been withheld in the last 30 days (1 distribution attempt).",
    ])
    const mixed = { ...gateUnset(), would_refuse: wouldRefuseMeasured({ runs: 1, decisions: 3 }) }
    expect(stateLines(mixed)).toEqual([
      "Audit only — nothing is withheld. 1 run's AI reports would have been withheld in the last 30 days (3 distribution attempts).",
    ])
  })

  it('says "Not measured" with no digit when the audit log could not be read', () => {
    const lines = stateLines(gateNotMeasured())
    expect(lines).toEqual(['Audit only — nothing is withheld.', GATE_SENTENCES.not_measured])
    expect(lines.join(' ')).not.toMatch(/\d/)
    expect(channelRows(gateNotMeasured())).toEqual([])
  })

  it('lists every counted channel in the server\'s order, labelled from the answer, zero rows omitted', () => {
    const gate = reviewGate({
      would_refuse: wouldRefuseMeasured({
        by_channel: [
          { channel: 'report_pdf', decisions: 20 },
          { channel: 'gitlab_mr_note', decisions: 3 },
          { channel: 'some_future_channel', decisions: 2 },
          { channel: 'share_link_pdf', decisions: 0 },
        ],
      }),
    })
    expect(channelRows(gate)).toEqual([
      { channel: 'report_pdf', label: 'PDF export of a run report', decisions: 20 },
      { channel: 'gitlab_mr_note', label: 'AI failure-kind labels in GitLab MR notes', decisions: 3 },
      { channel: 'some_future_channel', label: 'some_future_channel', decisions: 2 },
    ])
  })

  it('sends a local time as ISO 8601 WITH the browser\'s offset at that instant', () => {
    expect(toOffsetIso('2026-11-02T09:00')).toBe('2026-11-02T09:00:00-06:00')
    expect(toOffsetIso('2026-07-01T09:30')).toBe('2026-07-01T09:30:00-05:00')
    expect(toOffsetIso('')).toBeNull()
    expect(toOffsetIso('2026-02-30T09:00')).toBeNull()
    expect(toLocalInput('2026-11-02T15:00:00Z')).toBe('2026-11-02T09:00')
    expect(formatInstant('2026-11-02T15:00:00Z')).toBe('Nov 2, 2026, 09:00 (UTC-06:00)')
  })

  it('a past or now instant is past; a future one is not', () => {
    const now = new Date('2026-11-02T15:00:00Z') // 09:00 in Chicago
    expect(isPast('2026-11-02T08:59', now)).toBe(true)
    expect(isPast('2026-11-02T09:00', now)).toBe(true)
    expect(isPast('2026-11-02T09:01', now)).toBe(false)
    expect(isPast('not a date', now)).toBe(false)
  })

  it('says when the audit trail is shorter than 30 days, and nothing for a full window', () => {
    const now = new Date()
    const short = reviewGate({ would_refuse: wouldRefuseMeasured({ since: days(-12).toISOString() }) })
    expect(measuredSinceLine(short, now)).toMatch(/^Measured since .+ — the audit log holds less than 30 days\.$/)
    expect(measuredSinceLine(gateUnset(), now)).toBeNull()
    expect(measuredSinceLine(gateNotMeasured(), now)).toBeNull()
  })
})

describe('ReviewGatePage', () => {
  it('an admin: ONE read, the audit-only state with the counts, every counted channel and every gated channel', async () => {
    renderPage()
    expect(await screen.findByText(stateLines(gateUnset())[0])).toBeInTheDocument()
    expect(within(card('state')).getByText('Audit only')).toBeInTheDocument()
    const byChannel = card('state').querySelector('[data-review-gate-by-channel]') as HTMLElement
    expect(Array.from(byChannel.querySelectorAll('[data-channel]'), (el) => el.textContent)).toEqual([
      'PDF export of a run report20',
      'AI failure-kind labels in GitHub PR comments14',
      'AI summary e-mail and event-driven digests8',
    ])
    const gated = Array.from(card('channels').querySelectorAll('li'), (li) => li.textContent)
    expect(gated).toEqual(RG_CHANNELS.map((c) => c.label))
    expect(within(card('channels')).getByText('Takes effect on every server within 30 seconds.')).toBeInTheDocument()
    expect(http.getData).toHaveBeenCalledTimes(1)
    expect(http.getData.mock.calls[0][0]).toBe(URL)
    expect(http.putData).not.toHaveBeenCalled()
  })

  it('not measured: the sentence and no digit anywhere in the state card', async () => {
    answer = gateNotMeasured()
    renderPage()
    expect(await screen.findByText(GATE_SENTENCES.not_measured)).toBeInTheDocument()
    expect(card('state').textContent).not.toMatch(/\d/)
  })

  it('a future time saves at once with an offset and reads "Audit only until …"', async () => {
    renderPage()
    const input = await screen.findByLabelText('Enforce from')
    const local = toLocalInput(days(5).toISOString())
    fireEvent.change(input, { target: { value: local } })
    const saved = gateScheduled()
    http.putData.mockResolvedValue(saved)
    fireEvent.click(screen.getByRole('button', { name: 'Set date' }))
    await waitFor(() => expect(http.putData).toHaveBeenCalledTimes(1))
    const [url, body, config] = http.putData.mock.calls[0]
    expect(url).toBe(URL)
    expect(body).toEqual({ enforced_from: toOffsetIso(local) })
    expect(body.enforced_from).toMatch(OFFSET_ISO)
    expect(config).toEqual({ suppressToast: true })
    expect(document.querySelector('[data-inline-confirm]')).toBeNull()
    expect(await screen.findByText(stateLines(saved)[0])).toBeInTheDocument()
  })

  it('a past time asks inline first; Enforce now sends it with an offset and reads "Enforced since …"', async () => {
    renderPage()
    const input = await screen.findByLabelText('Enforce from')
    const local = toLocalInput(days(-1).toISOString())
    fireEvent.change(input, { target: { value: local } })
    fireEvent.click(screen.getByRole('button', { name: 'Set date' }))
    const confirm = await screen.findByTestId('review-gate-past-date')
    expect(within(confirm).getByText(GATE_SENTENCES.past_date_warning)).toBeInTheDocument()
    expect(http.putData).not.toHaveBeenCalled()
    const enforced = gateDate()
    http.putData.mockResolvedValue(enforced)
    fireEvent.click(within(confirm).getByRole('button', { name: 'Enforce now' }))
    await waitFor(() => expect(http.putData).toHaveBeenCalledWith(URL, { enforced_from: toOffsetIso(local) }, { suppressToast: true }))
    expect(await screen.findByText(stateLines(enforced)[0])).toBeInTheDocument()
    expect(within(card('state')).getByText('Enforced')).toBeInTheDocument()
    expect(screen.queryByTestId('review-gate-past-date')).toBeNull()
  })

  it('Clear date sends null (back to audit only)', async () => {
    answer = gateScheduled()
    renderPage()
    http.putData.mockResolvedValue(gateUnset())
    fireEvent.click(await screen.findByRole('button', { name: 'Clear date' }))
    await waitFor(() => expect(http.putData).toHaveBeenCalledWith(URL, { enforced_from: null }, { suppressToast: true }))
    expect(await screen.findByText(stateLines(gateUnset())[0])).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Clear date' })).toBeNull()
  })

  it('a refusal reads as the server\'s sentence, under the field', async () => {
    renderPage()
    fireEvent.change(await screen.findByLabelText('Enforce from'), { target: { value: toLocalInput(days(3).toISOString()) } })
    http.putData.mockRejectedValue(refusal(403, { code: 'interactive_only', message: REVIEW_GATE_ERRORS.interactive_only }))
    fireEvent.click(screen.getByRole('button', { name: 'Set date' }))
    const alert = await screen.findByText(REVIEW_GATE_ERRORS.interactive_only)
    expect(card('date')).toContainElement(alert)
    expect(screen.queryByText(/Request failed/)).toBeNull()
  })

  it('the env override leads the date card with its sentence', async () => {
    answer = gateEnv()
    renderPage()
    await screen.findByLabelText('Enforce from')
    expect(within(card('date')).getByText(GATE_SENTENCES.env)).toBeInTheDocument()
  })

  it('a non-admin is told who can change it, sees no form and reads nothing', () => {
    perms.isAdmin = false
    renderPage()
    expect(screen.getByRole('heading', { level: 1, name: 'Review gate' })).toBeInTheDocument()
    expect(screen.getByText(ADMIN_ONLY)).toBeInTheDocument()
    expect(screen.queryByLabelText('Enforce from')).toBeNull()
    expect(http.getData).not.toHaveBeenCalled()
  })

  it('a failed read says so, with Retry', async () => {
    http.getData.mockRejectedValue(new Error('Network Error'))
    renderPage()
    expect(await screen.findByText(LOAD_ERROR)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})
