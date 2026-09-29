import { render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DefectsPage from './DefectsPage'
import { useReleaseStore } from '@/store/releaseStore'

// Mock every useMetrics export — the page (or its widgets) may import any
// of them and vitest errors on an undefined export. Tests override the
// specific hook(s) they care about via ``mockReturnValue`` further down.
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
// Default: settings not yet loaded. Tests that care set a config.
type IntegrationsHookResult = {
  config: Partial<import('@/services/appSettingsService').IntegrationsConfigRead> | undefined
  error: unknown
  isLoading: boolean
}
const mockIntegrationsConfig = vi.fn<() => IntegrationsHookResult>(
  () => ({ config: undefined, error: undefined, isLoading: true }),
)
vi.mock('@/hooks/useIntegrationsConfig', async (importOriginal) => {
  // Keep the real jiraBridgeState — the mapping from config to badge is the
  // thing under test.
  const actual = await importOriginal<typeof import('@/hooks/useIntegrationsConfig')>()
  return { ...actual, useIntegrationsConfig: () => mockIntegrationsConfig() }
})
vi.mock('@/hooks/useSuiteOptions', () => ({
  useSuiteOptions: () => ({ options: [], isLoading: false }),
}))
const analyticsControls = vi.hoisted(() => ({
  widgetIds: ['defect_kpis', 'defect_category_bar'],
}))

vi.mock('@/hooks/useAnalyticsView', () => ({
  useAnalyticsView: () => ({
    instances: [], widgetIds: analyticsControls.widgetIds, addInstance: vi.fn(), removeInstance: vi.fn(),
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

describe('DefectsPage', () => {
  beforeEach(() => {
    analyticsControls.widgetIds = ['defect_kpis', 'defect_category_bar']
  })

  it('renders the defect workflow strip above the defect table', async () => {
    const { useDefects } = await import('@/hooks/useMetrics')

    ;(useDefects as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        items: [
          {
            id: 'def-1',
            test_name: 'payments should process a charge',
            suite_name: 'Payments',
            resolution_status: 'OPEN',
            ai_confidence_score: 84,
            created_at: '2026-04-01T10:00:00Z',
          },
        ],
        total: 1,
        pages: 1,
      },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/defects']}>
        <Routes>
          <Route path="/defects" element={<DefectsPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/Defect Workflow/i)).toBeInTheDocument()
    expect(screen.getByText(/Defect Intake/i)).toBeInTheDocument()
    // ``Defects`` appears in multiple places (page header, table caption,
    // KPI labels) — use ``getAllByText`` to assert presence without
    // tying to a specific surface.
    expect(screen.getAllByText(/Defects/i).length).toBeGreaterThan(0)
  })

  it('removes the category panel when its saved widget is deselected', async () => {
    const { useDefects } = await import('@/hooks/useMetrics')
    analyticsControls.widgetIds = ['defect_kpis']
    ;(useDefects as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        items: [{
          id: 'def-1', test_name: 'payment fails', suite_name: 'Payments',
          resolution_status: 'OPEN', ai_confidence_score: 80,
          created_at: '2026-04-01T10:00:00Z',
        }],
        total: 1,
        pages: 1,
      },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/defects']}>
        <Routes><Route path="/defects" element={<DefectsPage />} /></Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: 'Defects' })).toBeInTheDocument()
    expect(screen.queryByText('Where defects live')).not.toBeInTheDocument()
  })

  // Regression: the Jira bridge card was static chrome. It rendered a green
  // "Connected" badge, the literal host `jira`, "Last sync: just now" and
  // "Webhook v2 · auto-link enabled" without ever reading integration state.
  // On a workspace with jira_enabled=false and no credential, the page told
  // the user their defects were syncing to a Jira that did not exist.
  const renderWithDefects = () => render(
    <MemoryRouter initialEntries={['/defects']}>
      <Routes>
        <Route path="/defects" element={<DefectsPage />} />
      </Routes>
    </MemoryRouter>,
  )

  it('does not claim a Jira connection when none is configured', async () => {
    const { useDefects } = await import('@/hooks/useMetrics')
    ;(useDefects as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [], total: 0, pages: 0 }, isLoading: false,
    })
    mockIntegrationsConfig.mockReturnValue({
      config: {
        jira_enabled: false, jira_domain: null, jira_email: null,
        jira_token_set: false, jira_default_project_key: 'QA',
      },
      error: undefined, isLoading: false,
    })

    renderWithDefects()

    expect(await screen.findByText('Not configured')).toBeInTheDocument()
    expect(screen.queryByText('Connected')).not.toBeInTheDocument()
    // The fabricated telemetry is gone with it.
    expect(screen.queryByText(/Last sync/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Webhook v2/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Sync latency/i)).not.toBeInTheDocument()
  })

  it('reports unknown rather than connected when it cannot read settings', async () => {
    const { useDefects } = await import('@/hooks/useMetrics')
    ;(useDefects as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [], total: 0, pages: 0 }, isLoading: false,
    })
    // /settings/integrations needs QA_LEAD; a developer gets a 403.
    mockIntegrationsConfig.mockReturnValue({
      config: undefined, error: new Error('403'), isLoading: false,
    })

    renderWithDefects()

    expect(await screen.findByText('Unknown')).toBeInTheDocument()
    expect(screen.queryByText('Connected')).not.toBeInTheDocument()
  })

  it('says connected only with an enabled bridge that has a host and a credential', async () => {
    const { useDefects } = await import('@/hooks/useMetrics')
    ;(useDefects as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [], total: 0, pages: 0 }, isLoading: false,
    })
    mockIntegrationsConfig.mockReturnValue({
      config: {
        jira_enabled: true, jira_domain: 'acme.atlassian.net', jira_email: 'qa@acme.test',
        jira_token_set: true, jira_default_project_key: 'ACME',
      },
      error: undefined, isLoading: false,
    })

    renderWithDefects()

    expect(await screen.findByText('Connected')).toBeInTheDocument()
    // The host is the configured one, not the literal 'jira'.
    expect(screen.getByText('acme.atlassian.net')).toBeInTheDocument()
  })

  it('will not call an enabled-but-unreachable bridge connected', async () => {
    const { useDefects } = await import('@/hooks/useMetrics')
    const { jiraBridgeState } = await import('@/hooks/useIntegrationsConfig')
    ;(useDefects as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [], total: 0, pages: 0 }, isLoading: false,
    })
    // The toggle is on but there is no host and no credential — a bridge that
    // cannot reach anything.
    const halfConfigured = {
      jira_enabled: true, jira_domain: null, jira_email: null,
      jira_token_set: false, jira_default_project_key: 'QA',
    }
    expect(jiraBridgeState(halfConfigured)).toBe('not_configured')
    expect(jiraBridgeState(undefined)).toBe('unknown')

    mockIntegrationsConfig.mockReturnValue({ config: halfConfigured, error: undefined, isLoading: false })
    renderWithDefects()
    expect(await screen.findByText('Not configured')).toBeInTheDocument()
  })
})

