import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ValueMetrics } from '@/types/valueMetrics'
import OverviewPage from './OverviewPage'

vi.mock('@/hooks/useMetrics', () => {
  const d = () => ({ data: undefined, isLoading: false })
  return {
    useDashboardSummary:  vi.fn(d),
    useTrendData:         vi.fn(d),
    useFlakyTests:        vi.fn(d),
    useFailureCategories: vi.fn(d),
    useTopFailing:        vi.fn(d),
    useCoverage:          vi.fn(d),
    useDefects:           vi.fn(d),
    useSuiteDetail:       vi.fn(d),
    useAiSummary:         vi.fn(d),
  }
})
vi.mock('@/hooks/useSuiteOptions', () => ({
  useSuiteOptions: () => ({ options: [], isLoading: false }),
}))
// Eng-hours saved KPI (US-12.2): mutable state so tests can flip between
// available / unavailable / not-yet-loaded.
const valueKpiState: { metrics: ValueMetrics | undefined } = { metrics: undefined }
vi.mock('@/hooks/useValueMetrics', () => ({
  useValueMetricsKpi: () => valueKpiState,
}))
vi.mock('@/hooks/useAnalyticsView', () => ({
  useAnalyticsView: () => ({
    instances: [], widgetIds: [], addInstance: vi.fn(), removeInstance: vi.fn(),
    save: vi.fn(), reset: vi.fn(), isDirty: false, savedViews: [],
    activeViewId: null, setActiveView: vi.fn(), deleteView: vi.fn(),
    updateInstance: vi.fn(), moveInstance: vi.fn(),
  }),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: { activeProjectId: string; activeProject: { name: string } | null }) => unknown) =>
    selector({ activeProjectId: 'proj-1', activeProject: { name: 'Project One' } })),
}))

function hoursSavedMetrics(overrides: Partial<ValueMetrics> = {}): ValueMetrics {
  return {
    period_days: 30,
    project_id: 'proj-1',
    triage_time_saved_minutes: 0,
    triage_time_saved_hours: 0,
    defects_auto_grouped: 0,
    tests_grouped: 0,
    duplicate_tickets_avoided: 0,
    defects_promoted: 0,
    flaky_tests_identified: 0,
    quarantine_recommended: 0,
    risky_releases_blocked: 0,
    releases_conditional: 0,
    release_overrides: 0,
    intelligence_reports_generated: 0,
    available: true,
    insufficient_data_reason: null,
    headline: { hours_saved_30d: 42.5, fte_equivalent_30d: 0.8 },
    monthly: [],
    assumptions: {
      triage_minutes_per_failure: 15,
      blocked_run_wait_minutes: 30,
      defect_filing_minutes: 10,
    },
    assumptions_source: 'default',
    methodology_version: 1,
    ...overrides,
  }
}

function mockDashboardData(useDashboardSummary: unknown, useTrendData: unknown) {
  ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({
    data: {
      release_readiness: 'GREEN',
      total_executions_7d: { value: 120 },
      avg_pass_rate_7d: { value: 91.5 },
      active_defects: { value: 4 },
      flaky_test_count: { value: 2 },
      new_failures_24h: { value: 3 },
      avg_duration_ms: { value: 180000 },
    },
    isLoading: false,
  })
  ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
    data: { data: [] },
    isLoading: false,
  })
}

