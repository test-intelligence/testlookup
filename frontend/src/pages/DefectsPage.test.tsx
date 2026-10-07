import { render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
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
    expect(screen.getByText('Where defects live')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Defect KPIs' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Customize/i })).toBeNull()
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

  it('keeps the verdict\'s real action — New defect — when no P0 is open', async () => {
    await renderWith([p0('d1', { failure_category: 'FLAKY' })])
    const verdict = screen.getByRole('region', { name: 'Defect queue verdict' })
    expect(within(verdict).getByRole('button', { name: 'New defect' })).toBeInTheDocument()
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
    const meter = screen.getByRole('meter', { name: 'Queue health' })
    expect(meter).toHaveAttribute('aria-valuenow', String(expected))
    // The tiles show the re-normalised weights, which sum to 100 %.
    const verdict = screen.getByRole('region', { name: 'Defect queue verdict' })
    for (const [label, pct] of [['P0 throughput', '44%'], ['Jira coverage', '31%'], ['Fix velocity', '25%']]) {
      const tile = within(verdict).getByText(label).parentElement
      expect(tile, label).toHaveTextContent(`${label}${pct}`)
    }
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
    await renderWith([p0('a1'), p0('a2', { created_at: ago(20) })])
    const verdict = screen.getByRole('region', { name: 'Defect queue verdict' })
    expect(verdict).toHaveTextContent('2 defects have no Jira ticket — they exist only in TestLookup.')
    expect(verdict).toHaveTextContent('All open or in progress.')
    expect(verdict.textContent).not.toMatch(/this week|auto-link|closed today/)
    // The lede: the unlinked P0 count with the right verb, and no reason for it.
    expect(verdict).toHaveTextContent('2 have no Jira ticket.')
    expect(verdict.textContent).not.toMatch(/bridge missed/)
  })
})
