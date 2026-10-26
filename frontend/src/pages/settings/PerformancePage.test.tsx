/**
 * PerformancePage — read-only performance budgets (Settings › System).
 *
 * No coverage before UX redesign P5. What P5 changed is the header: the page's
 * own `<h1>` block became the template's compact `PageHeader` with the route's
 * help topic. The page is read-only (no form), so nothing else moved; the
 * tests below also pin that the three tabs still render their data.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PerformancePage from './PerformancePage'
import { expectTemplateHeader } from '@/test/expectTemplateHeader'

const settings = {
  budgets: {
    latency_budgets: [{ operation: 'search.query', p50_ms: 80, p95_ms: 250, p99_ms: 600, description: 'Full-text search' }],
    throughput_budgets: [{ operation: 'ingest.results', min_rps: 50, description: 'Result ingestion' }],
    scale_scenarios: [
      { name: 'Enterprise', description: 'Large tenant', projects: 40, runs_per_day: 900, tests_per_run: 5000, concurrent_users: 120 },
    ],
  },
  config: {
    index_batch_size: 500,
    incremental_limit: 2000,
    query_timeout_ms: 1500,
    max_results: 100,
    pg_pool_size: 20,
    pg_max_overflow: 10,
    pg_pool_recycle: 1800,
    celery_worker_concurrency: 4,
  },
  isLoading: false,
  isError: false,
}

const mockUsePerformanceSettings = vi.fn()
const mockObserved = vi.fn()
const mockSlow = vi.fn()
const permissions = vi.hoisted(() => ({ isAdmin: true }))
vi.mock('@/hooks/usePerformanceSettings', () => ({
  usePerformanceSettings: () => mockUsePerformanceSettings(),
  useObservedPerformance: (w: string) => mockObserved(w),
  useSlowQueries: (enabled: boolean) => mockSlow(enabled),
}))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => permissions }))

const OBSERVED_ROW = {
  operation: 'search.query', http_handler: '/api/v1/search', http_method: 'GET',
  budget_p50_ms: 80, budget_p95_ms: 250, budget_p99_ms: 600,
  observed_p50_ms: 60, observed_p95_ms: 310, observed_p99_ms: 420, count: 1234, measured: true, breach: true,
}

beforeEach(() => {
  permissions.isAdmin = true
  mockUsePerformanceSettings.mockReturnValue(settings)
  mockObserved.mockReturnValue({ data: { window: '1h', source: 'in_app', measured: true, slot_seconds: 300, budgets: [OBSERVED_ROW] }, isLoading: false })
  mockSlow.mockReturnValue({ data: { source: 'in_app', threshold_ms: 500, items: [], total: 0 }, isLoading: false })
})

describe('PerformancePage', () => {
  it('replaces the page-local h1 with the compact header and the route help topic', () => {
    render(<PerformancePage />)
    expectTemplateHeader('Performance Budgets', '/settings/performance')
  })

  it('still renders each tab from the settings', () => {
    render(<PerformancePage />)
    expect(screen.getByText('search.query')).toBeInTheDocument()
    expect(screen.getByText('50 rps')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Search Config' }))
    expect(screen.getByText('Index Batch Size')).toBeInTheDocument()
    expect(screen.getByText('1500ms')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Scale Scenarios' }))
    expect(screen.getByText('Enterprise')).toBeInTheDocument()
    expect(screen.getByText('Concurrent Users')).toBeInTheDocument()
  })

  it('W17b ADM-06: shows the measured p50/p95/p99 beside the budget; a p95 over budget is red', () => {
    render(<PerformancePage />)
    const row = document.querySelector('[data-operation="search.query"]') as HTMLElement
    expect(row).toHaveTextContent('250 ms')
    expect(row).toHaveTextContent('60 ms')
    expect(row).toHaveTextContent('1,234')
    const p95 = screen.getByText('310 ms')
    expect(p95).toHaveAttribute('data-breach', 'true')
    expect(screen.getByText('420 ms')).toHaveAttribute('data-breach', 'false')
    expect(screen.getByTestId('observed-source')).toHaveTextContent('measured by TestLookup (last hour, all API pods)')
    expect(screen.getByTestId('observed-source')).toHaveTextContent('1 over budget')
  })

  it('W17b ADM-06: a route with no requests says so instead of 0 ms', () => {
    mockObserved.mockReturnValue({ data: { window: '1h', source: 'in_app', measured: false, slot_seconds: 300, budgets: [{ ...OBSERVED_ROW, observed_p50_ms: null, observed_p95_ms: null, observed_p99_ms: null, count: 0, measured: false, breach: false }] }, isLoading: false })
    render(<PerformancePage />)
    expect(screen.getByText('No requests')).toBeInTheDocument()
    expect(screen.queryByText('0 ms')).toBeNull()
  })

  it('W17b ADM-06: the window picker re-reads the measurement', () => {
    render(<PerformancePage />)
    fireEvent.change(screen.getByLabelText('Observation window'), { target: { value: '24h' } })
    expect(mockObserved).toHaveBeenLastCalledWith('24h')
  })

  it('W17b ADM-06: Slow queries is an admin tab with statement shapes and an empty state', () => {
    mockSlow.mockReturnValue({ data: { source: 'in_app', threshold_ms: 500, total: 1, items: [
      { fingerprint: 'f1', sql: 'SELECT * FROM test_runs WHERE project_id = ?', count: 3, total_ms: 2100, mean_ms: 700, max_ms: 950, last_seen: 1790000000 },
    ] }, isLoading: false })
    render(<PerformancePage />)
    fireEvent.click(screen.getByRole('button', { name: 'Slow queries' }))
    expect(screen.getByText('SELECT * FROM test_runs WHERE project_id = ?')).toBeInTheDocument()
    expect(screen.getByText('950 ms')).toBeInTheDocument()

    mockSlow.mockReturnValue({ data: { source: 'in_app', threshold_ms: 500, items: [], total: 0 }, isLoading: false })
    render(<PerformancePage />)
    fireEvent.click(screen.getAllByRole('button', { name: 'Slow queries' })[1])
    expect(screen.getByTestId('slow-queries-empty')).toHaveTextContent('No query over 500 ms has been recorded.')
  })

  it('W17b ADM-06: a QA lead has no Slow queries tab', () => {
    permissions.isAdmin = false
    render(<PerformancePage />)
    expect(screen.queryByRole('button', { name: 'Slow queries' })).toBeNull()
  })
})
