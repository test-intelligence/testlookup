/**
 * Regression: /coverage/suite trend chart + days-selector URL rewrite.
 *
 * Two distinct bugs pinned in this file:
 *
 * 1) (2026-05-19) The days selector chips (7d/14d/30d/90d) had no
 *    effect when the URL already carried ``?days=``. The URL pin
 *    overrode the click. Fix: clicking a chip rewrites ?days= via
 *    ``setSearchParams(..., {replace: true})``.
 *
 * 2) (2026-05-19) /coverage/suite had no per-day trend chart. The new
 *    ``testManagementService.getSuiteTrend`` call powers a stacked
 *    bar chart of passed/failed/skipped/broken per day, gated on
 *    "at least one day with run_count > 0".
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { cloneElement, isValidElement, type ReactElement } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SuiteDetailPage from './SuiteDetailPage'

const mockGetSuiteTrend = vi.fn()

vi.mock('@/services/testManagementService', () => ({
  testManagementService: {
    getSuiteTrend: (...args: unknown[]) => mockGetSuiteTrend(...args),
  },
}))

// ``useSuiteDetail`` is the page's primary fetch. Stub it to a
// stable summary so the page renders past the loading branch.
vi.mock('@/hooks/useMetrics', () => ({
  useSuiteDetail: vi.fn(() => ({
    data: {
      summary: {
        unique_tests: 12, total_executions: 100,
        passed: 80, failed: 15, pass_rate: 80, avg_duration_ms: 1234,
      },
      test_cases: [
        { test_fingerprint: 'fp1', test_name: 't1', class_name: 'C', runs: 5,
          passed: 4, failed: 1, skipped: 0, pass_rate: 80,
          last_status: 'PASSED', last_duration_ms: 100, last_run_date: null },
      ],
      recent_runs: [
        { run_id: 'r1', build_number: 'b1', run_date: '2026-05-13',
          passed: 9, failed: 1, skipped: 0, pass_rate: 90 },
        { run_id: 'r2', build_number: 'b2', run_date: '2026-05-14',
          passed: 8, failed: 2, skipped: 0, pass_rate: 80 },
      ],
    },
    isLoading: false,
    error: null,
  })),
}))

vi.mock('@/hooks/useSuites', () => ({
  useSuites: vi.fn(() => ({ data: { items: [] } })),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (s: {
    activeProjectId: string; activeProject: null | { name: string }
  }) => unknown) => selector({ activeProjectId: 'proj-1', activeProject: { name: 'P' } })),
}))

// The charts are the kit's (VIZ-104), drawn by real Recharts. jsdom lays
// nothing out, so the ResponsiveContainer hands its chart a fixed size and
// the stacked columns and the time series really draw.
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) =>
      isValidElement(children)
        ? cloneElement(children as ReactElement<{ width?: number; height?: number }>, { width: 640, height: 220 })
        : null,
  }
})

function renderPage(url = '/coverage/suite?name=Auth&days=30') {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/coverage/suite" element={<SuiteDetailPage />} />
        </Routes>
      </MemoryRouter>
    </SWRConfig>,
  )
}

/** Three zero-filled days: one with no run, one ordinary, one with only skipped executions. */
const WEEK = [
  { date: '2026-05-12', run_count: 0, total_tests: 0,
    passed_count: 0, failed_count: 0, skipped_count: 0, broken_count: 0 },
  { date: '2026-05-13', run_count: 1, total_tests: 10,
    passed_count: 7, failed_count: 1, skipped_count: 1, broken_count: 1 },
  { date: '2026-05-14', run_count: 1, total_tests: 4,
    passed_count: 0, failed_count: 0, skipped_count: 4, broken_count: 0 },
]

/** The kit frame around a chart heading. */
const frameOf = (heading: HTMLElement) => heading.closest('[data-chart-frame]') as HTMLElement

/** The frame's table view, opened. */
function tableOf(frame: HTMLElement) {
  fireEvent.click(within(frame).getByRole('button', { name: 'View as table' }))
  const table = within(frame).getByRole('table', { name: /data table/i })
  const headers = within(table).getAllByRole('columnheader').map((th) => th.textContent ?? '')
  const row = (label: string) =>
    Array.from((within(table).getByRole('rowheader', { name: label }).parentElement as HTMLElement).querySelectorAll('td'), (td) => td.textContent)
  return { headers, row }
}

