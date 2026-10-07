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
vi.mock('@/hooks/usePerformanceSettings', () => ({
  usePerformanceSettings: () => mockUsePerformanceSettings(),
}))

beforeEach(() => {
  mockUsePerformanceSettings.mockReturnValue(settings)
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
})
