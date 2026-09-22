/**
 * The CONNECTED chrome against the state half's API (`useReportScope`) and
 * one real `useReportMetrics` request. The store is faked with the agreed
 * signature; only the HTTP helper is faked below the hook.
 *
 * The key property: the header states what the SERVER applied. Here the
 * client asks for release R-REQ and suite "payments"; the server applies the
 * suite, ignores the release and says so — the header must read "All
 * releases" (with the reason) while the chip still shows the request, marked
 * with a warning.
 */
import type { ReactNode } from 'react'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { GALLERY_CASES } from '@/pages/dev/reportContextFixtures'

const scopeState = vi.hoisted(() => ({
  projectId: 'p1' as string | null,
  allProjects: false,
  releaseIds: ['r-req'],
  suiteNames: ['payments'],
  windowDays: 30,
  setReleaseIds: vi.fn(),
  setSuiteNames: vi.fn(),
  setWindowDays: vi.fn(),
  clearAll: vi.fn(),
  droppedNotice: [] as { dimension: 'release' | 'suite'; values: string[]; reason: string }[],
  dismissDroppedNotice: vi.fn(),
}))

vi.mock('@/store/reportScope', () => ({
  RELEASE_CAP: 20,
  SUITE_CAP: 50,
  DEFAULT_WINDOW_DAYS: 30,
  useReportScope: () => scopeState,
}))
/** The DATA clock (settled 250 ms after the last change), as the page's hooks read it. */
const settled = vi.hoisted(() => ({ releases: ['r-req'] as string[] | null, suites: ['payments'] as string[] | null }))
vi.mock('@/hooks/useReleaseScope', () => ({ useReleaseScope: () => settled.releases }))
vi.mock('@/hooks/useSuiteScope', () => ({ useSuiteScope: () => settled.suites }))
vi.mock('@/hooks/useReleases', () => ({
  useReleases: () => ({ data: { items: [{ id: 'r-req', name: '2026.09', status: 'in_progress' }] } }),
}))
vi.mock('@/hooks/useSuiteOptions', () => ({ useSuiteOptions: () => ({ options: ['payments', 'cart'] }) }))
vi.mock('@/services/http', () => ({
  getData: vi.fn(),
  postData: vi.fn(),
  putData: vi.fn(),
  patchData: vi.fn(),
  deleteData: vi.fn(),
}))

import { getData } from '@/services/http'
import ReportChrome from './ReportChrome'

const get = vi.mocked(getData)
const base = GALLERY_CASES.find((c) => c.id === 'unfiltered')?.meta
const SERVER_META = {
  ...base,
  scope: { ...base?.scope, releases: [], suites: ['payments'] },
  totals: { matched_runs: 18, total_runs: 143, matched_executions: 412, total_executions: 3960 },
  ignored_filters: [{ dimension: 'release', reason: 'Defect counts are project-wide.' }],
}

function wrapper({ children }: { children: ReactNode }) {
  return <SWRConfig value={{ provider: () => new Map() }}>{children}</SWRConfig>
}

const renderChrome = (route: '/overview' | '/value-metrics' | '/reports/summary' = '/overview') =>
  render(<ReportChrome route={route} />, { wrapper })
/** The params of the LAST summary request. */
const summaryParams = () => {
  const calls = get.mock.calls.filter(([url]) => url === '/api/v1/metrics/summary')
  return (calls[calls.length - 1]?.[1] as { params: Record<string, unknown> }).params
}

