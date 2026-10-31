/**
 * W25a HYG-04 phase 1 — the "Suite membership cutover" card through the REAL service, hooks and SWR (contract §4.4):
 * only `services/http` is mocked, so every request is asserted on its FULL path. The status line and the day list
 * from `suiteParityMocks`; the load error sentence; "Compare now" → the POST + the toast + the re-read; a 409 / 503
 * reads the server's sentence inline; "Show differences" reads the detail and lists it inline (no dialog); the
 * history polls only while a comparison runs or a queued one is awaited. REFRESH_INTERVALS.ACTIVE is 40 ms here;
 * `Date` is pinned to Fri 18 Sep 2026 12:00 UTC (timers stay real).
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { parityDetail, parityHistory, parityHistoryOf, parityRunId } from '@/test/suiteParityMocks'
import type { SuiteParityHistory } from '@/types/suiteParity'

const http = vi.hoisted(() => ({ getData: vi.fn(), postData: vi.fn() }))
vi.mock('@/services/http', () => ({ getData: http.getData, postData: http.postData }))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast }))
vi.mock('@/config/refreshIntervals', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/config/refreshIntervals')>()
  return { ...actual, REFRESH_INTERVALS: { ...actual.REFRESH_INTERVALS, ACTIVE: 40 } }
})

import SuiteParityCard from './SuiteParityCard'
import { suiteParityRefreshInterval } from '@/hooks/useSuiteParity'
import { PARITY_TEXT } from './suiteParityModel'

const HISTORY_PATH = '/api/v1/admin/maintenance/suite-membership-parity'
const NOW = () => new Date('2026-09-18T12:00:00Z')
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

/** The history answers, in order; the last one repeats. */
let histories: SuiteParityHistory[] = []
let historyError: unknown = null

function answer(url: string) {
  if (url === HISTORY_PATH) {
    if (historyError) return Promise.reject(historyError)
    return Promise.resolve(histories.length > 1 ? histories.shift() : histories[0])
  }
  const detailId = url.startsWith(`${HISTORY_PATH}/`) ? url.slice(HISTORY_PATH.length + 1) : null
  if (detailId) {
    const run = histories[0].runs.find((r) => r.id === decodeURIComponent(detailId))
    return run ? Promise.resolve(parityDetail(run)) : Promise.reject(new Error('unknown run'))
  }
  return Promise.reject(new Error(`unexpected GET ${url}`))
}

const historyCalls = () => http.getData.mock.calls.filter(([url]) => url === HISTORY_PATH).length

function renderCard() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <SuiteParityCard />
    </SWRConfig>,
  )
}

function refusal(status: number, detail: string) {
  return Object.assign(new Error(`Request failed with status code ${status}`), {
    isAxiosError: true,
    response: { status, data: { detail } },
  })
}

const compareButton = () => screen.getByRole('button', { name: /^(Compare now|Comparing…)$/ })

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW())
  http.getData.mockReset().mockImplementation(answer)
  http.postData.mockReset()
  toast.success.mockReset()
  toast.error.mockReset()
  histories = [parityHistory({ streak: 4, now: NOW() })]
  historyError = null
})

afterEach(() => {
  vi.useRealTimers()
})