describe('DefectsPage — saying that the release filter does not apply', () => {
  /**
   * The page's own subtitle reads "N P0 blocking release". With a release
   * picked in the header, that sentence reads as "blocking THIS release" — and
   * the numbers are the project's: `/analytics/defects` accepts no release_id,
   * and a defect raised against an earlier release can still be open now.
   */
  function renderPage() {
    return render(
      <MemoryRouter initialEntries={['/defects']}>
        <Routes>
          <Route path="/defects" element={<DefectsPage />} />
        </Routes>
      </MemoryRouter>,
    )
  }

  it('marks the page when a release is selected', async () => {
    useReleaseStore.setState({ activeReleaseId: 'rel-1', scopedProjectId: 'proj-1' })
    renderPage()

    expect(await screen.findByText('All releases')).toBeInTheDocument()
  })

  it('explains why, rather than only flagging it', async () => {
    useReleaseStore.setState({ activeReleaseId: 'rel-1', scopedProjectId: 'proj-1' })
    renderPage()

    const badge = await screen.findByText('All releases')
    expect(badge.getAttribute('title')).toMatch(/tracked per project, not per release/)
  })

  it('stays silent when no release is selected', async () => {
    // The control, and the badge's whole design: with no release chosen there
    // is no discrepancy to explain, and the page looks exactly as it did
    // before the release axis existed. A badge that always rendered would pass
    // the two tests above and become furniture.
    useReleaseStore.setState({ activeReleaseId: null, scopedProjectId: null })
    renderPage()

    await screen.findByText(/Defect Workflow/i)
    expect(screen.queryByText('All releases')).toBeNull()
  })
})