describe('OverviewPage', () => {
  beforeEach(() => {
    valueKpiState.metrics = undefined
  })

  it('renders the Eng-hours saved KPI card only when the hours-saved model is available', async () => {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    mockDashboardData(useDashboardSummary, useTrendData)
    valueKpiState.metrics = hoursSavedMetrics()

    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText('Eng-hours saved')).toBeInTheDocument()
    expect(screen.getByText(/42\.5/)).toBeInTheDocument()
    expect(screen.getByText(/View value metrics/i)).toBeInTheDocument()
  })

  it('omits the Eng-hours saved KPI card entirely when unavailable — no dash-card', async () => {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    mockDashboardData(useDashboardSummary, useTrendData)
    valueKpiState.metrics = hoursSavedMetrics({
      available: false,
      insufficient_data_reason: 'Not enough ingested data yet.',
      headline: { hours_saved_30d: 0, fte_equivalent_30d: 0 },
    })

    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )

    await screen.findAllByText(/Quality workflow/i)
    expect(screen.queryByText('Eng-hours saved')).toBeNull()
    expect(screen.queryByText(/View value metrics/i)).toBeNull()
  })

  it('omits the Eng-hours saved KPI card while value metrics have not loaded', async () => {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    mockDashboardData(useDashboardSummary, useTrendData)
    valueKpiState.metrics = undefined

    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )

    await screen.findAllByText(/Quality workflow/i)
    expect(screen.queryByText('Eng-hours saved')).toBeNull()
  })

  it('renders the quality workflow strip above dashboard metrics', async () => {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')

    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        release_readiness: 'GREEN',
        total_executions_7d: { value: 120 },
        avg_pass_rate_7d: { value: 91.5 },
        active_defects: { value: 4 },
        flaky_test_count: { value: 2 },
        new_failures_24h: { value: 3 },
        avg_duration_ms: { value: 180000 },
      },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        data: [
          { day: '2026-04-01', passed: 90, failed: 5, skipped: 2, broken: 0 },
          { day: '2026-04-02', passed: 91, failed: 4, skipped: 1, broken: 0 },
        ],
      },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )

    // ``Quality workflow`` appears in both the workflow strip and the
    // dashboard widget header, so use getAllByText for the presence check.
    expect((await screen.findAllByText(/Quality workflow/i)).length).toBeGreaterThan(0)
    expect(screen.getByText(/^Dashboard$/i)).toBeInTheDocument()
    // The page renders the verdict via ``gateLabel`` — ``GREEN`` readiness
    // maps to "Go". Match the rendered label rather than the raw backend
    // colour to stay aligned with the verdict-led redesign.
    expect(screen.getAllByText(/\bGo\b/).length).toBeGreaterThan(0)
  })

  it('shows Pending readiness instead of RED when there are zero executions', async () => {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')

    // Backend can still return RED on an empty dataset (default thresholds
    // applied to zero data) — the UI must override the verdict because
    // "Critical issues must be resolved" is misleading when there's nothing
    // to assess.
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        release_readiness: 'RED',
        total_executions_7d: { value: 0 },
        avg_pass_rate_7d: { value: 0 },
        active_defects: { value: 0 },
        flaky_test_count: { value: 0 },
        new_failures_24h: { value: 0 },
        avg_duration_ms: { value: 0 },
      },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { data: [] },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )

    // Zero-executions case must surface as the Pending verdict (via
    // ``mapReadinessToVerdict`` → ``gateLabel`` = "Pending"), not the
    // misleading "Critical issues must be resolved" copy.
    expect(await screen.findByText(/\bPending\b/)).toBeInTheDocument()
    expect(screen.queryByText(/Critical issues must be resolved/i)).toBeNull()
  })

  it('does not render fabricated cost/evidence/duration literals or dead CTAs', async () => {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')

    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        release_readiness: 'GREEN',
        total_executions_7d: { value: 120 },
        avg_pass_rate_7d: { value: 91.5 },
        active_defects: { value: 4 },
        flaky_test_count: { value: 2 },
        new_failures_24h: { value: 3 },
        avg_duration_ms: { value: 180000 },
      },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { data: [{ date: '2026-04-02', passed: 91, failed: 4, skipped: 1, broken: 0, pass_rate: 94 }] },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )

    await screen.findAllByText(/Quality workflow/i)
    // Fabricated ribbon literals must be gone (the dashboard has no real
    // per-run cost / evidence-count / stage-duration signal to report).
    expect(screen.queryByText(/\$0\.31/)).toBeNull()
    expect(screen.queryByText(/evidence items/i)).toBeNull()
    // The invented "readiness confidence %" is replaced by the real pass rate.
    expect(screen.queryByText(/Readiness confidence/i)).toBeNull()
    expect(screen.getAllByText(/^Pass rate$/i).length).toBeGreaterThan(0)
    // Dead CTAs (no-op handlers) are removed, not shipped as inert buttons.
    expect(screen.queryByRole('button', { name: /Run quality workflow/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /View evidence/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /Override gate/i })).toBeNull()
  })
})
