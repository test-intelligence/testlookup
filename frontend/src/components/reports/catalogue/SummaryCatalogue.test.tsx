/**
 * The Summary report's catalogue sections (Wave 2.6, VIZ-408, plan 2.2
 * "Summary report").
 *
 * What this pins:
 *   - the donut and the suite bars read the page's own `/reports/summary`
 *     payload: its population (per unique test), its mode, every status
 *     (`broken` included), and no invented `unknown`;
 *   - suites are ordered worst first (failed + broken);
 *   - `part` renders exactly its sections (`headline` = all three, as before
 *     the split); a part without the trend asks for nothing;
 *   - the trend is the one new request, and its caption names its basis
 *     (executions), because every other number on the page counts unique tests;
 *   - each section root carries its `data-catalogue-section`, each frame its
 *     title as its heading.
 */
import { fireEvent, render, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChartState } from '@/components/charts/chartState'
import type { TrendResponse } from '@/types/metrics'
import type { SummaryReport } from '@/types/summaryReport'
import { utcDayIso } from '@/utils/calendarDay'

const trends = vi.hoisted(() => ({
  calls: [] as { days: number; releaseScope: unknown }[],
  state: { status: 'loading' } as ChartState<TrendResponse>,
}))
vi.mock('@/components/reports/catalogue/useTrendsSeries', () => ({
  useTrendsSeries: (days: number, releaseScope: unknown) => {
    trends.calls.push({ days, releaseScope })
    return trends.state
  },
}))

const releasesState = vi.hoisted(() => ({ items: [] as unknown[] }))
vi.mock('@/hooks/useReleases', () => ({ useReleases: () => ({ data: { items: releasesState.items } }) }))

const scope = vi.hoisted(() => ({ release: null as string | null }))
vi.mock('@/hooks/useReleaseScope', () => ({ useReleaseScope: () => scope.release }))

import SummaryCatalogue from './SummaryCatalogue'

function makeReport(overrides: Partial<SummaryReport> = {}): SummaryReport {
  return {
    project_id: 'p1',
    project_name: 'GoogleProject',
    mode: 'latest',
    window_days: 30,
    generated_at: '2026-05-16T00:00:00+00:00',
    period_start: '2026-04-16T00:00:00+00:00',
    period_end: '2026-05-16T00:00:00+00:00',
    totals: {
      total_test_cases: 200,
      passed: 180,
      failed: 15,
      skipped: 3,
      broken: 2,
      evaluated: 197,
      pass_rate_pct: 90.0,
      pass_rate_basis_label: 'per unique test',
      fail_rate_pct: 7.5,
      skip_rate_pct: 1.5,
      broken_rate_pct: 1.0,
      weighted_pass_rate_pct: 91.4,
    },
    run_count: 4,
    runs_per_day: null,
    avg_duration_ms: 12_345,
    latest_run_at: '2026-05-15T00:00:00+00:00',
    flaky_test_count: 3,
    flaky_rate_pct: 1.5,
    suites: [
      suite('auth-api', 78, 2, 0, 0),
      suite('checkout-api', 102, 13, 3, 2),
      suite('search', 50, 1, 0, 4),
    ],
    top_failing_tests: [
      { suite_name: 'checkout-api', class_name: 'CheckoutTests', test_name: 'test_pay', failures: 8 },
      { suite_name: 'auth-api', class_name: 'LoginTests', test_name: 'test_login', failures: 5 },
      { suite_name: 'search', class_name: 'LoginTests', test_name: 'test_login', failures: 3 },
    ],
    ...overrides,
  }
}

function suite(name: string, passed: number, failed: number, skipped: number, broken: number) {
  const total = passed + failed + skipped + broken
  return {
    suite_name: name,
    total,
    passed,
    failed,
    skipped,
    broken,
    pass_rate_pct: (passed / total) * 100,
    weighted_pass_rate_pct: (passed / total) * 100,
    last_run_at: '2026-05-15T00:00:00+00:00',
  }
}

const section = (id: string) => document.querySelector(`[data-catalogue-section="${id}"]`) as HTMLElement

/** A frame's data table, opened: first column -> the rest of the row. */
function tableRows(frame: HTMLElement): [string, string[]][] {
  fireEvent.click(within(frame).getByRole('button', { name: 'View as table' }))
  const table = within(frame).getByRole('table', { name: /data table/i })
  return within(table)
    .getAllByRole('rowheader')
    .map((header) => [
      header.textContent ?? '',
      Array.from((header.parentElement as HTMLElement).querySelectorAll('td'), (td) => td.textContent ?? ''),
    ])
}

