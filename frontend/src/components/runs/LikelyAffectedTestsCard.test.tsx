/**
 * W36 STRAT-08 — the "Likely affected tests" card: every state of the contract §3 (loading, error, unavailable
 * with the reason in words, path-only banner, none matched, the ranked list), reason lines from the fields only
 * (`co_failure_runs` null -> "no history yet", 0 stays 0), the flaky tag, at most 20 rows, the caveat footer in
 * every state. The real hook + service run; only the HTTP client is mocked.
 */
import { act, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { LikelyAffectedTest, LikelyAffectedTests } from '@/services/commitAttributionService'
import LikelyAffectedTestsCard, { ADVISORY_CAVEAT } from './LikelyAffectedTestsCard'

const get = vi.hoisted(() => vi.fn())
vi.mock('@/services/api', () => ({ api: { get } }))

const URL = '/api/v1/runs/run-1/likely-affected-tests'
const CAVEAT = 'Advisory only: ranked by path overlap and past co-failures. It never selects or skips tests.'

function row(over: Partial<LikelyAffectedTest> = {}, reason: Partial<LikelyAffectedTest['reason']> = {}): LikelyAffectedTest {
  return {
    test_fingerprint: 'fp-cart',
    test_name: 'test_cart_total',
    class_name: 'tests.checkout.test_cart',
    // The server sends the TestStatus value (`FAILED`); the card shows it lower-case.
    status_in_run: 'FAILED',
    score: 0.72,
    reason: {
      overlapping_paths: ['src/checkout/cart.py', 'src/checkout/total.py'],
      overlap_score: 1,
      co_failure_runs: 2,
      history_runs_considered: 7,
      known_flaky: false,
      ...reason,
    },
    ...over,
  }
}

function answer(over: Partial<LikelyAffectedTests> = {}): LikelyAffectedTests {
  return {
    run_id: 'run-1',
    project_id: 'p1',
    available: true,
    reason: null,
    advisory: true,
    caveat: CAVEAT,
    changed_files_count: 12,
    file_detail_coverage: 1,
    source: 'supplied',
    base_source: 'supplied',
    base_anchor_is_strong: true,
    history: {
      available: true,
      insufficient_data_reason: null,
      usable_runs: 41,
      window_days: 90,
      runs_considered: 7,
      scan_capped: false,
    },
    tests: [
      row(),
      row(
        { test_fingerprint: 'fp-pay', test_name: 'test_pay', class_name: null, status_in_run: 'PASSED', score: 0.36 },
        { overlapping_paths: ['src/checkout/pay.py'], co_failure_runs: 0, history_runs_considered: 4, known_flaky: true },
      ),
    ],
    total_candidates: 140,
    ...over,
  }
}

let body: unknown

const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })

function renderCard() {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, revalidateOnFocus: false, shouldRetryOnError: false }}>
      {children}
    </SWRConfig>
  )
  return render(<LikelyAffectedTestsCard runId="run-1" />, { wrapper })
}

const caveat = () => document.querySelector('[data-likely-caveat]') as HTMLElement
const rows = () => [...document.querySelectorAll('[data-likely-test]')] as HTMLElement[]

beforeEach(() => {
  body = answer()
  get.mockReset()
  get.mockImplementation(() => (body instanceof Error ? Promise.reject(body) : Promise.resolve({ data: body })))
})

