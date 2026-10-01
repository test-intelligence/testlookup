/**
 * The Summary report's catalogue sections (Wave 2.6, VIZ-408, plan 2.2
 * "Summary report").
 *
 * What this pins:
 *   - the donut, the suite bars and the failing-test bars read the page's own
 *     `/reports/summary` payload: its population (per unique test), its mode,
 *     every status (`broken` included), and no invented `unknown`;
 *   - suites are ordered worst first (failed + broken);
 *   - one test name in two suites is two bars (the row has no fingerprint);
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

  it('top-failing: only the failing-test bars, titled so the table’s own heading stays unique', () => {
    render(<SummaryCatalogue part="top-failing" report={makeReport()} days={30} mode="latest" />)
    expect(within(section('summary-top-failing')).getByRole('heading', { level: 2, name: 'Failures by test' })).toBeInTheDocument()
    expect(section('summary-donut')).toBeNull()
    // The page's table section is "Top failing tests"; a second heading starting so would make it ambiguous.
    expect(document.body.textContent).not.toMatch(/Top failing tests/)
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

describe('SummaryCatalogue — failures by test', () => {
  it('draws one test name in two suites as two bars, each naming its suite', () => {
    render(<SummaryCatalogue part="top-failing" report={makeReport()} days={30} mode="latest" />)
    const rows = new Map(tableRows(section('summary-top-failing')))
    expect(rows.get('test_login (auth-api)')?.[0]).toBe('5')
    expect(rows.get('test_login (search)')?.[0]).toBe('3')
    expect(rows.get('test_pay (checkout-api)')?.[0]).toBe('8')
  })

  it('renders a hostile test name as text', () => {
    const hostile = '<img src=x onerror="window.__xss=1">'
    render(
      <SummaryCatalogue
        part="top-failing"
        report={makeReport({ top_failing_tests: [{ suite_name: 's', class_name: null, test_name: hostile, failures: 2 }] })}
        days={30}
        mode="latest"
      />,
    )
    expect(new Map(tableRows(section('summary-top-failing'))).get(`${hostile} (s)`)?.[0]).toBe('2')
    expect(document.querySelector('img')).toBeNull()
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