describe('SuiteParityCard', () => {
  it('renders the heading, the explainer, the status line and the 7 UTC days from the history', async () => {
    renderCard()
    expect(await screen.findByText('Day 4 of 7 with no differences.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: PARITY_TEXT.heading })).toBeInTheDocument()
    expect(screen.getByText(PARITY_TEXT.explainer)).toBeInTheDocument()
    const days = within(screen.getByRole('list', { name: PARITY_TEXT.daysHeading })).getAllByRole('listitem')
    expect(days.map((li) => li.textContent)).toEqual([
      'Fri 18 SepNo differences · 338 expected',
      'Thu 17 SepNo differences · 341 expected',
      'Wed 16 SepNo differences · 339 expected',
      'Tue 15 SepNo differences · 342 expected',
      'Mon 14 Sep12 differences · 340 expected' + PARITY_TEXT.showDifferences,
      'Sun 13 SepNot run',
      'Sat 12 SepNot run',
    ])
    expect(compareButton()).toHaveTextContent('Compare now')
    expect(compareButton()).toBeEnabled()
    expect(http.getData).toHaveBeenCalledWith(HISTORY_PATH)
  })

  it('no comparison yet: the sentence, never "0 differences"', async () => {
    histories = [parityHistory({ newest: 'none', now: NOW() })]
    renderCard()
    expect(await screen.findByText(PARITY_TEXT.noRuns)).toBeInTheDocument()
    expect(screen.queryByText(/0 differences/)).toBeNull()
    expect(screen.getAllByText('Not run')).toHaveLength(6)
    expect(screen.getByText('Not run yet')).toBeInTheDocument()
  })

  it('a failed read: "Could not load the cutover comparisons." in the card', async () => {
    historyError = new Error('Network Error')
    renderCard()
    expect(await screen.findByText(PARITY_TEXT.loadError)).toBeInTheDocument()
    expect(compareButton()).toBeDisabled()
  })
})

describe('"Compare now"', () => {
  it('POSTs to the full path (toast suppressed: the card owns the words), toasts "Comparison queued." and re-reads', async () => {
    http.postData.mockResolvedValue({ queued: true, task_id: 'task-one', task_name: 'run_suite_membership_parity' })
    renderCard()
    await screen.findByText('Day 4 of 7 with no differences.')
    const before = historyCalls()
    fireEvent.click(compareButton())
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Comparison queued.'))
    expect(http.postData).toHaveBeenCalledTimes(1)
    expect(http.postData).toHaveBeenCalledWith(HISTORY_PATH, {}, { suppressToast: true })
    await waitFor(() => expect(historyCalls()).toBeGreaterThan(before))
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('a 409 reads "A comparison is already running." inline — never "Request failed with status code 409"', async () => {
    http.postData.mockRejectedValue(refusal(409, 'A comparison is already running.'))
    renderCard()
    await screen.findByText('Day 4 of 7 with no differences.')
    fireEvent.click(compareButton())
    expect(await screen.findByRole('alert')).toHaveTextContent('A comparison is already running.')
    expect(screen.queryByText(/Request failed/)).toBeNull()
    expect(toast.success).not.toHaveBeenCalled()
  })

  it("a 503 reads the server's sentence inline", async () => {
    http.postData.mockRejectedValue(refusal(503, 'Could not queue the comparison.'))
    renderCard()
    await screen.findByText('Day 4 of 7 with no differences.')
    fireEvent.click(compareButton())
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not queue the comparison.')
    expect(screen.queryByText(/Request failed/)).toBeNull()
  })

  it('polls until the queued comparison appears and finishes, then stops', async () => {
    const before = parityHistory({ streak: 4, now: NOW() })
    const runningRow = parityHistoryOf([{ daysAgo: 0, status: 'running', hour: 11, minute: 58 }], { now: NOW() }).runs[0]
    const cleanRow = { ...runningRow, status: 'clean', finished_at: NOW().toISOString(), expected_divergences: 336 }
    histories = [before]
    http.postData.mockResolvedValue({ queued: true, task_id: 'task-two', task_name: 'run_suite_membership_parity' })
    renderCard()
    await screen.findByText('Day 4 of 7 with no differences.')
    // Not claimed yet (the same newest run, twice), then running, then finished: the card keeps reading.
    histories = [before, before, { ...before, runs: [runningRow, ...before.runs] }, { ...before, runs: [cleanRow, ...before.runs] }]
    fireEvent.click(compareButton())
    expect(await screen.findByText('Comparing… (started 11:58)', {}, { timeout: 3000 })).toBeInTheDocument()
    expect(compareButton()).toBeDisabled()
    await waitFor(() => expect(screen.getAllByText('No differences · 336 expected')).toHaveLength(1), { timeout: 3000 })
    const calls = historyCalls()
    await pause(250)                                   // > 6 poll intervals: nothing more is read
    expect(historyCalls()).toBe(calls)
  })
})

describe('the history polls only while a comparison runs or is awaited', () => {
  it('suiteParityRefreshInterval is a number: ACTIVE while running / awaited, 0 otherwise', () => {
    const clean = parityHistory({ streak: 2, now: NOW() })
    expect(suiteParityRefreshInterval(parityHistory({ streak: 2, newest: 'running', now: NOW() }))).toBe(40)
    expect(suiteParityRefreshInterval(clean)).toBe(0)
    expect(suiteParityRefreshInterval(undefined)).toBe(0)
    expect(suiteParityRefreshInterval(clean, clean.runs[0].id), 'the queued run is not the newest yet').toBe(40)
    expect(suiteParityRefreshInterval(clean, 'an-older-run'), 'a newer run appeared').toBe(0)
    expect(suiteParityRefreshInterval(parityHistory({ newest: 'none', now: NOW() }), ''), 'none yet → awaited').toBe(40)
  })

  it('a running comparison: "Comparing…", the button disabled, re-read until it finishes, then quiet', async () => {
    const running = parityHistory({ streak: 2, newest: 'running', now: NOW() })
    histories = [running, running, parityHistory({ streak: 3, now: NOW() })]
    renderCard()
    expect(await screen.findByText('Comparing… (started 06:15)')).toBeInTheDocument()
    expect(compareButton()).toHaveTextContent('Comparing…')
    expect(compareButton()).toBeDisabled()
    expect(await screen.findByText('Day 3 of 7 with no differences.', {}, { timeout: 3000 })).toBeInTheDocument()
    const calls = historyCalls()
    expect(calls).toBeGreaterThanOrEqual(3)
    await pause(250)
    expect(historyCalls()).toBe(calls)
  })
})

describe('"Show differences"', () => {
  it('reads the detail on its full path and lists the rows INLINE under the day; the same button hides them', async () => {
    renderCard()
    fireEvent.click(await screen.findByRole('button', { name: PARITY_TEXT.showDifferences }))
    const sample = await screen.findByRole('table')
    expect(http.getData).toHaveBeenCalledWith(`${HISTORY_PATH}/${parityRunId(4)}`)
    expect(within(sample).getAllByRole('row')).toHaveLength(13)          // the header + 12 differences
    expect(within(sample).getAllByText('Missing from the catalog')).toHaveLength(10)
    expect(screen.getByText('By reason: Missing from the catalog 10 · Name differs 2.')).toBeInTheDocument()
    expect(screen.getByText('Expected, not counted: Absent from one run only 300 · Moved to another suite 40.')).toBeInTheDocument()
    expect(sample.closest('[data-parity-day]')).toHaveAttribute('data-parity-day', '2026-09-14')
    expect(document.querySelector('[role="dialog"]')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: PARITY_TEXT.hideDifferences }))
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('names are text, never markup', async () => {
    const hostile = '<img src=x onerror=alert(1)>'
    http.getData.mockImplementation((url: string) =>
      url === HISTORY_PATH
        ? Promise.resolve(histories[0])
        : answer(url).then((d) => {
            const detail = d as ReturnType<typeof parityDetail>
            return { ...detail, sample: detail.sample.map((row) => ({ ...row, suite: hostile, test_name: hostile })) }
          }),
    )
    renderCard()
    fireEvent.click(await screen.findByRole('button', { name: PARITY_TEXT.showDifferences }))
    await screen.findByRole('table')
    expect(screen.getAllByText(hostile).length).toBeGreaterThan(0)
    expect(document.querySelector('img')).toBeNull()
  })
})
