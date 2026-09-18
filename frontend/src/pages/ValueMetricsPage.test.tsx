/**
 * Hermetic tests for the Value Metrics page — US-12.1 hours-saved model.
 *
 * Mirrors the GitLabIntegrationPage.test.tsx style: REAL SWR hook + mocked
 * service layer + the real ALL_PROJECTS_ID sentinel, under an isolated
 * SWRConfig cache. Covers the load-bearing AC behaviours:
 *   - available=false → honest insufficient-data notice (reason shown),
 *     NO hours-saved headline / methodology link (never "0 hours saved").
 *   - available=true → headline + FTE render; the methodology panel opens
 *     from the "How is this calculated?" link and shows legs/formulas/
 *     caveats/research notes ("credibility requires showing the math").
 *   - Monthly chart receives the ascending series verbatim.
 *   - Assumptions editor: PUT sends only the changed fields; client-side
 *     bounds (0 < x <= 480) block out-of-range saves; the source badge flips
 *     after a successful save; permission-denied hides the editor.
 */
import { createElement } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SWRConfig } from 'swr'

import type { ValueMetrics, ValueMetricsMonthly } from '@/types/valueMetrics'

// ── mocks ───────────────────────────────────────────────────────────────────

const mockGet = vi.fn()
const mockGetMethodology = vi.fn()
const mockPutAssumptions = vi.fn()

vi.mock('@/services/valueMetricsService', () => ({
  valueMetricsService: {
    get: (...a: unknown[]) => mockGet(...a),
    getMethodology: (...a: unknown[]) => mockGetMethodology(...a),
    putAssumptions: (...a: unknown[]) => mockPutAssumptions(...a),
    exportUrl: vi.fn(() => '/api/v1/value-metrics/export?days=30'),
  },
}))

let mockCanManage = true
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ canAccessManagement: mockCanManage }),
}))

// Project store: mutable state so tests can flip into All-Projects mode.
// ALL_PROJECTS_ID is the REAL constant (importActual) so the page's sentinel
// comparison is tested against the production value, not an invented one.
const storeState: {
  activeProjectId: string | null
  activeProject: { id: string; name: string } | null
} = {
  activeProjectId: 'proj-1',
  activeProject: { id: 'proj-1', name: 'Project One' },
}
vi.mock('@/store/projectStore', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/store/projectStore')>()
  const useProjectStore = (sel: (s: typeof storeState) => unknown) => sel(storeState)
  return { useProjectStore, ALL_PROJECTS_ID: actual.ALL_PROJECTS_ID }
})

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}))

// Recharts renders nothing measurable in jsdom (zero-size ResponsiveContainer),
// so stand it in with a probe that exposes the data the chart received.
vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children?: React.ReactNode }) =>
    createElement('div', null, children),
  BarChart: ({ data, children }: { data?: unknown[]; children?: React.ReactNode }) =>
    createElement('div', { 'data-testid': 'monthly-chart', 'data-chart': JSON.stringify(data ?? []) }, children),
  Bar: () => null,
  XAxis: () => null,
  YAxis: () => null,
  CartesianGrid: () => null,
  Tooltip: () => null,
  Legend: () => null,
}))

// ── fixtures ────────────────────────────────────────────────────────────────

function month(m: string, seed: number): ValueMetricsMonthly {
  return {
    month: m,
    auto_triaged: 10 + seed,
    clustered_failures: 20 + seed,
    duplicates_absorbed: 3 + seed,
    quarantine_suppressed_failures: 5 + seed,
    runs_unblocked_proxy: 2 + seed,
    hours_triage: 8 + seed,
    hours_quarantine: 4 + seed,
    hours_dedup: 1 + seed,
    hours_total: 13 + 3 * seed,
  }
}