describe('LikelyAffectedTestsCard', () => {
  it('asks the run route once (limit 20, no global toast); while it loads: a spinner and the caveat', async () => {
    get.mockImplementation(() => new Promise(() => {}))
    renderCard()
    expect(screen.getByRole('region', { name: 'Likely affected tests' })).toBeInTheDocument()
    expect(document.querySelector('[data-likely-state="loading"]')).not.toBeNull()
    expect(caveat()).toHaveTextContent(ADVISORY_CAVEAT)
    expect(get).toHaveBeenCalledTimes(1)
    expect(get).toHaveBeenCalledWith(URL, { params: { limit: 20 }, suppressToast: true })
  })

  it('the ranked list: name, status in this run, score bar with its number, matched paths and "N of M past runs"', async () => {
    renderCard()
    await settle()
    const [first, second] = rows()
    expect(rows()).toHaveLength(2)
    expect(first).toHaveTextContent('test_cart_total · tests.checkout.test_cart')
    expect(first.querySelector('[data-likely-status]')).toHaveTextContent('failed')
    expect(first.querySelector('[data-likely-score]')).toHaveTextContent('0.72')
    expect(first.querySelector('[data-likely-reason]')).toHaveTextContent(
      'Matches src/checkout/cart.py, src/checkout/total.py · failed with these files in 2 of 7 past runs',
    )
    expect(first.querySelector('[data-likely-flaky]')).toBeNull()
    // 0 co-failures with history is a measured 0, never "no history".
    expect(second.querySelector('[data-likely-reason]')).toHaveTextContent(
      'Matches src/checkout/pay.py · failed with these files in 0 of 4 past runs',
    )
    expect(second.querySelector('[data-likely-status]')).toHaveTextContent('passed')
    expect(second.querySelector('[data-likely-flaky]')).toHaveTextContent('flaky')
    expect(screen.getByText(/2 of this run's 140 tests, ranked against 12 changed files\./)).toBeInTheDocument()
    expect(document.querySelector('[data-likely-path-only]')).toBeNull()
    expect(caveat()).toHaveTextContent(CAVEAT)
  })

  it('history not ready: the path-only banner names the reason; each row says "no history yet" (null is not 0)', async () => {
    body = answer({
      history: {
        available: false,
        insufficient_data_reason: 'only 4 of 12 runs carry a usable commit range (need 30)',
        usable_runs: 4,
        window_days: 90,
        runs_considered: 0,
        scan_capped: false,
      },
      tests: [row({ score: 1 }, { co_failure_runs: null, history_runs_considered: null })],
    })
    renderCard()
    await settle()
    expect(document.querySelector('[data-likely-path-only]')).toHaveTextContent(
      'Ranked by path overlap only: past co-failures are not counted yet — only 4 of 12 runs carry a usable commit range (need 30).',
    )
    const reason = rows()[0].querySelector('[data-likely-reason]') as HTMLElement
    expect(reason).toHaveTextContent('· no history yet')
    expect(reason.textContent).not.toMatch(/\d+ of \d+ past/)
    expect(caveat()).toHaveTextContent(CAVEAT)
  })

  it('history ready but no earlier run changed these files: a measured 0 says so, never "0 of 0" or "no history"', async () => {
    body = answer({ tests: [row({ score: 0.6 }, { co_failure_runs: 0, history_runs_considered: 0 })] })
    renderCard()
    await settle()
    const reason = rows()[0].querySelector('[data-likely-reason]') as HTMLElement
    expect(reason).toHaveTextContent('· no earlier run changed these files')
    expect(reason.textContent).not.toMatch(/0 of 0|no history yet/)
  })

  it('a row matched only through history says no path matched it', async () => {
    body = answer({ tests: [row({ score: 0.24 }, { overlapping_paths: [], overlap_score: 0, co_failure_runs: 3, history_runs_considered: 5 })] })
    renderCard()
    await settle()
    expect(rows()[0].querySelector('[data-likely-reason]')).toHaveTextContent(
      'No changed path matches it · failed with these files in 3 of 5 past runs',
    )
  })

  it.each([
    ['no_commit_range', 'No commit range is known for this run, so there are no changed files to match its tests against.'],
    ['no_changed_files', "This run's commit range lists no changed files (the source sent none), so there is nothing to match its tests against."],
  ] as const)('unavailable (%s): the reason in words, no list, the caveat', async (reason, words) => {
    body = answer({ available: false, reason, tests: [], changed_files_count: 0 })
    renderCard()
    await settle()
    const line = document.querySelector('[data-likely-state="unavailable"]') as HTMLElement
    expect(line).toHaveTextContent(words)
    expect(line.getAttribute('data-likely-reason-code')).toBe(reason)
    expect(document.querySelector('[data-likely-list]')).toBeNull()
    expect(document.querySelector('[data-likely-state="none"]')).toBeNull()
    expect(caveat()).toHaveTextContent(CAVEAT)
  })

  it('available but no test scored: a measured result naming the changed files, not an empty list', async () => {
    body = answer({ tests: [], changed_files_count: 1 })
    renderCard()
    await settle()
    expect(document.querySelector('[data-likely-state="none"]')).toHaveTextContent(
      'No test in this run matched the 1 changed file.',
    )
    expect(document.querySelector('[data-likely-list]')).toBeNull()
  })

  it('at most 20 rows', async () => {
    body = answer({ tests: Array.from({ length: 25 }, (_, i) => row({ test_fingerprint: `fp-${i}`, test_name: `t${i}` })) })
    renderCard()
    await settle()
    expect(rows()).toHaveLength(20)
  })

  it('a capped history scan is said (counts are floors)', async () => {
    body = answer({
      history: { available: true, insufficient_data_reason: null, usable_runs: 900, window_days: 90, runs_considered: 1000, scan_capped: true },
    })
    renderCard()
    await settle()
    expect(document.querySelector('[data-likely-scan-capped]')).toHaveTextContent(
      'Only the newest 1,000 past runs were read, so the counts are floors.',
    )
  })

  it('a failed read: one line and the caveat, no list', async () => {
    body = Object.assign(new Error('boom'), { response: { status: 500, data: {} } })
    renderCard()
    await settle()
    expect(document.querySelector('[data-likely-state="error"]')).toHaveTextContent(
      'Could not load the likely affected tests for this run.',
    )
    expect(document.querySelector('[data-likely-list]')).toBeNull()
    expect(caveat()).toHaveTextContent(ADVISORY_CAVEAT)
  })
})
