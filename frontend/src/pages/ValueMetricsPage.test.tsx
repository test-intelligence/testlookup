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
 *   - Monthly chart draws the ascending series verbatim (its table view),
 *     each model leg in a SERIES colour, not a borrowed status colour.
 *   - Assumptions editor: PUT sends only the changed fields; client-side
 *     bounds (0 < x <= 480) block out-of-range saves; the source badge flips
 *     after a successful save; permission-denied hides the editor.
 *   - UX redesign P3 (page template): header with the shared WindowPicker
 *     (7d/30d/90d/365d) · one-line hero · one strip of five counters · the
 *     monthly chart as the primary content · Model breakdown and Model
 *     assumptions in disclosures under it.
 */
import { cloneElement, createElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { SWRConfig } from 'swr'
import { MemoryRouter, useLocation } from 'react-router-dom'

import type { TeamValueMetrics, ValueMetrics, ValueMetricsMonthly } from '@/types/valueMetrics'
import { useTimeWindowStore } from '@/store/timeWindowStore'

// ── mocks ───────────────────────────────────────────────────────────────────

const mockGet = vi.fn()
const mockGetMethodology = vi.fn()
const mockPutAssumptions = vi.fn()
const mockDownloadExport = vi.fn()
const mockExportErrorMessage = vi.fn()

vi.mock('@/services/valueMetricsService', () => ({
  valueMetricsService: {
    get: (...a: unknown[]) => mockGet(...a),
    getMethodology: (...a: unknown[]) => mockGetMethodology(...a),
    putAssumptions: (...a: unknown[]) => mockPutAssumptions(...a),
    downloadExport: (...a: unknown[]) => mockDownloadExport(...a),
    exportErrorMessage: (...a: unknown[]) => mockExportErrorMessage(...a),
  },
}))

let mockCanManage = true
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ canAccessManagement: mockCanManage }),
}))

// W08 TRND-08: the By team read. The spy records every call, so a test can
// prove Overview asks nothing (`enabled` false) and By team asks for one project.
const team = vi.hoisted(() => ({
  answer: { teams: null, malformed: false, isLoading: false, error: undefined } as {
    teams: unknown
    malformed: boolean
    isLoading: boolean
    error: unknown
  },
  calls: [] as unknown[][],
}))
vi.mock('@/hooks/useTeamValueMetrics', () => ({
  TEAM_VALUE_KEY: 'value-metrics-by-team',
  useTeamValueMetrics: (...args: unknown[]) => {
    team.calls.push(args)
    const enabled = args[2] === true && Boolean(args[0])
    return enabled ? { ...team.answer, refresh: vi.fn() } : { teams: null, malformed: false, isLoading: false, error: undefined, refresh: vi.fn() }
  },
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

// The monthly chart is the kit's StackedColumnChartFrame (VIZ-104), drawn by
// real Recharts. jsdom lays nothing out, so the ResponsiveContainer hands its
// chart a fixed size and the columns, legend and patterns really draw. (This
// file used to replace recharts with a fixed list of eight stand-ins, which
// the kit's own imports would have broken.)
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }) =>
      isValidElement(children)
        ? cloneElement(children as ReactElement<{ width?: number; height?: number }>, { width: 640, height: 260 })
        : null,
  }
})

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

/** Open a disclosure by its title (its button also reads the summary). */
async function openDisclosure(title: string) {
  const button = await screen.findByRole('button', { name: new RegExp(`^${title}`) })
  expect(button).toHaveAttribute('aria-expanded', 'false')
  fireEvent.click(button)
  expect(button).toHaveAttribute('aria-expanded', 'true')
  return button
}

/** A counter card in the strip, by its title. */
function counterCard(title: string): HTMLElement {
  return screen.getByText(title).closest('[data-metric-card]') as HTMLElement
}

// The page's module graph (its charts included) loads once, here, with a
// budget of its own. Imported inside the first test it counted against that
// test's 15 s, and under the push gate's load the first test timed out with
// nothing wrong (twice in #217's gate, again on 2026-10-08).
let ValueMetricsPage: (typeof import('./ValueMetricsPage'))['default']
beforeAll(async () => {
  ;({ default: ValueMetricsPage } = await import('./ValueMetricsPage'))
}, 60_000)