describe('ReportChrome (connected)', () => {
  beforeEach(() => {
    get.mockReset()
    get.mockResolvedValue({
      total_executions_7d: { value: 412 },
      avg_pass_rate_7d: { value: 91.2, basis: 'executions' },
      flaky_test_count: { value: 5 },
      avg_duration_ms: { value: 1200 },
      meta: SERVER_META,
    })
    scopeState.setReleaseIds.mockClear()
    scopeState.setSuiteNames.mockClear()
    scopeState.windowDays = 30
    scopeState.releaseIds = ['r-req']
    scopeState.suiteNames = ['payments']
    settled.releases = ['r-req']
    settled.suites = ['payments']
  })

  it('makes ONE request, and the header shows the APPLIED scope, not the requested one', async () => {
    renderChrome()
    await waitFor(() => expect(document.querySelector('[data-report-context-header]')).toHaveAttribute('data-state', 'ready'))
    expect(get.mock.calls.filter(([url]) => url === '/api/v1/metrics/summary')).toHaveLength(1)
    expect(get.mock.calls[0][1]).toEqual({
      params: { project_id: 'p1', days: 30, release_id: 'r-req', suite_name: 'payments', include: 'report_metrics' },
      suppressToast: true,
    })

    const release = document.querySelector('[data-context-entry="release"] dd') as HTMLElement
    expect(within(release).getByText('All releases')).toBeInTheDocument()
    expect(release.textContent).not.toContain('2026.09')
    expect(release.textContent).toContain('Filter not applied: Defect counts are project-wide.')
    expect(document.querySelector('[data-context-entry="suite"] dd')?.textContent).toBe('payments')
  })

  it('the chips show the request, warn on the ignored dimension, and write back to the store', async () => {
    renderChrome()
    await waitFor(() => expect(screen.getByRole('img', { name: 'Warning: Defect counts are project-wide.' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Remove filter Release 2026.09' }))
    expect(scopeState.setReleaseIds).toHaveBeenCalledWith([])
    fireEvent.click(screen.getByRole('button', { name: 'Remove filter Suite payments' }))
    expect(scopeState.setSuiteNames).toHaveBeenCalledWith([])
  })

  it('the same response feeds the summary and the strip', async () => {
    renderChrome()
    await waitFor(() =>
      expect(screen.getByText('Showing 18 of 143 runs · 412 of 3,960 executions · 1 suite · last 30 days')).toBeInTheDocument(),
    )
    const runs = document.querySelector('[data-metric="runs"]') as HTMLElement
    expect(runs.textContent).toContain('18')
    expect(document.querySelector('[data-metric="pass_rate"]')?.textContent).toContain('91.2%')
    expect(document.querySelector('[data-metric="pass_rate"]')?.textContent).toContain('of executions')
  })

  it('requests with the SETTLED scope the page uses, not the selection mid-burst (M1)', async () => {
    // The user has just ticked "cart" (UI clock); the page's hooks have not
    // settled on it yet, so neither may the chrome.
    scopeState.suiteNames = ['cart', 'payments']
    settled.suites = ['payments']
    renderChrome()
    await waitFor(() => expect(get).toHaveBeenCalled())
    expect(summaryParams().suite_name).toBe('payments')
    // With the flag on, no settled release means none is sent, whatever the UI shows.
    settled.releases = null
    settled.suites = null
    renderChrome('/reports/summary')
    await waitFor(() => expect(get.mock.calls.length).toBeGreaterThan(1))
    expect(summaryParams()).toEqual({ project_id: 'p1', days: 30, include: 'report_metrics' })
  })

  it('a stored window the endpoint rejects is never sent; the header says which window it used (M2)', async () => {
    // Value metrics offers a year; /metrics/summary allows 90.
    scopeState.windowDays = 365
    renderChrome('/value-metrics')
    await waitFor(() => expect(get).toHaveBeenCalled())
    expect(summaryParams().days).toBe(90)
    await waitFor(() => expect(document.querySelector('[data-context-window-note]')).not.toBeNull())
    expect(document.querySelector('[data-context-entry="window"] dd')?.textContent).toContain('(max for summary)')
  })

  it('a stored "All time" (0) is sent as the page shows it (24 h on Overview), never as 0 (M2)', async () => {
    scopeState.windowDays = 0
    renderChrome('/overview')
    await waitFor(() => expect(get).toHaveBeenCalled())
    expect(summaryParams().days).toBe(1)
    expect(screen.getByRole('combobox', { name: 'Window' })).toHaveValue('1')
    expect(document.querySelector('[data-context-window-note]')).toBeNull()
  })

  it('a failed request shows its own reason inline: header and every tile, "—" never 0 (M2)', async () => {
    get.mockRejectedValue(Object.assign(new Error('Unprocessable'), { response: { status: 422 } }))
    renderChrome()
    await waitFor(() =>
      expect(document.querySelector('[data-report-context-header]')).toHaveAttribute('data-state', 'unavailable'),
    )
    expect(screen.getByText('Report context is unavailable: the metrics request failed (HTTP 422).')).toBeInTheDocument()
    const runs = document.querySelector('[data-metric="runs"]') as HTMLElement
    expect(runs).toHaveAttribute('data-measured', 'false')
    expect(runs.textContent).toContain('Not measured: the metrics request failed (HTTP 422)')
  })

  // Fix round B (a11y M3): the layout mounts the chrome ABOVE the page's
  // <h1> and pages render their h1 inline (no shared outlet), so the chrome
  // names itself with an <h2> and can be skipped in one step.
  it('is headed "Report context" (h2) and "Skip to report content" jumps past it', async () => {
    renderChrome()
    expect(screen.getByRole('heading', { level: 2, name: 'Report context' })).toBeInTheDocument()
    const skip = screen.getByRole('link', { name: 'Skip to report content' })
    const chrome = document.querySelector('[data-report-chrome]') as HTMLElement
    expect(chrome.firstElementChild).toBe(skip)
    fireEvent.click(skip)
    const end = document.querySelector('[data-report-chrome-end]') as HTMLElement
    expect(end).toHaveFocus()
    expect(chrome.lastElementChild).toBe(end)
  })

  it('an envelope that fails the contract is not rendered as context', async () => {
    get.mockResolvedValue({ total_executions_7d: { value: 1 }, meta: { schema_version: 1 } })
    renderChrome()
    await waitFor(() =>
      expect(document.querySelector('[data-report-context-header]')).toHaveAttribute('data-state', 'unavailable'),
    )
    expect(screen.getByText(/did not describe its scope/)).toBeInTheDocument()
    expect(document.querySelector('[data-filtered-summary]')).toBeNull()
  })
})