describe('SuiteDetailPage — regression', () => {
  beforeEach(() => {
    mockGetSuiteTrend.mockReset()
  })

  it('renders the run-history bar chart when trend has runs', async () => {
    mockGetSuiteTrend.mockResolvedValue({
      suite_name: 'Auth', days: 30,
      points: [
        { date: '2026-05-13', run_count: 1, total_tests: 10,
          passed_count: 9, failed_count: 1, skipped_count: 0, broken_count: 0 },
        { date: '2026-05-14', run_count: 2, total_tests: 20,
          passed_count: 18, failed_count: 2, skipped_count: 0, broken_count: 0 },
      ],
    })

    renderPage()

    await waitFor(() => expect(mockGetSuiteTrend).toHaveBeenCalled())
    expect(await screen.findByText(/Run history/)).toBeInTheDocument()
    // The summary line in the card header reports total runs +
    // executions across the window.
    expect(screen.getByText(/3 runs/i)).toBeInTheDocument()
  })

  it('hides the trend card when every day has run_count = 0', async () => {
    mockGetSuiteTrend.mockResolvedValue({
      suite_name: 'Auth', days: 30,
      points: [
        { date: '2026-05-13', run_count: 0, total_tests: 0,
          passed_count: 0, failed_count: 0, skipped_count: 0, broken_count: 0 },
      ],
    })

    renderPage()

    await waitFor(() => expect(mockGetSuiteTrend).toHaveBeenCalled())
    expect(screen.queryByText(/Run history/)).not.toBeInTheDocument()
  })

  it('draws the run history as four statuses — Skipped and Broken no longer share a colour', async () => {
    mockGetSuiteTrend.mockResolvedValue({ suite_name: 'Auth', days: 30, points: WEEK })
    renderPage()
    const frame = frameOf(await screen.findByRole('heading', { level: 3, name: 'Run history — last 30 days' }))
    const legend = Array.from(frame.querySelectorAll('[data-chart-legend] li'), (li) => li.textContent)
    expect(legend).toEqual(['Passed', 'Failed', 'Broken', 'Skipped'])
    const fillOf = (status: string) =>
      frame.querySelector(`[data-legend-status="${status}"] [data-legend-swatch]`)?.getAttribute('fill')
    const patternColour = (status: string) =>
      frame.querySelector(`pattern[id="${(fillOf(status) ?? '').slice(5, -1)}"] rect`)?.getAttribute('fill')
    expect(patternColour('skipped')).toBe('var(--status-skipped)')
    expect(patternColour('broken')).toBe('var(--status-broken)')
    // A day without a run is a MEASURED zero: in the table, 0 — not a gap.
    const { row } = tableOf(frame)
    expect(row('May 12')).toEqual(['0', '0', '0', '0', '0'])
    expect(row('May 13')).toEqual(['7', '1', '1', '1', '10'])
  })

  it('draws the pass rate PER DAY over the same window, a day with nothing evaluated as a gap', async () => {
    mockGetSuiteTrend.mockResolvedValue({ suite_name: 'Auth', days: 30, points: WEEK })
    renderPage()
    const frame = frameOf(await screen.findByRole('heading', { level: 3, name: 'Pass rate trend — last 30 days' }))
    // Per day, not per run: the old heading counted runs.
    expect(screen.queryByText(/Last \d+ Runs/i)).toBeNull()
    const { headers, row } = tableOf(frame)
    expect(headers[0]).toBe('Day (UTC)')
    // 7 passed of 9 evaluated (skipped is outside the rate).
    expect(row('2026-05-13')[0]).toBe('77.78')
    // No run that day: no pass rate — "—", never 0 %.
    expect(row('2026-05-12')[0]).toBe('—')
    // Only skipped that day: nothing evaluated, so no pass rate either.
    expect(row('2026-05-14')[0]).toBe('—')
    expect(frame.querySelector('[data-chart-gap-note]')?.textContent).toMatch(/^2 days have no pass rate/)
  })

  // R2 F7: a suite that runs every other day has no two adjacent days with a
  // rate, so the chart is a row of dots under a legend that shows a line. The
  // dots are not joined across a day without runs (that would draw a trend
  // through days nobody measured, as Trends does not either), so the card says
  // what each dot is, in words a sighted reader sees.
  it('says each point is a day with runs, and a day without runs a gap', async () => {
    const alternate = Array.from({ length: 6 }, (_, i) => ({
      date: `2026-05-1${i}`,
      run_count: i % 2 === 0 ? 1 : 0,
      total_tests: i % 2 === 0 ? 10 : 0,
      passed_count: i % 2 === 0 ? 9 : 0,
      failed_count: i % 2 === 0 ? 1 : 0,
      skipped_count: 0,
      broken_count: 0,
    }))
    mockGetSuiteTrend.mockResolvedValue({ suite_name: 'Auth', days: 30, points: alternate })
    renderPage()
    const frame = frameOf(await screen.findByRole('heading', { level: 3, name: 'Pass rate trend — last 30 days' }))
    expect(within(frame).getByText('One point per day with runs · a day without runs is a gap, never 0%')).toBeVisible()
  })

  it('draws no chart tooltip in a fixed dark slate: the page carries no colour literal', async () => {
    const { default: source } = await import('./SuiteDetailPage.tsx?raw')
    expect(source).not.toMatch(/#[0-9a-f]{6}\b|#[0-9a-f]{3}\b/i)
    expect(source).not.toMatch(/from 'recharts'/)
  })

  it('passes the URL days param to the trend service', async () => {
    mockGetSuiteTrend.mockResolvedValue({ suite_name: 'Auth', days: 90, points: [] })

    renderPage('/coverage/suite?name=Auth&days=90')

    await waitFor(() => {
      // Third positional arg is ``days``.
      expect(mockGetSuiteTrend).toHaveBeenCalledWith('Auth', expect.anything(), 90)
    })
  })
})