// ── Wave 2.5 (VIZ-104, OD-1): Defects has no defect history, so four KPI
// glyphs that drew literal point strings are gone; "Oldest open P0" is a real
// number of days and keeps a gauge; the queue meter is the kit GaugeBar. ────
describe('DefectsPage — KPI glyphs draw only real numbers', () => {
  const DAY = 86_400_000
  const ago = (days: number) => new Date(Date.now() - days * DAY - 3_600_000).toISOString()

  async function renderWith(items: Record<string, unknown>[]) {
    const { useDefects } = await import('@/hooks/useMetrics')
    ;(useDefects as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items, total: items.length, pages: 1 },
      isLoading: false,
    })
    render(
      <MemoryRouter initialEntries={['/defects']}>
        <Routes>
          <Route path="/defects" element={<DefectsPage />} />
        </Routes>
      </MemoryRouter>,
    )
    return screen.findByRole('region', { name: 'Defect KPIs' })
  }

  /** A KPI cell by its label: the label sits in the cell's first row. */
  function kpiCell(kpis: HTMLElement, label: string): HTMLElement {
    const cell = within(kpis).getByText(label).closest('div')?.parentElement
    if (!cell) throw new Error(`no KPI cell labelled ${label}`)
    return cell
  }

  const p0 = (id: string, days: number) => ({
    id,
    test_name: `checkout ${id}`,
    suite_name: 'Payments',
    failure_category: 'PRODUCT_BUG',
    ai_confidence_score: 92,
    resolution_status: 'OPEN',
    created_at: ago(days),
  })

  it('deletes the four hard-coded glyphs', async () => {
    const kpis = await renderWith([p0('d1', 4)])
    for (const label of ['Open defects', 'P0 / P1 open', 'Mean time to resolve', 'Escape rate']) {
      const cell = kpiCell(kpis, label)
      expect(cell.querySelector('svg:not(.lucide)'), label).toBeNull()
      expect(cell.querySelector('polyline'), label).toBeNull()
      expect(within(cell).queryByRole('meter'), label).toBeNull()
      expect(within(cell).queryByRole('img'), label).toBeNull()
    }
  })

  it('gauges the oldest open P0 in days on a one-week scale, toned by the KPI bands', async () => {
    const kpis = await renderWith([p0('d1', 4), p0('d2', 1)])
    const meter = within(kpiCell(kpis, 'Oldest open P0')).getByRole('meter')
    expect(meter).toHaveAttribute('aria-valuenow', '4')
    expect(meter).toHaveAttribute('aria-valuemax', '7')
    expect(meter).toHaveAttribute('aria-valuetext', '4 days open')
    expect(meter).toHaveAttribute('data-tone', 'warn')
  })

  it.each([
    [1, 'good'],
    [6, 'bad'],
  ])('tones an oldest P0 open %i day(s) as %s — the KPI\'s bands, lower is better', async (days, tone) => {
    const kpis = await renderWith([p0('d1', days)])
    expect(within(kpiCell(kpis, 'Oldest open P0')).getByRole('meter')).toHaveAttribute('data-tone', tone)
  })

  it('draws no oldest-P0 bar when no P0 is open — not a bar at 0', async () => {
    const kpis = await renderWith([{ ...p0('d1', 4), failure_category: 'FLAKY' }])
    const cell = kpiCell(kpis, 'Oldest open P0')
    expect(within(cell).queryByRole('meter')).toBeNull()
    expect(within(cell).queryByRole('img')).toBeNull()
    expect(cell).toHaveTextContent('no open P0 in window')
  })

  it('draws the queue health as a named meter', async () => {
    await renderWith([p0('d1', 4)])
    const meter = screen.getByRole('meter', { name: 'Queue health' })
    expect(meter.getAttribute('aria-valuetext')).toMatch(/^\d+ of 100, (Healthy|At risk|Blocked)$/)
  })
})