beforeEach(() => {
  trends.calls = []
  trends.state = { status: 'loading' }
  releasesState.items = []
  scope.release = null
})

describe('SummaryCatalogue — the DOM contract B0 relies on', () => {
  it('headline: the donut, the suite bars and the trend, each marked, each titled', () => {
    render(<SummaryCatalogue part="headline" report={makeReport()} days={30} mode="latest" />)
    const expected: [string, string][] = [
      ['summary-donut', 'Status breakdown'],
      ['summary-suites', 'Results by suite'],
      ['summary-trend', 'Pass rate trend'],
    ]
    for (const [id, title] of expected) {
      expect(within(section(id)).getByRole('heading', { level: 2, name: title })).toBeInTheDocument()
    }
    expect(section('summary-top-failing')).toBeNull()
  })
})

/** Every section id in the DOM, in order. */
const sectionIds = () =>
  Array.from(document.querySelectorAll('[data-catalogue-section]'), (el) => el.getAttribute('data-catalogue-section'))

describe('SummaryCatalogue — `part` picks which sections render (UX redesign P3)', () => {
  it('`headline` is unchanged: the donut, the suite bars, the trend, in that order, and the trend asks', () => {
    render(<SummaryCatalogue part="headline" report={makeReport()} days={30} mode="latest" />)
    expect(sectionIds()).toEqual(['summary-donut', 'summary-suites', 'summary-trend'])
    expect(trends.calls.length).toBeGreaterThan(0)
  })

  it('`suites`: the suite bars only, and no trend request', () => {
    render(<SummaryCatalogue part="suites" report={makeReport()} days={30} mode="latest" />)
    expect(sectionIds()).toEqual(['summary-suites'])
    expect(within(section('summary-suites')).getByRole('heading', { level: 2, name: 'Results by suite' })).toBeInTheDocument()
    expect(tableRows(section('summary-suites')).map(([name]) => name)).toEqual(['checkout-api', 'search', 'auth-api'])
    expect(trends.calls).toEqual([])
  })

  it('`status`: the donut only, with the report’s totals, and no trend request', () => {
    render(<SummaryCatalogue part="status" report={makeReport()} days={30} mode="latest" />)
    expect(sectionIds()).toEqual(['summary-donut'])
    expect(new Map(tableRows(section('summary-donut'))).get('Failed')?.[0]).toBe('15')
    expect(trends.calls).toEqual([])
  })

  it('`trend`: the trend only, asking with the page’s window', () => {
    render(<SummaryCatalogue part="trend" report={makeReport()} days={7} mode="latest" />)
    expect(sectionIds()).toEqual(['summary-trend'])
    expect(within(section('summary-trend')).getByRole('heading', { level: 2, name: 'Pass rate trend' })).toBeInTheDocument()
    expect(trends.calls[trends.calls.length - 1]).toEqual({ days: 7, releaseScope: null })
  })

  it('`trend` far from the reader: its lazy placeholder, and nothing asked', () => {
    class FarAway {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal('IntersectionObserver', FarAway)
    try {
      render(<SummaryCatalogue part="trend" report={makeReport()} days={30} mode="latest" />)
      expect(sectionIds()).toEqual([])
      expect(document.querySelector('[data-lazy-section="summary-trend"]')).not.toBeNull()
      expect(trends.calls).toEqual([])
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('no part draws the failures-by-test bars any more (deleted with their last caller)', () => {
    for (const part of ['headline', 'suites', 'status', 'trend'] as const) {
      const { unmount } = render(<SummaryCatalogue part={part} report={makeReport()} days={30} mode="latest" />)
      expect(section('summary-top-failing')).toBeNull()
      expect(document.body.textContent).not.toMatch(/Failures by test/)
      unmount()
    }
  })
})

describe('SummaryCatalogue — the status donut is the report’s own totals', () => {
  it('counts every status the report counts, broken included', () => {
    render(<SummaryCatalogue part="headline" report={makeReport()} days={30} mode="latest" />)
    const rows = new Map(tableRows(section('summary-donut')))
    expect(rows.get('Passed')?.[0]).toBe('180')
    expect(rows.get('Failed')?.[0]).toBe('15')
    expect(rows.get('Broken')?.[0]).toBe('2')
    expect(rows.get('Skipped')?.[0]).toBe('3')
    expect(section('summary-donut').querySelector('[data-donut-table-total]')?.textContent).toBe('Total 200 tests')
  })

  it('invents no `unknown` (the report does not count it) and says so', () => {
    render(<SummaryCatalogue part="headline" report={makeReport()} days={30} mode="latest" />)
    const frame = section('summary-donut')
    expect(new Map(tableRows(frame)).get('Unknown')).toBeUndefined()
    expect(frame.textContent).toMatch(/does not count an unknown status/)
  })

  it('names its population and the Aggregation toggle it follows', () => {
    const { rerender } = render(<SummaryCatalogue part="headline" report={makeReport()} days={30} mode="latest" />)
    expect(section('summary-donut').textContent).toMatch(/per unique test · latest run per suite/)
    rerender(<SummaryCatalogue part="headline" report={makeReport({ mode: 'window' })} days={30} mode="window" />)
    expect(section('summary-donut').textContent).toMatch(/per unique test · all runs in the window/)
  })

  it('still names the population when an older payload omits the basis label', () => {
    const report = makeReport()
    report.totals = { ...report.totals, pass_rate_basis_label: undefined }
    render(<SummaryCatalogue part="headline" report={report} days={1} mode="latest" />)
    expect(section('summary-donut').textContent).toMatch(/per unique test · latest run per suite/)
  })
})

describe('SummaryCatalogue — results by suite', () => {
  it('orders suites worst first: failed + broken, descending', () => {
    render(<SummaryCatalogue part="headline" report={makeReport()} days={30} mode="latest" />)
    // checkout-api 13 + 2, search 1 + 4, auth-api 2 + 0.
    expect(tableRows(section('summary-suites')).map(([name]) => name)).toEqual(['checkout-api', 'search', 'auth-api'])
  })
})

describe('SummaryCatalogue — the trend (the one new request)', () => {
  const today = utcDayIso()

  it('asks with the page’s window and the report’s release scope', () => {
    scope.release = 'rel-1'
    render(<SummaryCatalogue part="headline" report={makeReport()} days={7} mode="latest" />)
    expect(trends.calls[trends.calls.length - 1]).toEqual({ days: 7, releaseScope: 'rel-1' })
  })

  it('states its basis: executions in every run of the window, whichever aggregation is selected', () => {
    render(<SummaryCatalogue part="headline" report={makeReport()} days={30} mode="latest" />)
    const text = section('summary-trend').textContent ?? ''
    expect(text).toMatch(/whichever aggregation is selected/)
    expect(text).toMatch(/percent of test executions, not of unique tests/)
  })

  it('draws the day series over every window day, with the releases on their days', () => {
    releasesState.items = [
      { id: 'r1', name: 'v3.1', released_at: `${today}T08:00:00Z`, planned_date: null },
      // Not released yet: its planned day is the marker.
      { id: 'r2', name: 'v3.2', released_at: null, planned_date: today },
    ]
    trends.state = {
      status: 'ready',
      meta: null,
      revalidating: false,
      data: {
        period_days: 7,
        data: [{ date: `${today}T00:00:00`, passed: 9, failed: 1, skipped: 0, broken: 0, total: 10, pass_rate: 90 }],
      },
    }
    render(<SummaryCatalogue part="headline" report={makeReport()} days={7} mode="latest" />)
    const frame = section('summary-trend')
    const rows = tableRows(frame)
    expect(rows).toHaveLength(7)
    expect(rows[rows.length - 1]?.[1][0]).toMatch(/^90/)
    const markers = within(frame).getByRole('table', { name: /Release markers/i })
    expect((within(markers).getByRole('rowheader', { name: today }).parentElement as HTMLElement).querySelector('td')?.textContent).toBe('v3.1, v3.2')
  })

  it('says "not measured", never 0 %, when every execution in the window was skipped', () => {
    trends.state = {
      status: 'ready',
      meta: null,
      revalidating: false,
      data: { period_days: 1, data: [{ date: today, passed: 0, failed: 0, skipped: 4, broken: 0, total: 4, pass_rate: 0 }] },
    }
    render(<SummaryCatalogue part="headline" report={makeReport()} days={1} mode="latest" />)
    const frame = section('summary-trend')
    expect(within(frame).getByText('Not measured')).toBeInTheDocument()
    expect(frame.textContent).toMatch(/All runs in the last 24 hours/)
  })

  it('shows its own error inside its frame, with Retry, and leaves the other sections drawn', () => {
    const retry = vi.fn()
    trends.state = { status: 'error', error: { kind: 'server', message: 'The server failed.', requestId: 'req-9', status: 500 }, retry }
    render(<SummaryCatalogue part="headline" report={makeReport()} days={30} mode="latest" />)
    fireEvent.click(within(section('summary-trend')).getByRole('button', { name: /retry/i }))
    expect(retry).toHaveBeenCalled()
    expect(within(section('summary-donut')).getByRole('button', { name: 'View as table' })).toBeInTheDocument()
  })
})