function baseMetrics(overrides: Partial<ValueMetrics> = {}): ValueMetrics {
  return {
    period_days: 30,
    project_id: 'proj-1',
    triage_time_saved_minutes: 360,
    triage_time_saved_hours: 6,
    defects_auto_grouped: 12,
    tests_grouped: 44,
    duplicate_tickets_avoided: 5,
    defects_promoted: 9,
    flaky_tests_identified: 3,
    quarantine_recommended: 2,
    risky_releases_blocked: 4,
    releases_conditional: 1,
    release_overrides: 0,
    intelligence_reports_generated: 7,
    available: true,
    insufficient_data_reason: null,
    headline: { hours_saved_30d: 42.5, fte_equivalent_30d: 0.8 },
    monthly: [month('2026-05-01', 0), month('2026-06-01', 1), month('2026-07-01', 2)],
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

const methodologyFixture = {
  version: 1,
  legs: [
    {
      key: 'triage',
      title: 'Auto-triage leg',
      formula: 'auto_triaged × triage_minutes_per_failure ÷ 60',
      inputs: ['auto_triaged', 'triage_minutes_per_failure'],
      caveats: ['Assumes every auto-triaged failure would otherwise be triaged by hand.'],
    },
    {
      key: 'quarantine',
      title: 'Quarantine leg',
      formula: 'quarantine_suppressed_failures × blocked_run_wait_minutes ÷ 60',
      inputs: ['quarantine_suppressed_failures', 'blocked_run_wait_minutes'],
      caveats: ['Runs-unblocked is a proxy, not a direct measurement.'],
    },
  ],
  defaults: {
    triage_minutes_per_failure: 15,
    blocked_run_wait_minutes: 30,
    defect_filing_minutes: 10,
  },
  research_notes: ['Median manual triage time sourced from internal QA benchmarks.'],
}

async function renderPage() {
  const { default: ValueMetricsPage } = await import('./ValueMetricsPage')
  return render(
    createElement(
      SWRConfig,
      {
        value: {
          provider: () => new Map(),
          dedupingInterval: 0,
          shouldRetryOnError: false,
          revalidateOnFocus: false,
        },
      },
      createElement(ValueMetricsPage),
    ),
  )
}

describe('ValueMetricsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockCanManage = true
    storeState.activeProjectId = 'proj-1'
    storeState.activeProject = { id: 'proj-1', name: 'Project One' }
    mockGet.mockResolvedValue(baseMetrics())
    mockGetMethodology.mockResolvedValue(methodologyFixture)
    mockPutAssumptions.mockResolvedValue({
      assumptions: baseMetrics().assumptions,
      source: 'custom',
    })
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders the value realization workflow strip once metrics load', async () => {
    await renderPage()

    await waitFor(() => {
      expect(screen.getByText(/Value Realization Workflow/i)).toBeInTheDocument()
    })
    expect(screen.getByText(/Value Capture/i)).toBeInTheDocument()
    expect(screen.getByText(/Value Metrics/i)).toBeInTheDocument()
  })

  it('renders the hours-saved headline + FTE equivalent when available', async () => {
    await renderPage()

    expect(await screen.findByText(/Eng-Hours Saved · Last 30 Days/i)).toBeInTheDocument()
    expect(screen.getByText('42.5h')).toBeInTheDocument()
    expect(screen.getByText(/≈ 0\.8 FTE over the last 30 days/)).toBeInTheDocument()
    expect(screen.getByText('How is this calculated?')).toBeInTheDocument()
  })

  it('shows the honest insufficient-data notice — never a zero headline — when unavailable', async () => {
    mockGet.mockResolvedValue(baseMetrics({
      available: false,
      insufficient_data_reason: 'Need at least 14 days of ingested runs to estimate.',
      headline: { hours_saved_30d: 0, fte_equivalent_30d: 0 },
      monthly: [],
    }))
    await renderPage()

    expect(await screen.findByText(/Not enough data yet to estimate engineering hours saved/i)).toBeInTheDocument()
    expect(screen.getByText('Need at least 14 days of ingested runs to estimate.')).toBeInTheDocument()
    // No hours-saved headline, no methodology link, no monthly chart.
    expect(screen.queryByText(/Eng-Hours Saved · Last 30 Days/i)).toBeNull()
    expect(screen.queryByText('How is this calculated?')).toBeNull()
    expect(screen.queryByTestId('monthly-chart')).toBeNull()
  })

  it('renders a retryable request error instead of a blank page', async () => {
    mockGet.mockRejectedValueOnce(new Error('temporary outage')).mockResolvedValue(baseMetrics())
    await renderPage()

    expect(await screen.findByTestId('value-metrics-data-unavailable')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))

    expect(await screen.findByText(/Eng-Hours Saved · Last 30 Days/i)).toBeInTheDocument()
    expect(mockGet).toHaveBeenCalledTimes(2)
  })

  it('opens the methodology panel showing legs, formulas, caveats and research notes', async () => {
    await renderPage()

    fireEvent.click(await screen.findByText('How is this calculated?'))

    expect(await screen.findByText('Auto-triage leg')).toBeInTheDocument()
    expect(screen.getByText('Quarantine leg')).toBeInTheDocument()
    expect(screen.getByText('auto_triaged × triage_minutes_per_failure ÷ 60')).toBeInTheDocument()
    expect(screen.getByText(/Runs-unblocked is a proxy, not a direct measurement/)).toBeInTheDocument()
    expect(screen.getByText(/Median manual triage time sourced from internal QA benchmarks/)).toBeInTheDocument()
    expect(screen.getByText(/Methodology version 1/)).toBeInTheDocument()

    fireEvent.click(screen.getByText('Close'))
    expect(screen.queryByText('Auto-triage leg')).toBeNull()
  })

  it('passes the ascending monthly series to the chart verbatim', async () => {
    await renderPage()

    const chart = await screen.findByTestId('monthly-chart')
    const data = JSON.parse(chart.getAttribute('data-chart') ?? '[]') as ValueMetricsMonthly[]
    expect(data.map((m) => m.month)).toEqual(['2026-05-01', '2026-06-01', '2026-07-01'])
    expect(data[0].hours_triage).toBe(8)
  })

  it('renders the honest model-count labels (duplicates absorbed, runs unblocked estimate)', async () => {
    await renderPage()

    expect(await screen.findByText(/Duplicate failures absorbed/i)).toBeInTheDocument()
    expect(screen.getByText(/Runs unblocked \(estimate\)/i)).toBeInTheDocument()
  })

  it('PUTs only the changed assumption fields', async () => {
    await renderPage()

    const input = await screen.findByLabelText('Triage minutes per failure')
    fireEvent.change(input, { target: { value: '20' } })
    await act(async () => {
      fireEvent.click(screen.getByText('Save assumptions'))
    })

    expect(mockPutAssumptions).toHaveBeenCalledTimes(1)
    const [projectId, patch] = mockPutAssumptions.mock.calls[0]
    expect(projectId).toBe('proj-1')
    expect(patch).toEqual({ triage_minutes_per_failure: 20 })
  })

  it('blocks out-of-range assumption values client-side (0 < x <= 480)', async () => {
    await renderPage()

    const input = await screen.findByLabelText('Triage minutes per failure')

    fireEvent.change(input, { target: { value: '0' } })
    await act(async () => {
      fireEvent.click(screen.getByText('Save assumptions'))
    })
    expect(mockPutAssumptions).not.toHaveBeenCalled()
    expect(screen.getByText(/Must be greater than 0 and at most 480 minutes/)).toBeInTheDocument()

    fireEvent.change(input, { target: { value: '481' } })
    await act(async () => {
      fireEvent.click(screen.getByText('Save assumptions'))
    })
    expect(mockPutAssumptions).not.toHaveBeenCalled()

    // A valid value clears the error and saves.
    fireEvent.change(input, { target: { value: '480' } })
    await act(async () => {
      fireEvent.click(screen.getByText('Save assumptions'))
    })
    expect(mockPutAssumptions).toHaveBeenCalledTimes(1)
    expect(mockPutAssumptions.mock.calls[0][1]).toEqual({ triage_minutes_per_failure: 480 })
  })

  it('flips the source badge from Defaults to Customized after a successful save', async () => {
    await renderPage()

    expect(await screen.findByText('Defaults')).toBeInTheDocument()

    // The post-save revalidation returns the customized payload.
    mockGet.mockResolvedValue(baseMetrics({ assumptions_source: 'custom' }))

    const input = screen.getByLabelText('Blocked-run wait minutes')
    fireEvent.change(input, { target: { value: '45' } })
    await act(async () => {
      fireEvent.click(screen.getByText('Save assumptions'))
    })

    expect(mockPutAssumptions.mock.calls[0][1]).toEqual({ blocked_run_wait_minutes: 45 })
    expect(await screen.findByText('Customized')).toBeInTheDocument()
    expect(screen.queryByText('Defaults')).toBeNull()
  })

  it('hides the assumptions editor for viewers without management access', async () => {
    mockCanManage = false
    await renderPage()

    await screen.findByText(/Eng-Hours Saved · Last 30 Days/i)
    expect(screen.queryByText('Model assumptions')).toBeNull()
    expect(screen.queryByLabelText('Triage minutes per failure')).toBeNull()
    expect(screen.queryByText('Save assumptions')).toBeNull()
  })

  it('hides the assumptions editor in All-Projects mode (assumptions are per-project)', async () => {
    const { ALL_PROJECTS_ID } = await import('@/store/projectStore')
    storeState.activeProjectId = ALL_PROJECTS_ID
    storeState.activeProject = null
    await renderPage()

    await screen.findByText(/Eng-Hours Saved · Last 30 Days/i)
    expect(screen.queryByText('Model assumptions')).toBeNull()
  })
})
