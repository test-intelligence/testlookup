/**
 * W25a HYG-04 phase 1 — the "Suite membership cutover" card through the REAL service, hooks and SWR (contract §4.4):
 * only `services/http` is mocked, so every request is asserted on its FULL path. The status line and the day list
 * from `suiteParityMocks`; the load error sentence; "Compare now" → the POST + the toast + the re-read; a 409 / 503
 * reads the server's sentence inline; "Show differences" reads the detail and lists it inline (no dialog); the
 * history polls only while a comparison runs or a queued one is awaited. REFRESH_INTERVALS.ACTIVE is 40 ms here;
 * `Date` is pinned to Fri 18 Sep 2026 12:00 UTC (timers stay real).
 *
 * W25d HYG-04 (contract §4.4): "Cut over now" only when the history says ready with legacy writes on; the
 * confirmation opens IN the card and Cancel closes it; "Cut over" posts ONCE on its full path, toasts and re-reads;
 * a 409 renders the server's sentence in `data-parity-refusal`; cut over / not written hide both buttons.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  parityCutOverHistory,
  parityDetail,
  parityHistory,
  parityHistoryOf,
  parityLegacyOn,
  parityNotWrittenHistory,
  parityReadyHistory,
  parityRunId,
} from '@/test/suiteParityMocks'
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

// ── W25d HYG-04: the cutover ─────────────────────────────────────────────────

const CUTOVER_PATH = '/api/v1/admin/maintenance/suite-membership-cutover'
const cutOverNowButton = () => screen.queryByRole('button', { name: 'Cut over now' })
const card = () => document.querySelector('[data-suite-parity]') as HTMLElement

describe('W25d: "Cut over now" only when ready', () => {
  it('before the cutover (legacy writes on, day 4) and a W25a server that says ready: no "Cut over now"', async () => {
    histories = [parityLegacyOn(parityHistory({ streak: 4, now: NOW() }))]
    const first = renderCard()
    expect(await screen.findByText('Day 4 of 7 with no differences.')).toBeInTheDocument()
    expect(cutOverNowButton()).toBeNull()
    expect(compareButton()).toBeEnabled()
    expect(card()).not.toHaveAttribute('data-parity-cutover')
    first.unmount()

    histories = [parityHistory({ streak: 7, now: NOW() })]   // no `cutover`: the server has no cutover route
    renderCard()
    expect(await screen.findByText('Ready for the cutover: 7 clean days in a row.')).toBeInTheDocument()
    expect(cutOverNowButton()).toBeNull()
  })

  it('ready: "Cut over now" beside "Compare now"; it opens the confirmation IN the card, Cancel closes it, nothing is posted', async () => {
    histories = [parityReadyHistory({ now: NOW() })]
    renderCard()
    expect(await screen.findByText('Ready for the cutover: 7 clean days in a row.')).toBeInTheDocument()
    expect(card()).toHaveAttribute('data-parity-cutover', 'ready')
    expect(compareButton()).toBeEnabled()
    expect(cutOverNowButton()).toBeEnabled()
    expect(screen.queryByText(PARITY_TEXT.confirmQuestion)).toBeNull()

    fireEvent.click(cutOverNowButton() as HTMLElement)
    const confirm = card().querySelector('[data-parity-confirm]') as HTMLElement
    expect(confirm).not.toBeNull()
    expect(within(confirm).getByText('Stop writing the old suite-membership tables and read the test catalog from now on?')).toBeInTheDocument()
    expect(within(confirm).getByText('Reversible until the old tables are dropped in the next release.')).toBeInTheDocument()
    expect(within(confirm).getByRole('button', { name: 'Cut over' })).toBeEnabled()
    expect(document.querySelector('[role="dialog"]')).toBeNull()

    fireEvent.click(within(confirm).getByRole('button', { name: 'Cancel' }))
    expect(card().querySelector('[data-parity-confirm]')).toBeNull()
    expect(http.postData).not.toHaveBeenCalled()
  })
})

describe('W25d: "Cut over"', () => {
  it('posts ONCE to the full path (a double click included), toasts "Cut over." and re-reads into the cut-over state', async () => {
    histories = [parityReadyHistory({ now: NOW() }), parityCutOverHistory({ now: NOW() })]
    let release: (value: unknown) => void = () => {}
    http.postData.mockImplementation(() => new Promise((resolve) => { release = resolve }))
    renderCard()
    fireEvent.click((await screen.findByRole('button', { name: 'Cut over now' })))
    const confirmButton = screen.getByRole('button', { name: 'Cut over' })
    // Two clicks in ONE batch: the second lands before React re-renders the button disabled.
    act(() => {
      confirmButton.click()
      confirmButton.click()
    })
    await waitFor(() => expect(confirmButton).toBeDisabled())
    fireEvent.click(confirmButton)
    expect(http.postData).toHaveBeenCalledTimes(1)
    expect(http.postData).toHaveBeenCalledWith(CUTOVER_PATH, {}, { suppressToast: true })

    release({ cut_over_at: '2026-09-18T10:12:00Z', streak_days: 7 })
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Cut over.'))
    expect(await screen.findByText('Cut over on Fri 18 Sep after 7 clean days.')).toBeInTheDocument()
    expect(http.postData).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it("a 409 renders the server's sentence in the refusal line — never 'Request failed with status code 409'", async () => {
    histories = [parityReadyHistory({ now: NOW() })]
    http.postData.mockRejectedValue(refusal(409, 'Not ready: day 3 of 7 with no differences.'))
    renderCard()
    fireEvent.click(await screen.findByRole('button', { name: 'Cut over now' }))
    const before = historyCalls()
    fireEvent.click(screen.getByRole('button', { name: 'Cut over' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Not ready: day 3 of 7 with no differences.')
    expect(alert).toHaveAttribute('data-parity-refusal')
    expect(screen.queryByText(/Request failed/)).toBeNull()
    expect(toast.success).not.toHaveBeenCalled()
    expect(card().querySelector('[data-parity-confirm]')).toBeNull()
    await waitFor(() => expect(historyCalls()).toBeGreaterThan(before))
  })
})

describe('W25d: after the cutover', () => {
  it('cut over: both buttons hidden; the sentence, the detail, who (text, truncated with its title); the 7 dates ending at the cut-over date', async () => {
    const hostile = '<img src=x onerror=alert(1)>'
    histories = [parityCutOverHistory({ now: NOW(), daysAgo: 3, byName: hostile })]
    renderCard()
    expect(await screen.findByText('Cut over on Tue 15 Sep after 7 clean days.')).toBeInTheDocument()
    expect(card()).toHaveAttribute('data-parity-status', 'cut_over')
    expect(screen.queryAllByRole('button')).toHaveLength(0)
    expect(screen.getByText(PARITY_TEXT.cutOverDetail)).toBeInTheDocument()
    const by = card().querySelector('[data-parity-cut-over-by]') as HTMLElement
    expect(by).toHaveTextContent(`Cut over by ${hostile}.`)
    expect(by).toHaveAttribute('title', `Cut over by ${hostile}.`)
    expect(by).toHaveClass('truncate')
    expect(document.querySelector('img')).toBeNull()
    expect(screen.getByText('The 7 days before the cutover (UTC)')).toBeInTheDocument()
    const days = within(screen.getByRole('list', { name: 'The 7 days before the cutover (UTC)' })).getAllByRole('listitem')
    expect(days.map((li) => li.querySelector('span')?.textContent)).toEqual(['Tue 15 Sep', 'Mon 14 Sep', 'Sun 13 Sep', 'Sat 12 Sep', 'Fri 11 Sep', 'Thu 10 Sep', 'Wed 9 Sep'])
    expect(days.every((li) => li.getAttribute('data-day-kind') === 'clean')).toBe(true)
    expect(screen.queryByText('Not run')).toBeNull()
    expect(screen.queryByText('Not run yet')).toBeNull()
  })

  it('not written: the loud warning line, no button, the comparisons as they are', async () => {
    histories = [parityNotWrittenHistory({ now: NOW() })]
    renderCard()
    expect(await screen.findByText('The old suite-membership tables are not written.')).toBeInTheDocument()
    expect(card()).toHaveAttribute('data-parity-status', 'not_written')
    expect(screen.getByText('The cut-over switch suite_membership_legacy_writes is off or missing; the comparison is retired.')).toBeInTheDocument()
    expect(screen.queryAllByRole('button', { name: /Compare now|Cut over/ })).toHaveLength(0)
    expect(screen.queryByText(/Cut over on/)).toBeNull()
    expect(within(screen.getByRole('list', { name: PARITY_TEXT.daysHeading })).getAllByRole('listitem')).toHaveLength(7)
  })
})
