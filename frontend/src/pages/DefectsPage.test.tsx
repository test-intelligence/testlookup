import { useState } from 'react'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import DefectsPage from './DefectsPage'
import { reportViewFilters } from '@/components/reports/savedViewsModel'
import type { SavedView } from '@/services/savedViewsService'
import { useReleaseStore } from '@/store/releaseStore'
import { useTimeWindowStore } from '@/store/timeWindowStore'
import type {
  DefectEscapeRateResponse,
  DefectScopeQuery,
  DefectTrendPoint,
  DefectTrendResponse,
  DefectTrendTotals,
} from '@/types/analytics'

// Mock every useMetrics export — the page (or its widgets) may import any
// of them and vitest errors on an undefined export. Tests override the
// specific hook(s) they care about via ``mockReturnValue`` further down.
// P1: the header's Views menu reads this page's saved views (none by default).
const viewsService = vi.hoisted(() => ({
  listSavedViews: vi.fn(async (): Promise<unknown[]> => []),
  createSavedView: vi.fn(async () => ({})),
  updateSavedView: vi.fn(),
  deleteSavedView: vi.fn(),
}))
vi.mock('@/services/savedViewsService', () => viewsService)
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
// W06: the trend (DEF-04) and the escape rate (DEF-03) come from their own
// hook module; by default neither has answered (the tiles show the rows').
type InsightResult<T> = { data?: T; isLoading: boolean; error?: unknown }
const insights = vi.hoisted(() => ({
  trend: vi.fn((_scope: unknown): { data?: unknown; isLoading: boolean; error?: unknown } => ({ data: undefined, isLoading: false })),
  escape: vi.fn((_scope: unknown): { data?: unknown; isLoading: boolean; error?: unknown } => ({ data: undefined, isLoading: false })),
  refresh: vi.fn(async () => undefined),
}))
vi.mock('@/hooks/useDefectInsights', () => ({
  useDefectTrend: (scope: unknown) => insights.trend(scope),
  useDefectEscapeRate: (scope: unknown) => insights.escape(scope),
  refreshDefectInsights: () => insights.refresh(),
}))
// W06 DEF-01: the row menu is G-D's (`DefectRowActions`, its own tests). The
// stub records what each row hands it and lets a test play its answer back.
// W27 PAN-04: the row controls it (`action` + `onAction`) and renders the
// chosen action's form (`DefectActionForm`) under itself; the stubs record both.
const rowActions = vi.hoisted(() => ({
  defects: new Map<string, Record<string, unknown>>(),
  onUpdated: new Map<string, (row: unknown) => void>(),
  action: new Map<string, string | null | undefined>(),
  onAction: new Map<string, ((action: string | null) => void) | undefined>(),
}))
vi.mock('@/components/defects/DefectRowActions', () => ({
  default: ({ defect, onUpdated, action, onAction }: {
    defect: Record<string, unknown> & { id: string }
    onUpdated: (row: unknown) => void
    action?: string | null
    onAction?: (action: string | null) => void
  }) => {
    rowActions.defects.set(defect.id, defect)
    rowActions.onUpdated.set(defect.id, onUpdated)
    rowActions.action.set(defect.id, action)
    rowActions.onAction.set(defect.id, onAction)
    return <span data-testid="row-actions" data-defect-id={defect.id} />
  },
  DefectActionForm: ({ defect, action, onClose }: { defect: { id: string }; action: string; onClose: () => void }) => (
    <section aria-label={`form ${action}`} data-testid="row-action-form" data-defect-id={defect.id} data-action={action}>
      <button type="button" onClick={onClose}>Cancel</button>
    </section>
  ),
}))
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
const suiteOptions = vi.hoisted(() => ({ names: [] as string[] }))
vi.mock('@/hooks/useSuiteOptions', () => ({
  useSuiteOptions: () => ({ options: suiteOptions.names, isLoading: false }),
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

/** Render the page at `/defects<search>`, with the location probe beside it (own SWR cache: the Views menu reads one). */
function renderDefects(search = '') {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter initialEntries={[`/defects${search}`]}>
        <Routes>
          <Route path="/defects" element={<><DefectsPage /><LocationProbe /></>} />
        </Routes>
      </MemoryRouter>
    </SWRConfig>,
  )
}

const location = () => screen.getByTestId('location').textContent

/** Open the "How queue health is computed" Disclosure (it renders nothing while closed). */
function openHealthDetails() {
  fireEvent.click(screen.getByRole('button', { name: /How queue health is computed/ }))
  return screen.getByRole('meter', { name: 'Queue health' }).closest('[data-disclosure]') as HTMLElement
}

async function defectsHookMock() {
  const { useDefects } = await import('@/hooks/useMetrics')
  return useDefects as unknown as ReturnType<typeof vi.fn>
}

/** `useDefects` answers these rows (a fixed answer: a row menu's `mutate` is a spy). */
async function mockRows(items: Record<string, unknown>[], extra: Record<string, unknown> = {}) {
  const mutate = vi.fn(async () => undefined)
  ;(await defectsHookMock()).mockReturnValue({
    data: { items, total: items.length, pages: 1, ...extra },
    isLoading: false,
    mutate,
  })
  return mutate
}

/**
 * `useDefects` with a LIVE cache entry: its `mutate` applies the updater the
 * page hands it and re-renders, as SWR's does — so "the row moves without a
 * reload" is observable.
 */
async function mockLiveRows(items: Record<string, unknown>[]) {
  const mutate = vi.fn()
  ;(await defectsHookMock()).mockImplementation(function useDefectsLive() {
    const [data, setData] = useState<{ items: unknown[]; total: number; pages: number }>(
      { items, total: items.length, pages: 1 },
    )
    mutate.mockImplementation(async (updater: unknown) => {
      setData(prev => (typeof updater === 'function' ? (updater as (d: typeof prev) => typeof prev)(prev) : (updater as typeof prev)))
    })
    return { data, isLoading: false, mutate }
  })
  return mutate
}

/** Play the row menu's answer back for one row (what `DefectRowActions` calls after a PATCH). */
function rowMenuAnswers(id: string, row: Record<string, unknown>) {
  const onUpdated = rowActions.onUpdated.get(id)
  if (!onUpdated) throw new Error(`no row menu for ${id}`)
  act(() => onUpdated(row))
}

function lastCallArg(fn: ReturnType<typeof vi.fn>, index = 0): unknown {
  const calls = fn.mock.calls
  return calls[calls.length - 1]?.[index]
}

const DAY = 86_400_000
const ago = (days: number) => new Date(Date.now() - days * DAY).toISOString()

/** A KPI tile by its label (P3: a compact `MetricCard` of the `KpiStrip`). */
function kpiCell(kpis: HTMLElement, label: string): HTMLElement {
  const cell = within(kpis).getByText(label).closest('[data-metric-card]') as HTMLElement | null
  if (!cell) throw new Error(`no KPI cell labelled ${label}`)
  return cell
}

/** A body row of the defects table by its title text. */
function rowOf(title: string): HTMLElement {
  const row = within(screen.getByRole('table', { name: 'Defects' })).getByText(title).closest('tr')
  if (!row) throw new Error(`no row titled ${title}`)
  return row as HTMLElement
}

/** The table's column index of a header (the cells follow it). */
function cellOf(row: HTMLElement, header: string): HTMLElement {
  const headers = within(screen.getByRole('table', { name: 'Defects' })).getAllByRole('columnheader')
    .map(h => h.textContent?.replace(/[↕↑↓]/g, '').trim())
  const index = headers.indexOf(header)
  if (index < 0) throw new Error(`no column ${header}`)
  return row.querySelectorAll('td')[index] as HTMLElement
}

beforeEach(() => {
  analyticsControls.widgetIds = []
  suiteOptions.names = []
  insights.trend.mockReset()
  insights.trend.mockReturnValue({ data: undefined, isLoading: false })
  insights.escape.mockReset()
  insights.escape.mockReturnValue({ data: undefined, isLoading: false })
  insights.refresh.mockClear()
  rowActions.defects.clear()
  rowActions.onUpdated.clear()
  viewsService.listSavedViews.mockImplementation(async () => [])
  viewsService.createSavedView.mockClear()
  useReleaseStore.setState({ activeReleaseId: null, scopedProjectId: null })
  useTimeWindowStore.setState({ days: 30 })
  localStorage.removeItem('tl.defects.tab')
})

afterEach(() => {
  localStorage.removeItem('tl.defects.tab')
})

describe('DefectsPage', () => {
  it('renders the verdict, the KPIs and the defect table', async () => {
    await mockRows([
      {
        id: 'def-1',
        test_name: 'payments should process a charge',
        suite_name: 'Payments',
        resolution_status: 'OPEN',
        ai_confidence_score: 84,
        created_at: '2026-04-01T10:00:00Z',
      },
    ])

    renderDefects()

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
    analyticsControls.widgetIds = []
    await mockRows([{
      id: 'def-1', test_name: 'payment fails', suite_name: 'Payments',
      resolution_status: 'OPEN', ai_confidence_score: 80,
      created_at: '2026-04-01T10:00:00Z',
    }])

    renderDefects()

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
    await mockRows([])
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
    await mockRows([])
    // /settings/integrations needs QA_LEAD; a developer gets a 403.
    mockIntegrationsConfig.mockReturnValue({
      config: undefined, error: new Error('403'), isLoading: false,
    })

    renderWithDefects()

    expect(await screen.findByText('Unknown')).toBeInTheDocument()
    expect(screen.queryByText('Connected')).not.toBeInTheDocument()
  })

  it('says connected only with an enabled bridge that has a host and a credential', async () => {
    await mockRows([])
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
    const { jiraBridgeState } = await import('@/hooks/useIntegrationsConfig')
    await mockRows([])
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

describe('DefectsPage — saying that the release filter does not apply (toggle off)', () => {
  /**
   * The page's own subtitle reads "N P0 blocking release". With a release
   * picked in the header and "Only this release" off (the default), the
   * numbers are every release's: a defect raised against an earlier release
   * can still be open now.
   */
  it('marks the page when a release is selected', async () => {
    await mockRows([])
    useReleaseStore.setState({ activeReleaseId: 'rel-1', scopedProjectId: 'proj-1' })
    renderDefects()

    expect(await screen.findByText('All releases')).toBeInTheDocument()
  })

  it('explains why, rather than only flagging it, and names the toggle that narrows it', async () => {
    await mockRows([])
    useReleaseStore.setState({ activeReleaseId: 'rel-1', scopedProjectId: 'proj-1' })
    renderDefects()

    const badge = await screen.findByText('All releases')
    expect(badge.getAttribute('title')).toMatch(/tracked per project, not per release/)
    expect(badge.getAttribute('title')).toMatch(/"Only this release" narrows the page/)
  })

  it('stays silent when no release is selected', async () => {
    // The control, and the badge's whole design: with no release chosen there
    // is no discrepancy to explain, and the page looks exactly as it did
    // before the release axis existed. A badge that always rendered would pass
    // the two tests above and become furniture.
    await mockRows([])
    renderDefects()

    await screen.findByRole('heading', { name: 'Defects' })
    expect(screen.queryByText('All releases')).toBeNull()
  })
})

// ── W06 DEF-05: "Only this release", the suite, and saved views keep both ──
describe('DefectsPage — scoped by release and suite (DEF-05)', () => {
  const RELEASE_META = {
    scope: { projects: [], releases: [{ id: 'rel-1', name: 'Checkout 2.4', status: 'active' }], suites: [], window: { from: '', to: '', days: 30, timezone: 'UTC' } },
  }

  function selectRelease() {
    useReleaseStore.setState({ activeReleaseId: 'rel-1', scopedProjectId: 'proj-1' })
  }

  it('offers the toggle only while ONE release is picked in the top bar', async () => {
    await mockRows([])
    const { unmount } = renderDefects()
    await screen.findByRole('heading', { name: 'Defects' })
    expect(screen.queryByTestId('defects-release-scope')).toBeNull()
    unmount()

    selectRelease()
    renderDefects()
    const toggle = await screen.findByTestId('defects-release-scope')
    expect(toggle).toHaveTextContent('Only this release')
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    expect(toggle.getAttribute('title')).toBe(
      'Defects that affect the selected release: asserted impact, else where the defect was found. The release gate counts the same set.',
    )
    // The header order: Views · window · toggle · suite · New defect.
    const header = document.querySelector('[data-page-header]') as HTMLElement
    const order = [
      within(header).getByRole('button', { name: /Views/ }),
      within(header).getByRole('radiogroup', { name: 'Time window' }),
      toggle,
      within(header).getByRole('combobox', { name: 'Test suite' }),
      within(header).getByRole('button', { name: 'New defect' }),
    ]
    for (let i = 1; i < order.length; i++) {
      expect(order[i - 1].compareDocumentPosition(order[i]) & Node.DOCUMENT_POSITION_FOLLOWING, `control ${i}`).toBeTruthy()
    }
  })

  it('off: no release is sent by the list, the trend or the escape rate, whatever the top bar holds', async () => {
    const useDefects = await defectsHookMock()
    await mockRows([])
    selectRelease()
    renderDefects()
    await screen.findByTestId('defects-release-scope')

    expect(lastCallArg(useDefects, 1)).toEqual({ releaseIds: undefined, releaseMatch: undefined, suiteName: null })
    expect(lastCallArg(insights.trend)).toEqual({ releaseIds: undefined, releaseMatch: undefined, suiteName: null, days: 30 })
    expect(lastCallArg(insights.escape)).toEqual({ days: 30, releaseIds: undefined })
    expect(screen.getByText('All releases')).toBeInTheDocument()
    expect(document.querySelector('[data-defects-scope-chip]')).toBeNull()
  })

  it('on: the three hooks get the release in affects mode, ?scope=release is written, and the chip replaces the badge', async () => {
    const useDefects = await defectsHookMock()
    await mockRows([], { meta: RELEASE_META })
    selectRelease()
    renderDefects()

    fireEvent.click(await screen.findByTestId('defects-release-scope'))

    expect(screen.getByTestId('defects-release-scope')).toHaveAttribute('aria-pressed', 'true')
    expect(location()).toBe('?scope=release')
    const scope: DefectScopeQuery = { releaseIds: ['rel-1'], releaseMatch: 'affects', suiteName: null }
    expect(lastCallArg(useDefects, 1)).toEqual(scope)
    expect(lastCallArg(insights.trend)).toEqual({ ...scope, days: 30 })
    // The escape rate takes the release only.
    expect(lastCallArg(insights.escape)).toEqual({ days: 30, releaseIds: ['rel-1'] })
    const chip = document.querySelector('[data-defects-scope-chip]') as HTMLElement
    expect(chip).toHaveTextContent('Affecting Checkout 2.4')
    expect(screen.queryByText('All releases')).toBeNull()

    // Off again: the URL is clean and the badge is back.
    fireEvent.click(screen.getByTestId('defects-release-scope'))
    expect(location()).toBe('')
    expect(screen.getByText('All releases')).toBeInTheDocument()
    expect(lastCallArg(useDefects, 1)).toEqual({ releaseIds: undefined, releaseMatch: undefined, suiteName: null })
  })

  it('?scope=release survives a remount (a reload), and the chip falls back to "the selected release" without a name', async () => {
    const useDefects = await defectsHookMock()
    await mockRows([])
    selectRelease()
    renderDefects('?scope=release')

    expect(await screen.findByTestId('defects-release-scope')).toHaveAttribute('aria-pressed', 'true')
    expect(lastCallArg(useDefects, 1)).toEqual({ releaseIds: ['rel-1'], releaseMatch: 'affects', suiteName: null })
    expect(document.querySelector('[data-defects-scope-chip]')).toHaveTextContent('Affecting the selected release')
  })

  it('?scope=release without a picked release narrows nothing (no toggle, no release sent)', async () => {
    const useDefects = await defectsHookMock()
    await mockRows([])
    renderDefects('?scope=release')
    await screen.findByRole('heading', { name: 'Defects' })
    expect(screen.queryByTestId('defects-release-scope')).toBeNull()
    expect(lastCallArg(useDefects, 1)).toEqual({ releaseIds: undefined, releaseMatch: undefined, suiteName: null })
    expect(document.querySelector('[data-defects-scope-chip]')).toBeNull()
  })

  it('the suite narrows the list and the trend, never the escape rate', async () => {
    const useDefects = await defectsHookMock()
    await mockRows([])
    suiteOptions.names = ['Checkout', 'Payments']
    renderDefects()
    const select = await screen.findByRole('combobox', { name: 'Test suite' })
    expect(within(select).getAllByRole('option').map(o => o.textContent)).toEqual(['All suites', 'Checkout', 'Payments'])

    fireEvent.change(select, { target: { value: 'Payments' } })

    expect(lastCallArg(useDefects, 1)).toEqual({ releaseIds: undefined, releaseMatch: undefined, suiteName: 'Payments' })
    expect(lastCallArg(insights.trend)).toEqual({ releaseIds: undefined, releaseMatch: undefined, suiteName: 'Payments', days: 30 })
    expect(lastCallArg(insights.escape)).toEqual({ days: 30, releaseIds: undefined })
  })

  it('a saved view keeps the tab, the toggle, the release, the window and the suite', async () => {
    await mockRows([])
    suiteOptions.names = ['Checkout', 'Payments']
    selectRelease()
    renderDefects()
    fireEvent.click(await screen.findByTestId('defects-release-scope'))
    fireEvent.change(screen.getByRole('combobox', { name: 'Test suite' }), { target: { value: 'Payments' } })
    fireEvent.click(within(screen.getByRole('tablist', { name: 'Status filter' })).getByRole('tab', { name: /^Open/ }))
    fireEvent.click(screen.getByRole('radio', { name: '7d' }))

    fireEvent.click(screen.getByRole('button', { name: /Views/ }))
    fireEvent.change(await screen.findByPlaceholderText('e.g. Payments release watch'), { target: { value: 'Release watch' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save view' }))

    await waitFor(() => expect(viewsService.createSavedView).toHaveBeenCalledTimes(1))
    const body = (viewsService.createSavedView.mock.calls[0] as unknown[])[0] as { filters: Record<string, unknown> }
    expect(body.filters).toMatchObject({
      kind: 'report_view',
      page: 'defects',
      window: 7,
      release_ids: ['rel-1'],
      suites: ['Payments'],
      defects: { tab: 'OPEN', scope: 'release' },
    })
  })

  it('opening a saved view restores the toggle, the release, the suite, the window and the tab', async () => {
    const saved: SavedView = {
      id: 'v1', user_id: 'someone', project_id: 'proj-1', name: 'Checkout watch', description: null, page: 'defects',
      filters: {
        ...reportViewFilters('defects', { releaseIds: ['rel-1'], suiteNames: ['Checkout'], windowDays: 7 }),
        defects: { tab: 'RESOLVED', scope: 'release' },
      },
      is_shared: true, is_default: false, created_at: '2026-10-01T00:00:00Z', updated_at: null,
    }
    viewsService.listSavedViews.mockImplementation(async () => [saved])
    const useDefects = await defectsHookMock()
    await mockRows([])
    suiteOptions.names = ['Checkout', 'Payments']
    renderDefects()

    fireEvent.click(await screen.findByRole('button', { name: /Views/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Checkout watch/ }))

    expect(await screen.findByTestId('defects-release-scope')).toHaveAttribute('aria-pressed', 'true')
    expect(location()).toBe('?scope=release')
    expect(screen.getByRole('combobox', { name: 'Test suite' })).toHaveValue('Checkout')
    expect(screen.getByRole('radio', { name: '7d' })).toHaveAttribute('aria-checked', 'true')
    expect(within(screen.getByRole('tablist', { name: 'Status filter' })).getByRole('tab', { name: /^Resolved/, selected: true })).toBeInTheDocument()
    expect(lastCallArg(useDefects, 1)).toEqual({ releaseIds: ['rel-1'], releaseMatch: 'affects', suiteName: 'Checkout' })
    expect(lastCallArg(insights.trend)).toEqual({ releaseIds: ['rel-1'], releaseMatch: 'affects', suiteName: 'Checkout', days: 7 })
  })
})

// ── Wave 2.5 (VIZ-104, OD-1): a KPI glyph draws a REAL series or nothing.
// W06 DEF-04 gives the page a real one (the trend); while it has not
// answered, the tiles draw no line. ────────────────────────────────────────
describe('DefectsPage — KPI glyphs draw only real numbers', () => {
  const agoH = (days: number) => new Date(Date.now() - days * DAY - 3_600_000).toISOString()

  async function renderWith(items: Record<string, unknown>[]) {
    await mockRows(items)
    renderDefects()
    return screen.findByRole('region', { name: 'Defect KPIs' })
  }

  const p0 = (id: string, days: number) => ({
    id,
    test_name: `checkout ${id}`,
    suite_name: 'Payments',
    failure_category: 'PRODUCT_BUG',
    ai_confidence_score: 92,
    resolution_status: 'OPEN',
    created_at: agoH(days),
  })

  it('draws no line in any tile while the trend has not answered', async () => {
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

// ── W06 DEF-04: the tiles read the trend's totals and draw its lines ─────
describe('DefectsPage — trend history and KPI sparklines (DEF-04)', () => {
  const DATES = ['2026-10-09', '2026-10-10', '2026-10-11', '2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15']
  const MTTR = [null, null, 2.5, 3, null, 3.5, 4]

  function trendOf(totals: Partial<DefectTrendTotals> = {}, mttr: (number | null)[] = MTTR): DefectTrendResponse {
    const points: DefectTrendPoint[] = DATES.map((date, i) => ({
      date, opened: i + 1, closed: i, open_backlog: 1194 + i, p0p1_open: 27 + i, mttr_days: mttr[i] ?? null,
    }))
    return {
      points,
      window_days: 7,
      totals: {
        open: 1200, p0_open: 12, p1_open: 21, mttr_days: 3.4, resolved_in_window: 9,
        opened_in_window: 28, closed_in_window: 21, opened_last_7d: 28, closed_last_7d: 23, net_last_7d: 5,
        closed_without_time: 0, ...totals,
      },
    }
  }

  /** 20 loaded rows of 1,200 (the list's cap is not the tiles' business), all FLAKY so no P0 is open. */
  const rows = Array.from({ length: 20 }, (_, i) => ({
    id: `d-${i}`, test_name: `flaky ${i}`, suite_name: 'Payments', failure_category: 'FLAKY',
    resolution_status: 'OPEN', created_at: ago(2),
  }))

  async function renderTrend(trend: DefectTrendResponse | undefined, items: Record<string, unknown>[] = rows, total = 1200) {
    useTimeWindowStore.setState({ days: 7 })
    insights.trend.mockReturnValue({ data: trend, isLoading: false })
    await mockRows(items, { total })
    renderDefects()
    return screen.findByRole('region', { name: 'Defect KPIs' })
  }

  it('asks for the window picked in the header (7 / 14 / 30 / 90), under its title', async () => {
    await renderTrend(trendOf())
    const picker = screen.getByRole('radiogroup', { name: 'Time window' })
    expect(within(picker).getAllByRole('radio').map(r => r.textContent)).toEqual(['7d', '14d', '30d', '90d'])
    expect(picker.parentElement).toHaveAttribute(
      'title', 'Window for the KPI sparklines, Time to resolve and Escape rate. The table lists every defect.',
    )
    expect(lastCallArg(insights.trend)).toMatchObject({ days: 7 })
    expect(lastCallArg(insights.escape)).toMatchObject({ days: 7 })
    fireEvent.click(within(picker).getByRole('radio', { name: '90d' }))
    expect(lastCallArg(insights.trend)).toMatchObject({ days: 90 })
    expect(lastCallArg(insights.escape)).toMatchObject({ days: 90 })
    // WIN-01: 14d between 7d and 30d asks both reads for 14 days.
    fireEvent.click(within(picker).getByRole('radio', { name: '14d' }))
    expect(lastCallArg(insights.trend)).toMatchObject({ days: 14 })
    expect(lastCallArg(insights.escape)).toMatchObject({ days: 14 })
    expect(useTimeWindowStore.getState().days).toBe(14)
  })

  it('WIN-01: a stored 14d opens as 14d and stays 14 (no snap to 7)', async () => {
    useTimeWindowStore.setState({ days: 14 })
    insights.trend.mockReturnValue({ data: { ...trendOf(), window_days: 14 }, isLoading: false })
    await mockRows(rows, { total: 1200 })
    renderDefects()
    await screen.findByRole('region', { name: 'Defect KPIs' })
    expect(screen.getByRole('radio', { name: '14d' })).toHaveAttribute('aria-checked', 'true')
    expect(lastCallArg(insights.trend)).toMatchObject({ days: 14 })
    expect(lastCallArg(insights.escape)).toMatchObject({ days: 14 })
    expect(insights.trend.mock.calls.some(call => (call[0] as { days?: number }).days === 7)).toBe(false)
    expect(useTimeWindowStore.getState().days).toBe(14)
  })

  it('reads the tile values from the totals over every defect, not from the 20 loaded rows', async () => {
    const kpis = await renderTrend(trendOf())
    expect(kpiCell(kpis, 'Open defects')).toHaveTextContent(/^Open defects1,200/)
    expect(kpiCell(kpis, 'P0 / P1 open')).toHaveTextContent(/^P0 \/ P1 open12 \/ 21/)
    const ttr = kpiCell(kpis, 'Time to resolve')
    expect(ttr).toHaveTextContent(/^Time to resolve3\.4d/)
    expect(within(ttr).getByText('Time to resolve')).toHaveAttribute(
      'title', 'Mean time to resolve, over the defects resolved in the last 7 days',
    )
    // The cap note says the tiles are not capped.
    expect(document.querySelector('[data-defects-capped]')).toHaveTextContent(
      'The table and the tabs cover the newest 20 of 1200 defects; the KPI tiles count all of them.',
    )
  })

  it('draws the three lines over the window, with each point\'s day on hover', async () => {
    const kpis = await renderTrend(trendOf())
    const open = within(kpiCell(kpis, 'Open defects')).getByRole('img')
    expect(open).toHaveAccessibleName(/^Open defects per day, last 7 days: 7 points, latest 1,200,/)
    const openTitles = [...kpiCell(kpis, 'Open defects').querySelectorAll('circle title')].map(t => t.textContent)
    expect(openTitles).toHaveLength(7)
    expect(openTitles[0]).toBe('2026-10-09: +1 opened, −0 closed, 1194 open')
    expect(openTitles[6]).toBe('2026-10-15: +7 opened, −6 closed, 1200 open')

    const p0p1 = kpiCell(kpis, 'P0 / P1 open')
    expect(within(p0p1).getByRole('img')).toHaveAccessibleName(/^P0 \/ P1 open per day, last 7 days: 7 points, latest 33,/)
    expect(p0p1.querySelector('circle title')?.textContent).toBe('2026-10-09: +1 opened, −0 closed, 27 P0/P1 open')
    // A P0 is open: the P0 / P1 line is the bad tone.
    expect(p0p1.querySelector('path[data-part="line"]')).toHaveAttribute('stroke', 'var(--status-failed)')

    const ttr = kpiCell(kpis, 'Time to resolve')
    expect(within(ttr).getByRole('img')).toHaveAccessibleName(
      'Time to resolve per day, last 7 days: 4 points, latest 4.0d, min 2.5d, max 4.0d, 3 not measured',
    )
    // A day with nothing resolved in its 7 days is a gap: no hover target.
    expect([...ttr.querySelectorAll('circle title')].map(t => t.textContent)).toEqual([
      '2026-10-11: 2.5d to resolve (7-day mean)',
      '2026-10-12: 3.0d to resolve (7-day mean)',
      '2026-10-14: 3.5d to resolve (7-day mean)',
      '2026-10-15: 4.0d to resolve (7-day mean)',
    ])
  })

  it('tones the P0 / P1 line neutral when no P0 is open', async () => {
    const kpis = await renderTrend(trendOf({ p0_open: 0 }))
    expect(kpiCell(kpis, 'P0 / P1 open').querySelector('path[data-part="line"]'))
      .toHaveAttribute('stroke', 'var(--color-text-muted)')
  })

  it('says why there is no line with fewer than two measured points, and draws none', async () => {
    const kpis = await renderTrend(trendOf({ mttr_days: null }, [null, null, null, null, null, null, 2]))
    const ttr = kpiCell(kpis, 'Time to resolve')
    expect(within(ttr).queryByRole('img')).toBeNull()
    expect(ttr.querySelector('[data-spark-hint]')).toHaveTextContent('7d · not enough data for a trend line')
    // Nothing resolved in the window: "—", never "0.0d".
    expect(ttr).toHaveTextContent(/^Time to resolve—/)
  })

  it('states this week\'s net change from the endpoint, not from the loaded rows', async () => {
    await renderTrend(trendOf({ net_last_7d: 5 }))
    const facts = Array.from(screen.getByRole('region', { name: 'Defect queue verdict' }).querySelectorAll('[data-banner-fact]'))
      .map(f => f.textContent)
    // The 20 rows were all created this week (+20 from rows); the endpoint says +5.
    expect(facts).toContain('Net this week +5')
  })

  it('one MTTR on the page: Fix velocity scores the windowed server value', async () => {
    await renderTrend(trendOf({ mttr_days: 3.5 }))
    const details = openHealthDetails()
    const tile = within(details).getByText('Fix velocity').parentElement?.parentElement as HTMLElement
    // 100 − 3.5 / 7 × 100 = 50; the loaded rows have nothing resolved (they would score 100).
    expect(tile).toHaveTextContent('Fix velocity20%50')
  })

  it('while the trend has not answered, the tiles show the loaded rows\' values and the old cap note', async () => {
    const kpis = await renderTrend(undefined)
    expect(kpiCell(kpis, 'Open defects')).toHaveTextContent(/^Open defects20$/)
    expect(document.querySelector('[data-defects-capped]')).toHaveTextContent(
      'The counts and the table cover the newest 20 of 1200 defects.',
    )
    expect(within(kpiCell(kpis, 'Time to resolve')).getByText('Time to resolve')).toHaveAttribute(
      'title', 'Mean time to resolve, over the defects resolved',
    )
  })
})

// ── W06 DEF-03: a real escape rate replaces "Unlinked to Jira" ─────────────
describe('DefectsPage — the Escape rate tile (DEF-03)', () => {
  const escapeOf = (over: Partial<DefectEscapeRateResponse> = {}): DefectEscapeRateResponse => ({
    rate: 41.7, escaped: 5, attributed: 12, in_window: 20, measured: true, reason: null,
    sample_floor: 5, window_days: 30, outcomes: { incidents: 2, rollbacks: 1, releases: 3 }, ...over,
  })

  async function renderEscape(result: InsightResult<DefectEscapeRateResponse>) {
    insights.escape.mockReturnValue(result)
    await mockRows([
      { id: 'u1', test_name: 'a', resolution_status: 'OPEN', failure_category: 'FLAKY', created_at: ago(2) },
      { id: 'u2', test_name: 'b', resolution_status: 'OPEN', failure_category: 'FLAKY', created_at: ago(2) },
      { id: 'l1', test_name: 'c', resolution_status: 'OPEN', failure_category: 'FLAKY', created_at: ago(2), jira_ticket_id: 'PAY-1' },
    ])
    renderDefects()
    return screen.findByRole('region', { name: 'Defect KPIs' })
  }

  it('reads the endpoint\'s rate, with what it counts and the window\'s production outcomes on hover', async () => {
    const kpis = await renderEscape({ data: escapeOf(), isLoading: false })
    const cell = kpiCell(kpis, 'Escape rate')
    expect(cell).toHaveTextContent(/^Escape rate41\.7%$/)
    expect(within(cell).getByText('Escape rate')).toHaveAttribute(
      'title',
      'Defects filed after a release they affect had shipped: 5 of 12 reported in the last 30 days that affect a shipped release.'
        + ' Production outcomes in the window: 2 incident(s), 1 rollback(s).',
    )
    expect(cell.closest('[data-escape-measured]')).toHaveAttribute('data-escape-measured', 'true')
  })

  it('below the sample floor reads "Not measured" with the server\'s reason — never 0 %', async () => {
    const reason = '3 defect(s) reported in the last 30 days affect a release that has shipped; 5 are needed.'
    const kpis = await renderEscape({
      data: escapeOf({ rate: null, measured: false, reason, escaped: 0, attributed: 3, outcomes: { incidents: 0, rollbacks: 0, releases: 1 } }),
      isLoading: false,
    })
    const cell = kpiCell(kpis, 'Escape rate')
    // "—" as the value and "Not measured" on the small line under it (the words
    // as the 24 px value did not fit the tile at 1280 px).
    expect(cell).toHaveTextContent(/^Escape rate—Not measured$/)
    expect(cell.querySelector('[data-metric-trend="none"]')).toHaveTextContent('Not measured')
    expect(cell.textContent).not.toMatch(/0(\.0)?%/)
    expect(within(cell).getByText('Escape rate')).toHaveAttribute(
      'title', `${reason} Production outcomes in the window: 0 incident(s), 0 rollback(s).`,
    )
    expect(cell.closest('[data-escape-measured]')).toHaveAttribute('data-escape-measured', 'false')
  })

  it('says it could not load rather than inventing a number when the request failed', async () => {
    const kpis = await renderEscape({ data: undefined, isLoading: false, error: new Error('500') })
    const cell = kpiCell(kpis, 'Escape rate')
    expect(cell).toHaveTextContent(/^Escape rate—$/)
    expect(within(cell).getByText('Escape rate')).toHaveAttribute('title', 'The escape rate could not be loaded.')
    expect(cell.closest('[data-escape-measured]')).toBeNull()
  })

  it('no tile says "Unlinked to Jira" any more; the unlinked count is still the banner\'s "No Jira ticket"', async () => {
    const kpis = await renderEscape({ data: escapeOf(), isLoading: false })
    expect(within(kpis).queryByText(/Unlinked to Jira/i)).toBeNull()
    expect(document.body.textContent).not.toMatch(/Unlinked to Jira/i)
    const facts = Array.from(screen.getByRole('region', { name: 'Defect queue verdict' }).querySelectorAll('[data-banner-fact]'))
      .map(f => f.textContent)
    expect(facts).toContain('No Jira ticket 2')
    expect(kpis.querySelectorAll('[data-metric-card]')).toHaveLength(5)
  })
})

// ── UX redesign P2: anything not built is not rendered. The workflow ribbon
// (invented "6 / 5 evidence"), the provenance footer ("3 tools", a toast-only
// "Decision trail"), the recommended-actions card (invented @release-qa /
// @releng / @team-* handles) and every toast-only CTA are gone. W06 DEF-01 and
// DEF-02 built two of them for real: the Owner column and the row menu. ─────
describe('DefectsPage — renders only what it really does (P2)', () => {
  /** An open P0, by default unowned and unlinked. */
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
    await mockRows(items)
    renderDefects()
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

  it('offers "Open in Jira" only for a row with a safe ticket URL, and the row menu on every row', async () => {
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
    // The link is the Jira key itself (the Jira cell); the Actions cell (the
    // last one) holds only the "Defect actions" menu (DefectRowActions decides
    // who sees it) -- a second "Open" button there pushed the menu out of the
    // card at 1280 px on CI's fonts.
    const bodyRows = within(screen.getByRole('table')).getAllByRole('row').slice(1)
    expect(bodyRows).toHaveLength(4)
    expect(open[0].closest('td')).toBe(rowOf('checkout linked').querySelectorAll('td')[5])
    expect(open[0]).toHaveTextContent('ACME-7')
    const actionCells = bodyRows.map(r => r.querySelectorAll('td')[7])
    expect(actionCells.flatMap(c => [...c.querySelectorAll('a')])).toHaveLength(0)
    expect(actionCells.map(c => within(c).getAllByTestId('row-actions').length)).toEqual([1, 1, 1, 1])
    // The unsafe URL renders no link anywhere in the row (the Jira cell shows the key as text).
    expect(rowOf('checkout unsafe').querySelector('a')).toBeNull()
  })

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

  it('with no P0 open (and no trend yet), the banner states this week\'s net change from the rows', async () => {
    await renderWith([p0('f1', { failure_category: 'FLAKY' }), p0('f2', { failure_category: 'FLAKY', created_at: ago(20) })])
    const verdict = screen.getByRole('region', { name: 'Defect queue verdict' })
    const facts = Array.from(verdict.querySelectorAll('[data-banner-fact]')).map((f) => f.textContent)
    // One created this week (2 days ago), none resolved this week: +1.
    expect(facts).toContain('Net this week +1')
    expect(facts.some((f) => /Oldest open P0/.test(f ?? ''))).toBe(false)
  })
})

// ── W06 DEF-02: who owns each defect, and Ownership back in the health score ─
describe('DefectsPage — the Owner column and the Ownership dimension (DEF-02)', () => {
  const defect = (id: string, extra: Record<string, unknown> = {}) => ({
    id,
    project_id: 'proj-1',
    title: `defect ${id}`,
    test_name: `checkout ${id}`,
    suite_name: 'Payments',
    failure_category: 'FLAKY',
    resolution_status: 'OPEN',
    created_at: ago(2),
    ...extra,
  })

  it('has the Owner column between Status and Jira', async () => {
    await mockRows([defect('a')])
    renderDefects()
    const table = await screen.findByRole('table', { name: 'Defects' })
    expect(within(table).getAllByRole('columnheader').map(h => h.textContent?.replace(/[↕↑↓]/g, '').trim()))
      .toEqual(['Key', 'Title', 'Severity', 'Status', 'Owner', 'Jira', 'Age', 'Actions'])
  })

  it('shows the assignee over their team, else the team over where it came from, else "Unassigned"', async () => {
    await mockRows([
      defect('assigned', { assignee_user_id: 'u-1', assignee_name: 'Priya Nair', owner_team: 'payments-team', owner_source: 'defect' }),
      defect('assigned-no-team', { assignee_user_id: 'u-2', assignee_name: 'Sam Ortiz' }),
      defect('rule', { owner_team: 'checkout-team', owner_source: 'rule' }),
      defect('map', { owner_team: 'infra-team', owner_source: 'component_map' }),
      defect('label', { owner_team: 'qa-core', owner_source: 'test_label' }),
      defect('own', { owner_team: 'own-team', owner_source: 'defect' }),
      // An ineligible assignee (service / synthetic / inactive) has no name: nobody is named.
      defect('nobody', { assignee_user_id: 'svc-1', assignee_name: null }),
    ])
    renderDefects()
    await screen.findByRole('table', { name: 'Defects' })
    const owner = (id: string) => cellOf(rowOf(`defect ${id}`), 'Owner')

    const assigned = owner('assigned').querySelector('[data-owner]') as HTMLElement
    expect(assigned).toHaveAttribute('data-owner', 'assignee')
    expect([...assigned.querySelectorAll('span')].map(s => s.textContent)).toEqual(['Priya Nair', 'payments-team'])
    expect(assigned).toHaveAttribute('title', 'Priya Nair · payments-team')
    expect(assigned.className).toContain('max-w-[160px]')
    expect(assigned.querySelector('span')?.className).toContain('truncate')

    expect([...owner('assigned-no-team').querySelectorAll('span')].map(s => s.textContent)).toEqual(['Sam Ortiz'])
    expect([...owner('rule').querySelectorAll('span')].map(s => s.textContent)).toEqual(['checkout-team', 'from ownership rules'])
    expect(owner('rule').querySelector('[data-owner]')).toHaveAttribute('title', 'checkout-team · from ownership rules')
    expect([...owner('map').querySelectorAll('span')].map(s => s.textContent)).toEqual(['infra-team', 'from the component map'])
    expect([...owner('label').querySelectorAll('span')].map(s => s.textContent)).toEqual(['qa-core', "from the test's owner label"])
    expect([...owner('own').querySelectorAll('span')].map(s => s.textContent)).toEqual(['own-team'])
    expect(owner('nobody')).toHaveTextContent(/^Unassigned$/)
    expect(owner('nobody').querySelector('[data-owner]')).toHaveAttribute('data-owner', 'none')
  })

  it('keeps an owner\'s text as text (no markup from a team name)', async () => {
    const hostile = '<img src=x onerror="alert(1)">'
    await mockRows([defect('x', { owner_team: hostile, owner_source: 'rule' })])
    renderDefects()
    await screen.findByRole('table', { name: 'Defects' })
    expect(cellOf(rowOf('defect x'), 'Owner')).toHaveTextContent(hostile)
    expect(document.querySelector('img[src="x"]')).toBeNull()
  })

  it('searches owners: the assignee and the team', async () => {
    await mockRows([
      defect('a', { assignee_name: 'Priya Nair' }),
      defect('b', { owner_team: 'checkout-team', owner_source: 'rule' }),
      defect('c'),
    ])
    renderDefects()
    const search = await screen.findByRole('searchbox', { name: 'Search defects' })
    expect(search).toHaveAttribute('placeholder', 'Search defects, Jira keys, owners…')
    const titles = () => within(screen.getByRole('table', { name: 'Defects' })).getAllByRole('row').slice(1)
      .map(r => r.querySelectorAll('td')[1].querySelector('span')?.textContent)
    fireEvent.change(search, { target: { value: 'priya' } })
    expect(titles()).toEqual(['defect a'])
    fireEvent.change(search, { target: { value: 'CHECKOUT-team' } })
    expect(titles()).toEqual(['defect b'])
  })

  it('weighs four dimensions (Ownership 20 %), in four columns, with the disclosure\'s sentence', async () => {
    // P0 open (unlinked, unowned) + P0 resolved in 2 days (linked) + P2 open
    // (linked, owned) + P3 closed (unlinked, no resolved_at).
    const created = ago(10)
    const resolved = new Date(new Date(created).getTime() + 2 * DAY).toISOString()
    const p0 = { failure_category: 'PRODUCT_BUG', ai_confidence_score: 92 }
    await mockRows([
      defect('a', p0),
      defect('b', { ...p0, jira_ticket_id: 'ACME-1', resolution_status: 'RESOLVED', created_at: created, resolved_at: resolved }),
      defect('c', { jira_ticket_id: 'ACME-2', failure_category: 'INFRA', owner_team: 'infra-team', owner_source: 'defect' }),
      defect('d', { failure_category: 'FLAKY', resolution_status: 'CLOSED' }),
    ])
    renderDefects()
    await screen.findByRole('region', { name: 'Defect queue verdict' })
    const p0Throughput = 50            // 1 of 2 P0s resolved
    const jiraCoverage = 50            // 2 of 4 linked
    const fixVelocity = 100 - (2 / 7) * 100   // MTTR 2 d on the 7-day scale
    const ownership = 50               // 1 of the 2 ACTIVE rows has a team (closed rows are not counted)
    const expected = Math.round(p0Throughput * 0.35 + jiraCoverage * 0.25 + fixVelocity * 0.20 + ownership * 0.20)
    expect(expected).toBe(54)
    expect(screen.getByRole('region', { name: 'Defect queue verdict' })).toHaveTextContent(`Queue health ${expected}/100`)
    const details = openHealthDetails()
    expect(screen.getByRole('meter', { name: 'Queue health' })).toHaveAttribute('aria-valuenow', String(expected))
    for (const [label, pct, score] of [
      ['P0 throughput', '35%', '50'], ['Jira coverage', '25%', '50'], ['Fix velocity', '20%', '71'], ['Ownership', '20%', '50'],
    ]) {
      const tile = within(details).getByText(label).parentElement?.parentElement as HTMLElement
      expect(tile, label).toHaveTextContent(`${label}${pct}${score}`)
    }
    const grid = within(details).getByText('P0 throughput').closest('.grid') as HTMLElement
    expect(grid.style.gridTemplateColumns).toBe('repeat(4, minmax(0, 1fr))')
    expect(details).toHaveTextContent(
      "Ownership counts open defects with an assignee or a team (their own, or one the project's ownership rules give them).",
    )
  })

  it('assigning the only unowned open defect raises Ownership to 100 and the composite with it', async () => {
    await mockLiveRows([
      defect('owned', { owner_team: 'payments-team', owner_source: 'defect' }),
      defect('unowned'),
      // A closed, unowned defect: it must not count against Ownership.
      defect('closed', { resolution_status: 'RESOLVED', resolved_at: ago(1) }),
    ])
    renderDefects()
    await screen.findByRole('region', { name: 'Defect queue verdict' })
    const composite = () => Number(/Queue health (\d+)\/100/.exec(
      screen.getByRole('region', { name: 'Defect queue verdict' }).textContent ?? '',
    )?.[1])
    const ownershipTile = () => within(openHealthDetailsOnce()).getByText('Ownership').parentElement?.parentElement as HTMLElement
    const before = composite()
    expect(ownershipTile()).toHaveTextContent('Ownership20%50')

    rowMenuAnswers('unowned', { ...defect('unowned'), assignee_user_id: 'u-9', assignee_name: 'Priya Nair' })

    expect(cellOf(rowOf('defect unowned'), 'Owner')).toHaveTextContent('Priya Nair')
    expect(ownershipTile()).toHaveTextContent('Ownership20%100')
    expect(composite()).toBeGreaterThan(before)
  })
})

/** Open the health Disclosure if it is closed; return its body. */
function openHealthDetailsOnce(): HTMLElement {
  const meter = screen.queryByRole('meter', { name: 'Queue health' })
  if (meter) return meter.closest('[data-disclosure]') as HTMLElement
  return openHealthDetails()
}

// ── W06 DEF-01: the row menu's answer replaces the row; INVALID is closed ──
describe('DefectsPage — row actions and the status vocabulary (DEF-01)', () => {
  const defect = (id: string, extra: Record<string, unknown> = {}) => ({
    id,
    project_id: 'proj-1',
    title: `defect ${id}`,
    test_name: `checkout ${id}`,
    severity: 'LOW',
    failure_category: 'FLAKY',
    resolution_status: 'OPEN',
    created_at: ago(2),
    ...extra,
  })

  it('hands the row menu the row as the server sent it (its project, its stored severity), not the page\'s derived row', async () => {
    await mockRows([defect('a', { priority: 'P3', owner_team: 'payments-team', owner_source: 'defect' })])
    renderDefects()
    await screen.findByRole('table', { name: 'Defects' })
    expect(rowActions.defects.get('a')).toMatchObject({
      id: 'a', project_id: 'proj-1', resolution_status: 'OPEN', severity: 'LOW', owner_team: 'payments-team',
    })
    expect(rowActions.defects.get('a')).not.toHaveProperty('statusKey')
  })

  it('a status change moves the row to its tab without a reload, then revalidates the list and the insights', async () => {
    const mutate = await mockLiveRows([defect('a'), defect('b')])
    renderDefects()
    await screen.findByRole('table', { name: 'Defects' })
    const filter = screen.getByRole('tablist', { name: 'Status filter' })
    const counts = () => within(filter).getAllByRole('tab').map(t => t.querySelector('[data-tab-count]')?.textContent)
    expect(counts()).toEqual(['2', '2', '0', '0', '0'])

    rowMenuAnswers('a', { ...defect('a'), resolution_status: 'IN_PROGRESS' })

    expect(counts()).toEqual(['2', '1', '1', '0', '0'])
    expect(cellOf(rowOf('defect a'), 'Status')).toHaveTextContent('In progress')
    fireEvent.click(within(filter).getByRole('tab', { name: /^In progress/ }))
    const rows = within(screen.getByRole('table', { name: 'Defects' })).getAllByRole('row').slice(1)
    expect(rows.map(r => r.querySelectorAll('td')[1].querySelector('span')?.textContent)).toEqual(['defect a'])
    // The cache entry was updated in place and revalidated; the trend and the escape rate are re-read.
    expect(mutate).toHaveBeenCalledWith(expect.any(Function), { revalidate: true })
    expect(insights.refresh).toHaveBeenCalledTimes(1)
  })

  it('files INVALID under Closed as "Invalid", the legacy CLOSED as "Closed", and an unknown word under Open', async () => {
    await mockRows([
      defect('invalid', { resolution_status: 'INVALID', resolved_at: ago(1) }),
      defect('closed', { resolution_status: 'CLOSED' }),
      defect('drifted', { resolution_status: 'WONT_FIX_MAYBE' }),
      defect('open'),
    ])
    renderDefects()
    await screen.findByRole('table', { name: 'Defects' })
    const filter = screen.getByRole('tablist', { name: 'Status filter' })
    expect(within(filter).getAllByRole('tab').map(t => t.querySelector('[data-tab-count]')?.textContent))
      .toEqual(['4', '2', '0', '0', '2'])
    expect(cellOf(rowOf('defect invalid'), 'Status')).toHaveTextContent(/^Invalid$/)
    expect(cellOf(rowOf('defect closed'), 'Status')).toHaveTextContent(/^Closed$/)
    expect(cellOf(rowOf('defect drifted'), 'Status')).toHaveTextContent(/^Open$/)
    fireEvent.click(within(filter).getByRole('tab', { name: /^Closed/ }))
    const rows = within(screen.getByRole('table', { name: 'Defects' })).getAllByRole('row').slice(1)
    expect(rows.map(r => r.querySelectorAll('td')[1].querySelector('span')?.textContent).sort()).toEqual(['defect closed', 'defect invalid'])
  })
})

// ── W06: `?defect=<id>` (the activity feed's link) opens on that row ───────
describe('DefectsPage — ?defect= highlights the linked row', () => {
  const defect = (id: string, extra: Record<string, unknown> = {}) => ({
    id, project_id: 'proj-1', title: `defect ${id}`, test_name: `t ${id}`, failure_category: 'FLAKY',
    resolution_status: 'OPEN', created_at: ago(2), ...extra,
  })
  const scrollIntoView = vi.fn()
  beforeEach(() => {
    scrollIntoView.mockClear()
    Element.prototype.scrollIntoView = scrollIntoView
  })

  it('shows every status, marks the row, scrolls it into view once, and unmarks it when another row is acted on', async () => {
    localStorage.setItem('tl.defects.tab', 'RESOLVED')
    await mockLiveRows([defect('d1'), defect('d2'), defect('d3', { resolution_status: 'RESOLVED', resolved_at: ago(1) })])
    renderDefects('?defect=d2')
    await screen.findByRole('table', { name: 'Defects' })

    expect(within(screen.getByRole('tablist', { name: 'Status filter' })).getByRole('tab', { name: /^All/, selected: true }))
      .toBeInTheDocument()
    expect(rowOf('defect d2')).toHaveAttribute('data-highlighted', 'true')
    expect(rowOf('defect d1')).not.toHaveAttribute('data-highlighted')
    expect(rowOf('defect d2').querySelector('td')?.style.boxShadow).toBe('inset 3px 0 0 var(--color-accent)')
    expect(scrollIntoView).toHaveBeenCalledTimes(1)
    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'center' })
    expect(scrollIntoView.mock.contexts[0]).toBe(rowOf('defect d2'))
    expect(document.querySelector('[data-defect-missing]')).toBeNull()

    // Acting on the marked row keeps the mark; acting on another row clears it.
    rowMenuAnswers('d2', { ...defect('d2'), owner_team: 'payments-team', owner_source: 'defect' })
    expect(rowOf('defect d2')).toHaveAttribute('data-highlighted', 'true')
    rowMenuAnswers('d1', { ...defect('d1'), resolution_status: 'IN_PROGRESS' })
    expect(rowOf('defect d2')).not.toHaveAttribute('data-highlighted')
    expect(scrollIntoView).toHaveBeenCalledTimes(1)
  })

  it('says so when the linked defect is not among the loaded rows', async () => {
    await mockRows([defect('d1'), defect('d2')])
    renderDefects('?defect=not-loaded')
    await screen.findByRole('table', { name: 'Defects' })
    expect(document.querySelector('[data-defect-missing]')).toHaveTextContent('That defect is not among the newest 2 loaded.')
    expect(document.querySelector('[data-highlighted]')).toBeNull()
    expect(scrollIntoView).not.toHaveBeenCalled()
  })
})

// ── UX redesign P3: the page template (02-design-spec.md §2) ─────────────
describe('DefectsPage — the page template (P3)', () => {
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
    await mockRows(items)
    mockIntegrationsConfig.mockReturnValue({ config: undefined, error: new Error('403'), isLoading: false })
    const view = renderDefects(search)
    await screen.findByRole('region', { name: 'Defect queue verdict' })
    return view
  }

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
    expect([...kpis.querySelectorAll('[data-metric-card] > p:first-child')].map(p => p.textContent))
      .toEqual(['Open defects', 'P0 / P1 open', 'Time to resolve', 'Escape rate', 'Oldest open P0'])
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
  })

  // ── UX redesign P6: the status filter is the `Tabs` primitive ──
  it('the status filter is the Tabs primitive: a tab per status with its count; its state is local, never ?tab=', async () => {
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
  async function renderWith(data: Record<string, unknown>) {
    ;(await defectsHookMock()).mockReturnValue({ data, isLoading: false, mutate: vi.fn() })
    renderDefects()
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

  it('reads the server\'s priority when the row has one (one derivation for the tiles, the trend and the table)', async () => {
    const table = await renderWith({
      items: [
        { ...ENTERED, id: 'p-1', title: 'Server says P2', severity: 'CRITICAL', priority: 'P2' },
        { ...ENTERED, id: 'p-2', title: 'Unknown word falls back', severity: 'HIGH', priority: 'URGENT' },
      ],
      total: 2, pages: 1,
    })
    const sev = (title: string) => within(table).getByText(title).closest('tr')?.querySelectorAll('td')[2].textContent?.trim()
    expect(sev('Server says P2')).toBe('P2')
    expect(sev('Unknown word falls back')).toBe('P1')
  })

  // The fallback rule (`classifySeverity`), on the SAME 12-case table the
  // backend asserts for `defect_status.priority_of` / `priority_sql`.
  const SEVERITY_CASES: [string, Record<string, unknown>, string][] = [
    ['stored CRITICAL', { severity: 'CRITICAL', failure_category: 'FLAKY' }, 'P0'],
    ['stored high (any case)', { severity: 'high', failure_category: null }, 'P1'],
    ['unrated product bug at 80', { failure_category: 'PRODUCT_BUG', ai_confidence_score: 80 }, 'P0'],
    ['unrated product bug at 79', { failure_category: 'PRODUCT_BUG', ai_confidence_score: 79 }, 'P1'],
    ['INFRASTRUCTURE', { failure_category: 'INFRASTRUCTURE' }, 'P2'],
    ['RUNNER_ERROR', { failure_category: 'RUNNER_ERROR' }, 'P2'],
    ['CI_FAILURE', { failure_category: 'CI_FAILURE' }, 'P2'],
    ['FLAKY', { failure_category: 'FLAKY' }, 'P3'],
    ['TEST_DATA', { failure_category: 'TEST_DATA' }, 'P2'],
    ['no category', { failure_category: null }, 'P2'],
    ['"product bug" with a space', { failure_category: 'product bug', ai_confidence_score: 90 }, 'P0'],
    ['product bug, no confidence', { failure_category: 'PRODUCT_BUG', ai_confidence_score: null }, 'P1'],
  ]

  it('derives the P-level of a row without one by the server\'s rule, case for case', async () => {
    expect(SEVERITY_CASES).toHaveLength(12)
    const items: Record<string, unknown>[] = SEVERITY_CASES.map(([name, fields], i) => ({
      id: `sev-${i}`, title: name, severity: null, test_name: 't', resolution_status: 'OPEN', created_at: '2026-10-08T10:00:00Z',
      ...fields,
    }))
    const table = await renderWith({ items, total: items.length, pages: 1 })
    const got = SEVERITY_CASES.map(([name]) => [name, within(table).getByText(name).closest('tr')?.querySelectorAll('td')[2].textContent?.trim()])
    expect(got).toEqual(SEVERITY_CASES.map(([name, , want]) => [name, want]))
  })

  // W29a UIX-07: the severity chips read the ONE severity palette — the text is
  // the level's legible ink (a P2 chip's is the info ink), the dot its hue.
  it('draws each severity chip from SEVERITY_STYLE', async () => {
    const table = await renderWith({
      items: (['P0', 'P1', 'P2', 'P3'] as const).map((p) => ({ ...ENTERED, id: `chip-${p}`, title: `Chip ${p}`, severity: null, priority: p })),
      total: 4, pages: 1,
    })
    const chip = (p: string) => within(table).getByText(`Chip ${p}`).closest('tr')?.querySelector('[data-severity]') as HTMLElement
    expect(chip('P0').style.color).toBe('var(--ui-danger-ink)')
    expect(chip('P1').style.color).toBe('var(--ui-warning-ink)')
    expect(chip('P2').style.color).toBe('var(--ui-info-ink)')
    expect(chip('P3').style.color).toBe('var(--color-text-muted)')
    expect((chip('P2').querySelector('i') as HTMLElement).style.background).toBe('var(--ui-info)')
    for (const p of ['P0', 'P1', 'P2', 'P3']) expect(chip(p).getAttribute('style'), p).not.toMatch(/--status-/)
  })

  it('says when the table covers only the newest defects', async () => {
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

// ── W06 DEF-06: an approval that tried to file a Jira ticket says how it went ─
describe('DefectsPage — the Jira filing chip (DEF-06)', () => {
  const defect = (id: string, extra: Record<string, unknown> = {}) => ({
    id, project_id: 'proj-1', title: `defect ${id}`, test_name: `t ${id}`, failure_category: 'FLAKY',
    resolution_status: 'OPEN', created_at: ago(2), ...extra,
  })

  it('names what happened instead of "unlinked", with the reason on hover', async () => {
    await mockRows([
      defect('offline', { jira_filing_status: 'not_filed', jira_filing_reason: 'offline_mode' }),
      defect('off', { jira_filing_status: 'not_filed', jira_filing_reason: 'disabled' }),
      defect('unconfigured', { jira_filing_status: 'not_filed', jira_filing_reason: 'not_configured' }),
      defect('failed', { jira_filing_status: 'failed', jira_filing_reason: 'Jira answered HTTP 400 for QA.' }),
      defect('unknown', { jira_filing_status: 'unknown', jira_filing_reason: 'Jira did not confirm the issue.' }),
      defect('never'),
      defect('filed', { jira_filing_status: 'filed', jira_ticket_id: 'QA-12' }),
    ])
    renderDefects()
    await screen.findByRole('table', { name: 'Defects' })
    const jira = (id: string) => cellOf(rowOf(`defect ${id}`), 'Jira')
    const chip = (id: string) => jira(id).querySelector('span') as HTMLElement

    expect(chip('offline')).toHaveTextContent(/^not filed \(offline\)$/)
    expect(chip('offline')).toHaveAttribute('title', 'offline_mode')
    expect(chip('offline')).toHaveAttribute('data-jira-filing', 'not_filed')
    expect(chip('off')).toHaveTextContent(/^not filed \(Jira off\)$/)
    expect(chip('unconfigured')).toHaveTextContent(/^not filed \(Jira not configured\)$/)
    expect(chip('failed')).toHaveTextContent(/^filing failed$/)
    expect(chip('failed')).toHaveAttribute('title', 'Jira answered HTTP 400 for QA.')
    expect(chip('unknown')).toHaveTextContent(/^filing unconfirmed$/)
    expect(chip('never')).toHaveTextContent(/^unlinked$/)
    expect(chip('never')).not.toHaveAttribute('title')
    // A filed (linked) row shows its key, not a filing chip.
    expect(jira('filed')).toHaveTextContent('QA-12')
    expect(jira('filed').querySelector('[data-jira-filing]')).toBeNull()
  })
})

// ── W29b UIX-06 / UIX-09: the one table, the one panel, the one tile ─────
describe('DefectsPage — the one table, panel and KPI tile (W29b)', () => {
  const defect = (id: string, extra: Record<string, unknown> = {}) => ({
    id, project_id: 'proj-1', title: `defect ${id}`, test_name: `t ${id}`, failure_category: 'FLAKY',
    resolution_status: 'OPEN', created_at: ago(2), ...extra,
  })

  it('is the compact Table: Severity, Status and Age sort through buttons in th[aria-sort]; Age is a number column', async () => {
    await mockRows([defect('a'), defect('b', { created_at: ago(9) })])
    renderDefects()
    const table = await screen.findByRole('table', { name: 'Defects' })
    // compact, not dense: the sort headers' width at 1280 px (DefectsPage.tsx, the comment on the Table).
    expect(table).toHaveAttribute('data-density', 'compact')
    const headers = within(table).getAllByRole('columnheader')
    const header = (name: string) => headers.find(h => h.textContent === name) as HTMLElement
    for (const h of headers) expect(h).toHaveClass('th')
    // Sortable headers: a real button, the label exactly (a chevron icon, never a text arrow), aria-sort on the th.
    expect(header('Age')).toHaveAttribute('aria-sort', 'descending')
    expect(header('Severity')).toHaveAttribute('aria-sort', 'none')
    expect(header('Status')).toHaveAttribute('aria-sort', 'none')
    for (const name of ['Severity', 'Status', 'Age']) {
      const button = within(header(name)).getByRole('button', { name })
      expect(button).toHaveClass('th-sort')
      expect(button.textContent).toBe(name)
    }
    for (const name of ['Key', 'Title', 'Owner', 'Jira', 'Actions']) {
      expect(within(header(name)).queryByRole('button')).toBeNull()
      expect(header(name)).not.toHaveAttribute('aria-sort')
    }
    // Age: right-aligned with tabular figures, in its header AND its cells.
    expect(header('Age')).toHaveClass('th-num')
    expect(cellOf(rowOf('defect a'), 'Age')).toHaveClass('td', 'td-num')
    expect(cellOf(rowOf('defect a'), 'Title')).not.toHaveClass('td-num')
    // Severity sorts P0 first, so its header reads ascending once chosen.
    fireEvent.click(within(header('Severity')).getByRole('button', { name: 'Severity' }))
    expect(header('Severity')).toHaveAttribute('aria-sort', 'ascending')
    expect(header('Age')).toHaveAttribute('aria-sort', 'none')
    // Body rows are the table's rows; nothing opens one.
    expect(rowOf('defect a')).toHaveClass('table-row', 'cursor-default')
  })

  it('marks the ?defect= row selected as well as highlighted, and only that row', async () => {
    await mockRows([defect('d1'), defect('d2')])
    renderDefects('?defect=d2')
    await screen.findByRole('table', { name: 'Defects' })
    expect(rowOf('defect d2')).toHaveAttribute('data-selected', '')
    expect(rowOf('defect d2')).toHaveAttribute('data-highlighted', 'true')
    expect(rowOf('defect d1')).not.toHaveAttribute('data-selected')
    // The selected row's tint is the class's (no inline background over it).
    expect(rowOf('defect d2').style.background).toBe('')
  })

  it('draws its cards as Panels with an h3, and the queue-health dimensions as sm tiles with no sheen', async () => {
    await mockRows([defect('a', { severity: 'CRITICAL' })])
    renderDefects()
    const table = await screen.findByRole('table', { name: 'Defects' })
    const tablePanel = table.closest('[data-panel]') as HTMLElement
    expect(tablePanel).toHaveClass('card')
    expect(within(tablePanel).getByRole('heading', { level: 3 })).toHaveTextContent(/^All defects/)
    const sections = screen.getByRole('tabpanel', { name: 'Where defects live' })
    expect(within(sections).getByRole('heading', { level: 3, name: 'Where defects live' }).closest('[data-panel]')).not.toBeNull()

    const details = openHealthDetails()
    const tiles = details.querySelectorAll('[data-metric-card="stat"]')
    expect(tiles).toHaveLength(4)
    for (const tile of tiles) {
      expect(tile.getAttribute('style') ?? '').not.toMatch(/rgba\(/)
      expect(tile.querySelector('[data-metric-bar]')).not.toBeNull()
    }
    expect(details.querySelector('[data-metric-card="compact"]')).toBeNull()
    expect(details.innerHTML).not.toMatch(/rgba\(/)
  })
})