/** W08: the router's location, so a test can read the `?view=` the switch writes. */
function Location() {
  const location = useLocation()
  return createElement('output', { 'data-testid': 'location' }, `${location.pathname}${location.search}`)
}

async function renderPage(path = '/value-metrics') {
  return render(
    createElement(
      MemoryRouter,
      { initialEntries: [path] },
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
        createElement(Location),
      ),
    ),
  )
}

describe('ValueMetricsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // A disclosure remembers being opened (`persistKey`): start each test closed.
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const key = localStorage.key(i)
      if (key?.startsWith('tl.disclosure.')) localStorage.removeItem(key)
    }
    useTimeWindowStore.setState({ days: 30 })
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

  // UX P2: the "Value Realization Workflow" ribbon showed invented stages
  // (and named the product "RunScope AI"); the legacy "Triage Time Saved"
  // hero duplicated the hours-saved headline with a second, simpler model.
  it('renders neither the decorative workflow ribbon nor the legacy triage-time hero', async () => {
    await renderPage()

    expect(await screen.findByRole('heading', { name: 'Value Metrics' })).toBeInTheDocument()
    await screen.findByText(/Eng-Hours Saved · Last 30 Days/i)
    expect(screen.queryByText(/Value Realization Workflow/i)).toBeNull()
    expect(screen.queryByText(/Value Capture/i)).toBeNull()
    expect(screen.queryByText(/RunScope AI/i)).toBeNull()
    expect(screen.queryByText(/Triage Time Saved/i)).toBeNull()
    // baseMetrics: triage_time_saved_minutes 360 → the legacy hero's line.
    expect(screen.queryByText(/minutes saved over the last/i)).toBeNull()
  })

  // TRND-05: the headline used to be a fixed trailing 30 days whatever the
  // picker said (and a note admitted it). It now follows the window.
  it('follows the window picker: the headline and its label are the picked window', async () => {
    mockGet.mockImplementation(async (_projectId: string, days: number) =>
      baseMetrics({
        period_days: days,
        headline: {
          hours_saved: days === 90 ? 127.5 : 42.5,
          fte_equivalent: days === 90 ? 0.25 : 0.8,
          window_days: days,
          hours_saved_30d: 42.5,
          fte_equivalent_30d: 0.8,
        },
      }),
    )
    await renderPage()

    await screen.findByText('42.5h')
    expect(screen.getByTestId('hours-saved-window')).toHaveTextContent('Eng-hours saved · last 30 days')
    expect(screen.queryByText(/window picker does not change it/i)).toBeNull()

    fireEvent.click(screen.getByRole('radio', { name: '90d' }))
    expect(await screen.findByText('128h')).toBeInTheDocument()
    expect(screen.getByTestId('hours-saved-window')).toHaveTextContent('Eng-hours saved · last 90 days')
    expect(screen.getByText(/≈ 0\.3 FTE/)).toBeInTheDocument()
    expect(screen.queryByText('42.5h')).toBeNull()
  })

  it('WIN-01: clicking 14d asks for 14 days and the headline says the last 14 days', async () => {
    mockGet.mockImplementation(async (_projectId: string, days: number) =>
      baseMetrics({
        period_days: days,
        headline: {
          hours_saved: days === 14 ? 20 : 42.5,
          fte_equivalent: 0.8,
          window_days: days,
          hours_saved_30d: 42.5,
          fte_equivalent_30d: 0.8,
        },
      }),
    )
    await renderPage()
    await screen.findByText('42.5h')

    fireEvent.click(screen.getByRole('radio', { name: '14d' }))

    await waitFor(() => expect(mockGet.mock.calls.some((call) => call[1] === 14)).toBe(true))
    await waitFor(() => expect(screen.getByTestId('hours-saved-window')).toHaveTextContent('Eng-hours saved · last 14 days'))
    expect(useTimeWindowStore.getState().days).toBe(14)
  })

  it('WIN-01: a stored 14d opens as 14d and stays 14 (no snap to 7)', async () => {
    useTimeWindowStore.setState({ days: 14 })
    await renderPage()
    await screen.findByTestId('monthly-chart')
    expect(mockGet.mock.calls.some((call) => call[1] === 14)).toBe(true)
    expect(mockGet.mock.calls.some((call) => call[1] === 7)).toBe(false)
    expect(screen.getByRole('radio', { name: '14d' })).toHaveAttribute('aria-checked', 'true')
    expect(useTimeWindowStore.getState().days).toBe(14)
  })

  it('labels a payload without window_days as the trailing 30 days, whatever the picker says', async () => {
    // baseMetrics() carries only the 30-day pair (a pre-TRND-05 payload).
    useTimeWindowStore.setState({ days: 90 })
    await renderPage()

    expect(await screen.findByText('42.5h')).toBeInTheDocument()
    expect(screen.getByTestId('hours-saved-window')).toHaveTextContent('Eng-hours saved · last 30 days')
  })

  it('renders the hours-saved headline + FTE equivalent when available', async () => {
    await renderPage()

    expect(await screen.findByText(/Eng-Hours Saved · Last 30 Days/i)).toBeInTheDocument()
    expect(screen.getByText('42.5h')).toBeInTheDocument()
    expect(screen.getByText(/≈ 0\.8 FTE/)).toBeInTheDocument()
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

  it('W27b: the methodology opens IN THE PAGE right under the hero, read once and cached on the next open', async () => {
    await renderPage()
    expect(mockGetMethodology).not.toHaveBeenCalled()

    fireEvent.click(await screen.findByText('How is this calculated?'))
    const section = await screen.findByRole('region', { name: 'How hours saved is calculated' })
    expect(document.querySelector('[data-value-hero]')?.nextElementSibling).toBe(section)
    expect(document.querySelector('[aria-modal]')).toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(within(section).getByRole('heading', { name: 'How is this calculated?' })).toBeInTheDocument()
    expect(await within(section).findByText('Auto-triage leg')).toBeInTheDocument()
    // The text Close button, no X.
    expect(within(section).getAllByRole('button').map((b) => b.textContent)).toEqual(['Close'])
    expect(mockGetMethodology).toHaveBeenCalledTimes(1)

    fireEvent.click(within(section).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('region', { name: 'How hours saved is calculated' })).toBeNull()
    // The hero's figure reopens it from the cache: the legs are there at once.
    fireEvent.click(screen.getByTitle('How is this calculated?'))
    expect(screen.getByRole('region', { name: 'How hours saved is calculated' })).toHaveTextContent('Auto-triage leg')
  })

  it('passes the ascending monthly series to the chart verbatim', async () => {
    await renderPage()

    const chart = await screen.findByTestId('monthly-chart')
    // The PLOT draws the months in the order the API sent them (ascending):
    // the table view sorts a time axis itself, so it cannot prove this.
    const ticks = Array.from(chart.querySelectorAll('.recharts-xAxis-tick-labels .recharts-cartesian-axis-tick-value'), (el) => el.textContent)
    expect(ticks).toEqual(['2026-05', '2026-06', '2026-07'])
    fireEvent.click(within(chart).getByRole('button', { name: 'View as table' }))
    const table = within(chart).getByRole('table', { name: /data table/i })
    // One row per month.
    expect(within(table).getAllByRole('rowheader').map((th) => th.textContent)).toEqual(['2026-05', '2026-06', '2026-07'])
    expect(within(table).getAllByRole('columnheader').map((th) => th.textContent)).toEqual([
      'Month', 'Triage', 'Quarantine', 'Duplicate absorption', 'Total',
    ])
    const first = within(table).getByRole('rowheader', { name: '2026-05' }).parentElement as HTMLElement
    // month('2026-05-01', 0): triage 8, quarantine 4, dedup 1 — and their sum.
    expect(Array.from(first.querySelectorAll('td'), (td) => td.textContent)).toEqual(['8 h', '4 h', '1 h', '13 h'])
  })

  it('draws the three model legs in series colours, not in borrowed status colours', async () => {
    await renderPage()

    const chart = await screen.findByTestId('monthly-chart')
    expect(within(chart).getByRole('heading', { level: 3, name: 'Hours saved per month' })).toBeInTheDocument()
    const legend = Array.from(chart.querySelectorAll('[data-chart-legend] li'), (li) => li.textContent)
    expect(legend).toEqual(['Triage', 'Quarantine', 'Duplicate absorption'])
    const colours = Array.from(chart.querySelectorAll('pattern[id*="-chart-pattern-series-"] > rect:first-child'), (rect) =>
      rect.getAttribute('fill'),
    )
    expect(colours).toEqual(['var(--chart-series-1)', 'var(--chart-series-2)', 'var(--chart-series-3)'])
  })

  it('shows a leg the payload does not carry as "—", never as 0 hours', async () => {
    const partial = { ...month('2026-05-01', 0), hours_dedup: undefined as unknown as number }
    mockGet.mockResolvedValue(baseMetrics({ monthly: [partial, month('2026-06-01', 1)] }))
    await renderPage()

    const chart = await screen.findByTestId('monthly-chart')
    fireEvent.click(within(chart).getByRole('button', { name: 'View as table' }))
    const row = within(chart).getByRole('rowheader', { name: '2026-05' }).parentElement as HTMLElement
    expect(Array.from(row.querySelectorAll('td'), (td) => td.textContent)).toEqual(['8 h', '4 h', '—', '12 h'])
  })

  it('renders the honest model-count labels (duplicates absorbed, runs unblocked estimate)', async () => {
    await renderPage()

    await openDisclosure('Model breakdown')
    expect(await screen.findByText(/Duplicate failures absorbed/i)).toBeInTheDocument()
    expect(screen.getByText(/Runs unblocked \(estimate\)/i)).toBeInTheDocument()
  })

  it('PUTs only the changed assumption fields', async () => {
    await renderPage()

    await openDisclosure('Model assumptions')
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

    await openDisclosure('Model assumptions')
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

    // The badge is the disclosure's summary: it reads while the editor is closed.
    expect(await screen.findByText('Defaults')).toBeInTheDocument()
    await openDisclosure('Model assumptions')

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

  // ── UX redesign P3: the page template ─────────────────────────────────────

  it('lays the page out as the template: hero, one strip of five counters, then the monthly chart before every disclosure', async () => {
    const { container } = await renderPage()

    const chart = await screen.findByTestId('monthly-chart')
    const primary = container.querySelector('[data-primary]') as HTMLElement
    expect(primary).not.toBeNull()
    expect(primary.contains(chart)).toBe(true)
    // Above it: the hero, then the strip of five compact counters.
    const hero = container.querySelector('[data-value-hero]') as HTMLElement
    const strip = container.querySelector('[data-kpi-strip]') as HTMLElement
    expect(strip.querySelectorAll('[data-metric-card="compact"]')).toHaveLength(5)
    expect(hero.compareDocumentPosition(strip) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(strip.compareDocumentPosition(primary) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // Every secondary section (no tab bar here; two disclosures) comes after it.
    expect(container.querySelectorAll('[role="tablist"]')).toHaveLength(0)
    const disclosures = Array.from(container.querySelectorAll('[data-disclosure]'))
    expect(disclosures.map((d) => d.querySelector('button')?.textContent)).toEqual([
      'Model breakdownlast 3 months',
      'Model assumptionsDefaults',
    ])
    for (const d of disclosures) expect(primary.compareDocumentPosition(d) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // The page's block gap is the template's (space-y-4).
    expect((primary.parentElement as HTMLElement).className.split(/\s+/)).toContain('space-y-4')
  })

  it('folds "Defects promoted" into Defects grouped and drops the derived "Release decisions" (seven cards → five)', async () => {
    await renderPage()

    await screen.findByTestId('monthly-chart')
    expect(within(counterCard('Defects grouped')).getByText('12')).toBeInTheDocument()
    expect(counterCard('Defects grouped')).toHaveTextContent('44 tests · 9 promoted to Jira')
    expect(within(counterCard('Duplicates avoided')).getByText('5')).toBeInTheDocument()
    expect(counterCard('Flaky tests found')).toHaveTextContent('2 recommended for quarantine')
    // Blocked and conditional are both on this card: their sum needs no card of its own.
    expect(counterCard('Releases blocked')).toHaveTextContent('1 conditional · 0 overridden')
    expect(within(counterCard('AI reports')).getByText('7')).toBeInTheDocument()
    expect(screen.queryByText(/^Release Decisions$/i)).toBeNull()
    expect(screen.queryByText(/^Defects Promoted$/i)).toBeNull()
  })

  it('shows an empty counter as "—", never as a stark 0', async () => {
    mockGet.mockResolvedValue(baseMetrics({ duplicate_tickets_avoided: 0 }))
    await renderPage()

    await screen.findByTestId('monthly-chart')
    expect(within(counterCard('Duplicates avoided')).getByText('—')).toBeInTheDocument()
    expect(within(counterCard('Duplicates avoided')).queryByText('0')).toBeNull()
  })

  it('keeps Model breakdown collapsed until it is opened, and closes it again', async () => {
    await renderPage()

    await screen.findByTestId('monthly-chart')
    expect(screen.queryByTestId('model-breakdown')).toBeNull()
    const button = await openDisclosure('Model breakdown')
    const breakdown = screen.getByTestId('model-breakdown')
    // The three months summed: auto_triaged 10 + 11 + 12.
    expect(within(breakdown.querySelector('[data-metric-card]') as HTMLElement).getByText('33')).toBeInTheDocument()
    expect(within(breakdown).getByText('Proxy estimate')).toBeInTheDocument()
    fireEvent.click(button)
    expect(screen.queryByTestId('model-breakdown')).toBeNull()
  })

  it('keeps the assumptions editor collapsed until it is opened, and closes it again', async () => {
    await renderPage()

    await screen.findByTestId('monthly-chart')
    expect(screen.queryByTestId('assumptions-editor')).toBeNull()
    const button = await openDisclosure('Model assumptions')
    expect(screen.getByTestId('assumptions-editor')).toBeInTheDocument()
    expect(screen.getByLabelText('Defect filing minutes')).toHaveValue(10)
    fireEvent.click(button)
    expect(screen.queryByTestId('assumptions-editor')).toBeNull()
  })

  it('offers 7d / 14d / 30d / 90d / 365d in the shared window picker, and a year asks for 365 days', async () => {
    await renderPage()

    await screen.findByTestId('monthly-chart')
    const picker = screen.getByRole('radiogroup', { name: 'Time window' })
    expect(within(picker).getAllByRole('radio').map((r) => r.textContent)).toEqual(['7d', '14d', '30d', '90d', '365d'])
    expect(within(picker).getByRole('radio', { name: '30d' })).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(within(picker).getByRole('radio', { name: '365d' }))
    await waitFor(() => expect(mockGet.mock.calls.some((call) => call[1] === 365)).toBe(true))
    expect(useTimeWindowStore.getState().days).toBe(365)
    // Export stays the header's one secondary action: a button now (HON-04), not a bare link.
    expect(screen.getByTestId('value-metrics-export').tagName).toBe('BUTTON')
    expect(screen.queryByRole('link', { name: /Export/ })).toBeNull()
  })

  // HON-04: a bare `<a href download>` navigation carried no Bearer token and
  // saved the API's 401 body; the export now runs through the service (axios).
  it('exports the selected project and window through the service, not a bare link', async () => {
    mockDownloadExport.mockResolvedValue(undefined)
    await renderPage()

    await screen.findByTestId('monthly-chart')
    await act(async () => {
      fireEvent.click(screen.getByTestId('value-metrics-export'))
    })
    expect(mockDownloadExport).toHaveBeenCalledWith('proj-1', 30)

    fireEvent.click(screen.getByRole('radio', { name: '90d' }))
    await act(async () => {
      fireEvent.click(screen.getByTestId('value-metrics-export'))
    })
    expect(mockDownloadExport).toHaveBeenLastCalledWith('proj-1', 90)
    const toast = (await import('react-hot-toast')).default
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('toasts the API refusal when the export fails, and re-enables the button', async () => {
    const refusal = new Error('Request failed with status code 403')
    mockDownloadExport.mockRejectedValue(refusal)
    mockExportErrorMessage.mockResolvedValue('You do not have access to this project')
    await renderPage()

    await screen.findByTestId('monthly-chart')
    await act(async () => {
      fireEvent.click(screen.getByTestId('value-metrics-export'))
    })
    const toast = (await import('react-hot-toast')).default
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Export failed: You do not have access to this project'))
    expect(mockExportErrorMessage).toHaveBeenCalledWith(refusal)
    expect(screen.getByTestId('value-metrics-export')).not.toBeDisabled()
  })

  it('says once that nothing has fired yet, with what fills each counter behind a disclosure', async () => {
    mockGet.mockResolvedValue(baseMetrics({
      defects_auto_grouped: 0,
      duplicate_tickets_avoided: 0,
      defects_promoted: 0,
      flaky_tests_identified: 0,
      risky_releases_blocked: 0,
      releases_conditional: 0,
      release_overrides: 0,
    }))
    await renderPage()

    await screen.findByTestId('monthly-chart')
    expect(screen.queryByText(/the Flaky Coach scans history/)).toBeNull()
    await openDisclosure('No value-generating activity yet for this window')
    expect(screen.getByText(/the Flaky Coach scans history/)).toBeInTheDocument()
  })
})

// ── W08 TRND-08: Overview · By team ─────────────────────────────────────────

function teamAnswer(overrides: Partial<TeamValueMetrics> = {}): TeamValueMetrics {
  const row = (name: string, hours: number, extra: Partial<TeamValueMetrics['teams'][number]> = {}) => ({
    team: name,
    tests: 10,
    executions: 40,
    evaluated: 36,
    passed: 30,
    pass_rate: 83.3,
    flaky_tests: 1,
    defects: 2,
    mttr_hours: 5.5,
    hours_saved: hours,
    hours: { triage: hours / 2, dedup: hours / 2 },
    ...extra,
  })
  return {
    window_days: 30,
    measured: true,
    reason: null,
    rules_configured: true,
    // The server sends Unassigned last; the view holds it last even when it is not.
    teams: [row('Unassigned', 1, { pass_rate: null, mttr_hours: null }), row('Payments', 6), row('Identity <b>', 3)],
    not_attributable_hours: 2,
    headline_hours: 12,
    ...overrides,
  }
}

describe('ValueMetricsPage — By team (W08 TRND-08)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useTimeWindowStore.setState({ days: 30 })
    mockCanManage = true
    storeState.activeProjectId = 'proj-1'
    storeState.activeProject = { id: 'proj-1', name: 'Project One' }
    mockGet.mockResolvedValue(baseMetrics())
    team.calls = []
    team.answer = { teams: teamAnswer(), malformed: false, isLoading: false, error: undefined }
  })

  const view = () => within(screen.getByRole('radiogroup', { name: 'View' }))

  it('Overview is the default and asks for no by-team read; the switch writes ?view=team (replaced)', async () => {
    await renderPage()
    await screen.findByText(/Eng-Hours Saved · Last 30 Days/i)
    expect(view().getByRole('radio', { name: 'Overview' })).toHaveAttribute('aria-checked', 'true')
    expect(team.calls).toHaveLength(0)
    fireEvent.click(view().getByRole('radio', { name: 'By team' }))
    expect(screen.getByTestId('location')).toHaveTextContent('/value-metrics?view=team')
    expect(view().getByRole('radio', { name: 'By team' })).toHaveAttribute('aria-checked', 'true')
    expect(team.calls[team.calls.length - 1]).toEqual(['proj-1', 30, true])
    fireEvent.click(view().getByRole('radio', { name: 'Overview' }))
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/value-metrics$/)
    await screen.findByText(/Eng-Hours Saved · Last 30 Days/i)
  })

  it('any other ?view= value is Overview', async () => {
    await renderPage('/value-metrics?view=teams')
    await screen.findByText(/Eng-Hours Saved · Last 30 Days/i)
    expect(team.calls).toHaveLength(0)
  })

  it('By team draws one row per team, Unassigned last, and the two totals; the header and the window picker stay', async () => {
    await renderPage('/value-metrics?view=team')
    const table = await screen.findByRole('table')
    const teams = within(table).getAllByRole('row').slice(1).map((r) => within(r).getAllByRole('cell')[0].textContent)
    expect(teams).toEqual(['Payments', 'Identity <b>', 'Unassigned'])
    const unassigned = within(table).getAllByRole('row')[3]
    // Not measured is "—", never 0.
    expect(within(unassigned).getAllByRole('cell').map((c) => c.textContent)).toEqual(['Unassigned', '10', '—', '1', '2', '—', '1 h'])
    expect(screen.getByText(/Not attributable to a team: 2 h \(runs unblocked by quarantine are counted per run\)/)).toBeInTheDocument()
    expect(screen.getByText('Total 12 h — the headline for these 30 days')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Value Metrics' })).toBeInTheDocument()
    expect(screen.getByRole('radiogroup', { name: 'Time window' })).toBeInTheDocument()
    expect(screen.queryByText(/Eng-Hours Saved/i)).toBeNull()
  })

  it('a QA lead gets team links to /ownership?team=…; a viewer reads the names as text; Unassigned never links', async () => {
    await renderPage('/value-metrics?view=team')
    const table = await screen.findByRole('table')
    expect(within(table).getByRole('link', { name: 'Payments' })).toHaveAttribute('href', '/ownership?team=Payments')
    // A hostile name is text, and only an encoded query value in the link.
    expect(within(table).getByRole('link', { name: 'Identity <b>' })).toHaveAttribute('href', '/ownership?team=Identity%20%3Cb%3E')
    expect(within(table).queryByRole('link', { name: 'Unassigned' })).toBeNull()
    expect(table.querySelector('b')).toBeNull()
  })

  it('a viewer sees team names as plain text', async () => {
    mockCanManage = false
    await renderPage('/value-metrics?view=team')
    const table = await screen.findByRole('table')
    expect(within(table).queryAllByRole('link')).toHaveLength(0)
    expect(within(table).getByText('Payments')).toBeInTheDocument()
  })

  it('no ownership rules → the empty state (QA lead: Set up ownership; others: ask a QA lead)', async () => {
    team.answer = {
      teams: teamAnswer({ rules_configured: false, measured: false, reason: 'No ownership rules yet.', teams: [] }),
      malformed: false,
      isLoading: false,
      error: undefined,
    }
    const { unmount } = await renderPage('/value-metrics?view=team')
    expect(await screen.findByText('No ownership rules yet')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Set up ownership' })).toHaveAttribute('href', '/ownership')
    unmount()
    mockCanManage = false
    await renderPage('/value-metrics?view=team')
    expect(await screen.findByText('Ask a QA lead to set up ownership rules.')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Set up ownership' })).toBeNull()
  })

  it('a malformed answer says "Value by team is not available" and keeps the header', async () => {
    team.answer = { teams: null, malformed: true, isLoading: false, error: undefined }
    await renderPage('/value-metrics?view=team')
    expect(await screen.findByText('Value by team is not available')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Value Metrics' })).toBeInTheDocument()
    expect(screen.getByRole('radiogroup', { name: 'View' })).toBeInTheDocument()
  })

  it('All Projects asks nothing and says to pick a project', async () => {
    const { ALL_PROJECTS_ID } = await import('@/store/projectStore')
    storeState.activeProjectId = ALL_PROJECTS_ID
    storeState.activeProject = null
    await renderPage('/value-metrics?view=team')
    expect(await screen.findByText('Pick a project to see value by team')).toBeInTheDocument()
    expect(team.calls.every((args) => args[2] === false || !args[0])).toBe(true)
  })
})
