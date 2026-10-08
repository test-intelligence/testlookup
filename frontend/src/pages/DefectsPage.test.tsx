import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DefectsPage from './DefectsPage'
import { useReleaseStore } from '@/store/releaseStore'

// Mock every useMetrics export — the page (or its widgets) may import any
// of them and vitest errors on an undefined export. Tests override the
// specific hook(s) they care about via ``mockReturnValue`` further down.
// P1: the header's Views menu reads this page's saved views (none here).
vi.mock('@/services/savedViewsService', () => ({
  listSavedViews: vi.fn(async () => []),
  createSavedView: vi.fn(),
  updateSavedView: vi.fn(),
  deleteSavedView: vi.fn(),
}))
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
// UX redesign P2: the page no longer reads a saved widget selection. The mock
// stays so the test below can hand it an EMPTY selection and prove that a
// reintroduced gate would be caught (it would hide the KPIs and the panel).
const analyticsControls = vi.hoisted(() => ({
  widgetIds: [] as string[],
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

/** The router's current query string, for the tab-URL tests. */
function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.search}</output>
}

/** Render the page at `/defects<search>`, with the location probe beside it. */
function renderDefects(search = '') {
  return render(
    <MemoryRouter initialEntries={[`/defects${search}`]}>
      <Routes>
        <Route path="/defects" element={<><DefectsPage /><LocationProbe /></>} />
      </Routes>
    </MemoryRouter>,
  )
}

/** Open the "How queue health is computed" Disclosure (it renders nothing while closed). */
function openHealthDetails() {
  fireEvent.click(screen.getByRole('button', { name: /How queue health is computed/ }))
  return screen.getByRole('meter', { name: 'Queue health' }).closest('[data-disclosure]') as HTMLElement
}

describe('DefectsPage', () => {
  beforeEach(() => {
    analyticsControls.widgetIds = []
  })

  it('renders the verdict, the KPIs and the defect table', async () => {
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

    expect(await screen.findByRole('region', { name: 'Defect queue verdict' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Defect KPIs' })).toBeInTheDocument()
    expect(screen.getByText('All defects')).toBeInTheDocument()
    // ``Defects`` appears in multiple places (page header, table caption,
    // KPI labels) — use ``getAllByText`` to assert presence without
    // tying to a specific surface.
    expect(screen.getAllByText(/Defects/i).length).toBeGreaterThan(0)
  })

  it('shows every section whatever a saved widget selection says (no Customize)', async () => {
    // A saved selection used to gate the KPIs and the category panel, and with
    // the picker gone nothing could ever bring a hidden section back.
    const { useDefects } = await import('@/hooks/useMetrics')
    analyticsControls.widgetIds = []
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
    // P3: the component breakdown is the default tab under the table (its card heading; the tab has the same words).
    expect(screen.getByRole('heading', { name: 'Where defects live' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Defect KPIs' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Customize/i })).toBeNull()
  })

  // Regression: the Jira bridge card was static chrome. It rendered a green
  // "Connected" badge, the literal host `jira`, "Last sync: just now" and
  // "Webhook v2 · auto-link enabled" without ever reading integration state.
  // On a workspace with jira_enabled=false and no credential, the page told
  // the user their defects were syncing to a Jira that did not exist.
  // UX redesign P3: the Jira bridge card is the "Jira bridge" tab under the table.
  const renderWithDefects = () => renderDefects('?tab=jira')

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

    await screen.findByRole('heading', { name: 'Defects' })
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

  /** A KPI tile by its label (P3: a compact `MetricCard` of the `KpiStrip`). */
  function kpiCell(kpis: HTMLElement, label: string): HTMLElement {
    const cell = within(kpis).getByText(label).closest('[data-metric-card]') as HTMLElement | null
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
    for (const label of ['Open defects', 'P0 / P1 open', 'Time to resolve', 'Escape rate']) {
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
    expect(cell).toHaveTextContent('Oldest open P0—')
  })

  it('draws the queue health as a named meter (P3: in its Disclosure below the table, closed at load)', async () => {
    await renderWith([p0('d1', 4)])
    expect(screen.queryByRole('meter', { name: 'Queue health' })).toBeNull()
    openHealthDetails()
    const meter = screen.getByRole('meter', { name: 'Queue health' })
    expect(meter.getAttribute('aria-valuetext')).toMatch(/^\d+ of 100, (Healthy|At risk|Blocked)$/)
  })
})

// ── UX redesign P2: anything not built is not rendered. The workflow ribbon
// (invented "6 / 5 evidence"), the provenance footer ("3 tools", a toast-only
// "Decision trail"), the recommended-actions card (invented @release-qa /
// @releng / @team-* handles) and every toast-only CTA are gone. ────────────
describe('DefectsPage — renders only what it really does (P2)', () => {
  const DAY = 86_400_000
  const ago = (days: number) => new Date(Date.now() - days * DAY).toISOString()

  /** An open P0: unowned (the backend has no owner) and, by default, unlinked. */
  const p0 = (id: string, extra: Record<string, unknown> = {}) => ({
    id,
    test_name: `checkout ${id}`,
    suite_name: 'Payments',
    failure_category: 'PRODUCT_BUG',
    ai_confidence_score: 92,
    resolution_status: 'OPEN',
    created_at: ago(2),
    ...extra,
  })

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
    await screen.findByRole('region', { name: 'Defect queue verdict' })
  }

  it('has no workflow ribbon, provenance footer or recommended actions', async () => {
    await renderWith([p0('d1'), p0('d2', { jira_ticket_id: 'ACME-1' })])
    expect(screen.queryByRole('region', { name: 'Defect workflow' })).toBeNull()
    expect(screen.queryByText(/Defect workflow/i)).toBeNull()
    expect(screen.queryByText(/^compact$/i)).toBeNull()
    expect(screen.queryByText(/evidence/i)).toBeNull()
    expect(screen.queryByText(/3 tools/)).toBeNull()
    expect(screen.queryByText(/Provenance/)).toBeNull()
    expect(screen.queryByRole('button', { name: /Decision trail/i })).toBeNull()
    expect(screen.queryByText('Recommended actions')).toBeNull()
  })

  it('invents no owner handles and no deploy state', async () => {
    await renderWith([p0('d1'), p0('d2')])
    for (const handle of [/@release-qa/, /@releng/, /@team-/, /@dev\b/]) {
      expect(screen.queryByText(handle)).toBeNull()
    }
    expect(screen.queryByText(/currently armed/i)).toBeNull()
  })

  it('renders no toast-only CTA in the verdict, its issue rows or the table rows', async () => {
    await renderWith([p0('d1'), p0('d2')])
    for (const name of [
      /Triage/i, /^Assign/i, /Open bridge/i, /Open release dashboard/i, /Link Jira/i, /^Hold$/i, /^Link$/,
    ]) {
      expect(screen.queryByRole('button', { name }), String(name)).toBeNull()
    }
    // With P0s open the verdict has no real action to offer: no button at all.
    const verdict = screen.getByRole('region', { name: 'Defect queue verdict' })
    expect(within(verdict).queryAllByRole('button')).toHaveLength(0)
  })

  // P3: "New defect" is the header's one primary action, whatever the queue holds; the banner does not
  // repeat it (it was the verdict card's CTA when no P0 was open).
  it('keeps the real action — New defect — as the header\'s one primary, not repeated in the verdict', async () => {
    await renderWith([p0('d1', { failure_category: 'FLAKY' })])
    const header = document.querySelector('[data-page-header]') as HTMLElement
    expect(within(header).getByRole('button', { name: 'New defect' })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'New defect' })).toHaveLength(1)
    const verdict = screen.getByRole('region', { name: 'Defect queue verdict' })
    expect(within(verdict).queryAllByRole('button')).toHaveLength(0)
  })

  it('offers "Open in Jira" only for a row with a safe ticket URL — no stub for the rest', async () => {
    await renderWith([
      p0('linked', { jira_ticket_id: 'ACME-7', jira_ticket_url: 'https://acme.atlassian.net/browse/ACME-7' }),
      p0('id-only', { jira_ticket_id: 'ACME-8' }),
      p0('unlinked'),
      p0('unsafe', { jira_ticket_id: 'ACME-9', jira_ticket_url: 'javascript:alert(1)' }),
    ])
    const open = screen.getAllByRole('link', { name: /^Open .+ in Jira$/ })
    expect(open).toHaveLength(1)
    expect(open[0]).toHaveAccessibleName('Open ACME-7 in Jira')
    expect(open[0]).toHaveAttribute('href', 'https://acme.atlassian.net/browse/ACME-7')
    expect(open[0]).toHaveAttribute('target', '_blank')
    // Every row's Actions cell (the last one) holds nothing clickable except
    // that single link.
    const table = screen.getByRole('table')
    const bodyRows = within(table).getAllByRole('row').slice(1)
    expect(bodyRows).toHaveLength(4)
    const actionCells = bodyRows.map(r => r.querySelectorAll('td')[6])
    expect(actionCells.flatMap(c => [...c.querySelectorAll('button')])).toHaveLength(0)
    expect(actionCells.flatMap(c => [...c.querySelectorAll('a')])).toHaveLength(1)
  })

  // The backend has no owner field: every row read "Unassigned", every open
  // P0 counted as unowned, and a 20 % "Ownership" dimension scored that
  // absence as a measurement.
  it('claims nothing about owners: no Owner column, no Unassigned, no Ownership dimension', async () => {
    await renderWith([p0('d1'), p0('d2'), p0('d3', { failure_category: 'FLAKY' })])
    const table = screen.getByRole('table')
    expect(within(table).getAllByRole('columnheader').map(h => h.textContent?.replace(/[↕↑↓]/g, '').trim()))
      .toEqual(['Key', 'Title', 'Severity', 'Status', 'Jira', 'Age', 'Actions'])
    expect(screen.queryByText(/unassigned/i)).toBeNull()
    expect(screen.queryByLabelText(/unassigned/i)).toBeNull()
    expect(screen.queryByText(/unowned/i)).toBeNull()
    expect(screen.queryByText(/no owner/i)).toBeNull()
    expect(screen.queryByText('Ownership')).toBeNull()
  })

  it('weighs the composite over the three real dimensions, re-normalised to 0-100', async () => {
    // P0 open (unlinked) + P0 resolved in 2 days (linked) + P2 open (linked)
    // + P3 closed (unlinked, no resolved_at).
    const created = ago(10)
    const resolved = new Date(new Date(created).getTime() + 2 * DAY).toISOString()
    await renderWith([
      p0('a'),
      p0('b', { jira_ticket_id: 'ACME-1', resolution_status: 'RESOLVED', created_at: created, resolved_at: resolved }),
      p0('c', { jira_ticket_id: 'ACME-2', failure_category: 'INFRA' }),
      p0('d', { failure_category: 'FLAKY', resolution_status: 'CLOSED' }),
    ])
    const p0Throughput = 50            // 1 of 2 P0s resolved
    const jiraCoverage = 50            // 2 of 4 linked
    const fixVelocity = 100 - (2 / 7) * 100   // MTTR 2 d on the 7-day scale
    const expected = Math.round(
      (p0Throughput * 0.35 + jiraCoverage * 0.25 + fixVelocity * 0.20) / (0.35 + 0.25 + 0.20),
    )
    expect(expected).toBe(55)
    // The banner states the composite; the meter and its tiles are in the Disclosure (P3).
    expect(screen.getByRole('region', { name: 'Defect queue verdict' })).toHaveTextContent(`Queue health ${expected}/100`)
    const details = openHealthDetails()
    const meter = screen.getByRole('meter', { name: 'Queue health' })
    expect(meter).toHaveAttribute('aria-valuenow', String(expected))
    // The tiles show the re-normalised weights, which sum to 100 %.
    for (const [label, pct] of [['P0 throughput', '44%'], ['Jira coverage', '31%'], ['Fix velocity', '25%']]) {
      const tile = within(details).getByText(label).parentElement
      expect(tile, label).toHaveTextContent(`${label}${pct}`)
    }
    // Three dimensions, three columns: no empty fourth cell (the old 2 x 2 grid).
    const grid = within(details).getByText('P0 throughput').closest('.grid') as HTMLElement
    expect(grid.style.gridTemplateColumns).toBe('repeat(3, minmax(0, 1fr))')
  })

  // `EVG-####` was a hash of the row id dressed as a ticket number.
  it('keys a linked row by its Jira id and an unlinked one by its own id prefix — no EVG-', async () => {
    await renderWith([
      p0('1a2b3c4d-0000-4000-8000-000000000001', { jira_ticket_id: 'ACME-42' }),
      p0('9f8e7d6c-0000-4000-8000-000000000002'),
    ])
    const bodyRows = within(screen.getByRole('table')).getAllByRole('row').slice(1)
    const keys = bodyRows.map(r => r.querySelectorAll('td')[0].textContent?.trim())
    expect(keys.sort()).toEqual(['9f8e7d6c', 'ACME-42'])
    expect(document.body.textContent).not.toMatch(/EVG-/)
  })

  it('states the unlinked count and the P0 state without claims it cannot back', async () => {
    // Two unlinked defects, one created 20 days ago: "from this week" was false,
    // and nothing on the page knows an auto-link rule or what closed today.
    // P3: the verdict card's issue rows and lede are the one-line banner's facts.
    await renderWith([p0('a1'), p0('a2', { created_at: ago(20) })])
    const verdict = screen.getByRole('region', { name: 'Defect queue verdict' })
    expect(verdict.querySelector('[data-status-banner]')).toHaveAttribute('data-status-banner', 'no_go')
    expect(verdict).toHaveTextContent('Release blocked · 2 P0 blocking release')
    // The unlinked count, and how many of them are P0s — and no reason for it.
    const facts = Array.from(verdict.querySelectorAll('[data-banner-fact]')).map((f) => f.textContent)
    expect(facts).toContain('No Jira ticket 2 (2 P0)')
    // A P0 is open, so its age is the fact, not this week's movement.
    expect(facts.some((f) => /^Oldest open P0 a2 · 20d$/.test(f ?? ''))).toBe(true)
    expect(verdict.textContent).not.toMatch(/this week|auto-link|closed today/i)
    expect(verdict.textContent).not.toMatch(/bridge missed/)
  })

  it('with no P0 open, the banner states this week\'s net change instead of an oldest P0', async () => {
    await renderWith([p0('f1', { failure_category: 'FLAKY' }), p0('f2', { failure_category: 'FLAKY', created_at: ago(20) })])
    const verdict = screen.getByRole('region', { name: 'Defect queue verdict' })
    const facts = Array.from(verdict.querySelectorAll('[data-banner-fact]')).map((f) => f.textContent)
    // One created this week (2 days ago), none resolved this week: +1.
    expect(facts).toContain('Net this week +1')
    expect(facts.some((f) => /Oldest open P0/.test(f ?? ''))).toBe(false)
  })
})

// ── UX redesign P3: the page template (02-design-spec.md §2) ─────────────
describe('DefectsPage — the page template (P3)', () => {
  const DAY = 86_400_000
  const ago = (days: number) => new Date(Date.now() - days * DAY).toISOString()
  const defect = (id: string, extra: Record<string, unknown> = {}) => ({
    id,
    test_name: `checkout ${id}`,
    suite_name: 'Payments',
    failure_category: 'PRODUCT_BUG',
    ai_confidence_score: 92,
    resolution_status: 'OPEN',
    created_at: ago(2),
    ...extra,
  })

  async function renderWith(items: Record<string, unknown>[], search = '') {
    const { useDefects } = await import('@/hooks/useMetrics')
    ;(useDefects as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items, total: items.length, pages: 1 },
      isLoading: false,
    })
    mockIntegrationsConfig.mockReturnValue({ config: undefined, error: new Error('403'), isLoading: false })
    const view = renderDefects(search)
    await screen.findByRole('region', { name: 'Defect queue verdict' })
    return view
  }

  const location = () => screen.getByTestId('location').textContent

  it('puts the defects table first: after one banner and one KPI strip, before the section tabs and the Disclosure', async () => {
    const { container } = await renderWith([defect('a'), defect('b', { failure_category: 'FLAKY' })])
    const primaries = container.querySelectorAll('[data-primary]')
    expect(primaries).toHaveLength(1)
    const primary = primaries[0] as HTMLElement
    expect(within(primary).getByRole('table', { name: 'Defects' })).toBeInTheDocument()
    // The status filter is the table's: inside the primary content, beside the search.
    const statusFilter = screen.getByRole('tablist', { name: 'Status filter' })
    expect(primary).toContainElement(statusFilter)
    expect(primary).toContainElement(screen.getByRole('searchbox', { name: 'Search defects' }))
    // Every other tab bar and every Disclosure follows the table.
    const after = [
      ...Array.from(container.querySelectorAll('[role="tablist"]')).filter((el) => !primary.contains(el)),
      ...Array.from(container.querySelectorAll('[data-disclosure]')),
    ]
    expect(after).toHaveLength(2)
    for (const el of after) {
      expect(primary.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING, el.outerHTML.slice(0, 80)).toBeTruthy()
    }
    // Above it: ONE banner, then ONE strip of five KPI tiles; no verdict card and no meter above the fold.
    const banner = screen.getByRole('region', { name: 'Defect queue verdict' })
    const kpis = screen.getByRole('region', { name: 'Defect KPIs' })
    expect(container.querySelectorAll('[data-status-banner]')).toHaveLength(1)
    expect(container.querySelectorAll('[data-kpi-strip]')).toHaveLength(1)
    expect(kpis.querySelectorAll('[data-metric-card]')).toHaveLength(5)
    expect(banner.compareDocumentPosition(kpis) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(kpis.compareDocumentPosition(primary) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.queryByRole('meter', { name: 'Queue health' })).toBeNull()
    // The shared compact header with the help **?** (the defects anchor's topic).
    const header = container.querySelector('[data-page-header]') as HTMLElement
    expect(header).toHaveAttribute('data-compact', 'true')
    expect(within(header).getByRole('button', { name: 'Help: Defects' })).toHaveAttribute('data-help-topic', 'failure-analysis')
  })

  it('"Where defects live" is the default tab; ?tab=jira selects the Jira bridge', async () => {
    await renderWith([defect('a')])
    expect(screen.getByRole('tab', { name: 'Where defects live', selected: true })).toBeInTheDocument()
    expect(screen.getByRole('tabpanel', { name: 'Where defects live' })).toHaveTextContent('open by component')
    expect(screen.queryByText(/Jira bridge ·/)).toBeNull()
  })

  it('?tab=jira renders the Jira bridge card in its tab', async () => {
    await renderWith([defect('a')], '?tab=jira')
    expect(screen.getByRole('tab', { name: 'Jira bridge', selected: true })).toBeInTheDocument()
    const panel = screen.getByRole('tabpanel', { name: 'Jira bridge' })
    expect(within(panel).getByText('Unknown')).toBeInTheDocument()
    expect(within(panel).getByText('Linked defects')).toBeInTheDocument()
    expect(screen.queryByText('Where defects live', { selector: 'h3' })).toBeNull()
  })

  it('picking a tab writes ?tab= (the default has the clean URL) and swaps the card', async () => {
    await renderWith([defect('a')])
    fireEvent.click(screen.getByRole('tab', { name: 'Jira bridge' }))
    expect(location()).toBe('?tab=jira')
    expect(screen.getByRole('tabpanel', { name: 'Jira bridge' })).toHaveTextContent('Linked defects')
    fireEvent.click(screen.getByRole('tab', { name: 'Where defects live' }))
    expect(location()).toBe('')
  })

  it('the status filter still filters the table from the table header', async () => {
    await renderWith([defect('open1'), defect('done1', { resolution_status: 'RESOLVED', resolved_at: ago(1) })])
    const table = screen.getByRole('table', { name: 'Defects' })
    expect(within(table).getAllByRole('row')).toHaveLength(3)
    fireEvent.click(screen.getByRole('tab', { name: /Resolved/ }))
    expect(within(table).getAllByRole('row')).toHaveLength(2)
    expect(table).toHaveTextContent('checkout done1')
    localStorage.removeItem('tl.defects.tab')
  })

  // ── UX redesign P6: the status filter is the `Tabs` primitive ──
  it('the status filter is the Tabs primitive: a tab per status with its count; its state is local, never ?tab=', async () => {
    localStorage.removeItem('tl.defects.tab')
    await renderWith([defect('open1'), defect('open2'), defect('done1', { resolution_status: 'RESOLVED', resolved_at: ago(1) })])
    const filter = screen.getByRole('tablist', { name: 'Status filter' })
    expect(filter).toHaveAttribute('data-tabs')
    const tabs = within(filter).getAllByRole('tab')
    expect(tabs.map((t) => t.getAttribute('data-tab'))).toEqual(['ALL', 'OPEN', 'IN_PROGRESS', 'RESOLVED', 'CLOSED'])
    // The count beside each label (0 is shown).
    expect(tabs.map((t) => t.querySelector('[data-tab-count]')?.textContent)).toEqual(['3', '2', '0', '1', '0'])
    expect(within(filter).getByRole('tab', { name: /^All/, selected: true })).toBeInTheDocument()

    fireEvent.click(within(filter).getByRole('tab', { name: /^Open/ }))
    expect(within(filter).getByRole('tab', { name: /^Open/, selected: true })).toBeInTheDocument()
    expect(within(screen.getByRole('table', { name: 'Defects' })).getAllByRole('row')).toHaveLength(3)
    // Remembered for the next visit, and the URL's ?tab= stays the section tabs'.
    expect(localStorage.getItem('tl.defects.tab')).toBe('OPEN')
    expect(location()).toBe('')
    expect(screen.getByRole('tab', { name: 'Where defects live', selected: true })).toBeInTheDocument()
    localStorage.removeItem('tl.defects.tab')
  })

  it('opens on the status remembered from the last visit, whatever the section tab in ?tab=', async () => {
    localStorage.setItem('tl.defects.tab', 'RESOLVED')
    await renderWith([defect('open1'), defect('done1', { resolution_status: 'RESOLVED', resolved_at: ago(1) })], '?tab=jira')
    const filter = screen.getByRole('tablist', { name: 'Status filter' })
    expect(within(filter).getByRole('tab', { name: /^Resolved/, selected: true })).toBeInTheDocument()
    const table = screen.getByRole('table', { name: 'Defects' })
    expect(within(table).getAllByRole('row')).toHaveLength(2)
    expect(table).toHaveTextContent('checkout done1')
    expect(screen.getByRole('tab', { name: 'Jira bridge', selected: true })).toBeInTheDocument()
    localStorage.removeItem('tl.defects.tab')
  })

  it('an empty queue says how to fill it in the table (the old verdict lede\'s links), and the banner is PENDING', async () => {
    await renderWith([])
    const verdict = screen.getByRole('region', { name: 'Defect queue verdict' })
    expect(verdict.querySelector('[data-status-banner]')).toHaveAttribute('data-status-banner', 'pending')
    expect(verdict).toHaveTextContent('Queue health —')
    const table = screen.getByRole('table', { name: 'Defects' })
    expect(table).toHaveTextContent('No defects in this project yet')
    expect(within(table).getByRole('link', { name: 'review your assigned failures' })).toHaveAttribute('href', '/my-failures')
    expect(within(table).getByRole('link', { name: 'run an AI investigation' })).toHaveAttribute('href', '/deep-investigate')
  })
})

// Browser E2E pass (2026-10-08): the list carried no title, severity or
// component, so a defect typed in as "Refund posts twice", P3, read "product
// bug" and P1; and the page loaded one 20-row page, reading "Open defects 20"
// on a project with 80.
describe('DefectsPage — each defect as entered, and every defect counted', () => {
  async function renderWith(data: unknown) {
    const { useDefects } = await import('@/hooks/useMetrics')
    ;(useDefects as ReturnType<typeof vi.fn>).mockReturnValue({ data, isLoading: false })
    render(
      <MemoryRouter initialEntries={['/defects']}>
        <Routes>
          <Route path="/defects" element={<DefectsPage />} />
        </Routes>
      </MemoryRouter>,
    )
    return screen.findByRole('region', { name: 'All defects' })
  }

  const ENTERED = {
    id: 'def-entered', title: 'Refund posts twice', severity: 'LOW', component: 'payments-service',
    failure_category: 'PRODUCT_BUG', test_name: null, suite_name: null,
    resolution_status: 'OPEN', created_at: '2026-10-08T10:00:00Z',
  }

  it('shows its own title, severity and component, not ones derived from its category', async () => {
    const table = await renderWith({ items: [ENTERED], total: 1, pages: 1 })
    const row = within(table).getByText('Refund posts twice').closest('tr') as HTMLElement
    expect(row).toHaveTextContent('P3')
    expect(row).not.toHaveTextContent('P1')
    // Its component is the one entered: searching for it finds the row.
    fireEvent.change(within(table).getByRole('searchbox', { name: 'Search defects' }), { target: { value: 'payments-service' } })
    expect(within(table).getByText('Refund posts twice')).toBeInTheDocument()
  })

  it('keeps the derived title and severity for a defect stored without them', async () => {
    const table = await renderWith({
      items: [{ ...ENTERED, id: 'def-seeded', title: null, severity: null, component: null, test_name: 'test_pay', ai_confidence_score: 90 }],
      total: 1, pages: 1,
    })
    const row = within(table).getByText('test_pay').closest('tr') as HTMLElement
    expect(row).toHaveTextContent('P0') // a product bug at 90% confidence, as before
  })

  it('says when the counts cover only the newest defects', async () => {
    await renderWith({ items: [ENTERED], total: 1500, pages: 1 })
    expect(document.querySelector('[data-defects-capped]')).toHaveTextContent(
      'The counts and the table cover the newest 1 of 1500 defects.',
    )
  })

  it('says nothing of the kind when every defect is loaded', async () => {
    await renderWith({ items: [ENTERED], total: 1, pages: 1 })
    expect(document.querySelector('[data-defects-capped]')).toBeNull()
  })
})
