/**
 * VIZ-504 section: the flag matrix, the requests it makes (and does not), and
 * what a group activation opens. Real seam, real fetch pipeline (`chartGet` ->
 * validator -> state), the HTTP client mocked.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { DrillLevel } from '@/lib/viz/contracts'
import { ChartAnnouncerProvider } from '@/components/charts/ChartAnnouncer'
import type { CatalogParams } from '@/components/charts/chartCatalogSources'
import {
  clustersBody,
  failureGroupsResponse,
  HOSTILE_GROUP_LABELS,
} from '@/components/charts/failureGroups/failureGroups.fixtures'
import { __resetChartConcurrency } from '@/services/chartApi'
import type { RowsPanelProps } from './RowsPanel.model'
import FailureGroupsSection, { CLUSTERS_TAB, FailureGroupsSection as Named, GROUPS_TAB } from './FailureGroupsSection'

const GROUPS_URL = '/api/v1/analytics/failure-groups'
const CLUSTERS_URL = '/api/v1/analytics/systemic-clusters'
const CATALOGUE = 'viz_chart_data_api'
const ADVANCED = 'viz_advanced_charts'

const get = vi.hoisted(() => vi.fn())
vi.mock('@/services/api', () => ({ api: { get } }))

const flags = vi.hoisted(() => ({ values: {} as Record<string, boolean> }))
vi.mock('@/hooks/useFeatureFlags', () => ({
  useFeatureEnabled: (key: string) => flags.values[key] ?? false,
  useFeatureFlagStatus: (key: string) => flags.values[key],
}))
vi.mock('./useEverHadRun', () => ({ useEverHadRun: (enabled: boolean) => (enabled ? true : null) }))
const project = vi.hoisted(() => ({ id: 'p1' }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: (state: { activeProjectId: string }) => unknown) => selector({ activeProjectId: project.id }),
}))
vi.mock('@/hooks/useReleaseScope', () => ({ useReleaseScope: () => null }))

/** The drill URL, controlled: what the section reads and what it asks for. */
const drill = vi.hoisted(() => ({
  rows: [] as DrillLevel[],
  owner: 'failures-groups' as string | null,
  openRows: vi.fn(),
  closeRows: vi.fn(),
}))
vi.mock('@/hooks/useDrillPath', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/hooks/useDrillPath')>()),
  useDrillPath: () => ({
    path: [],
    rows: drill.rows,
    rowsOwner: drill.owner,
    dropped: [],
    drill: vi.fn(),
    truncate: vi.fn(),
    openRows: drill.openRows,
    closeRows: drill.closeRows,
  }),
}))
const rowsPanels = vi.hoisted(() => [] as RowsPanelProps[])
vi.mock('./RowsPanel', () => ({
  default: (props: RowsPanelProps) => {
    rowsPanels.push(props)
    return props.selectors.length > 0 ? <div data-testid="rows-panel">{props.title}</div> : null
  },
}))

let groupsBody: unknown
let clusters: unknown

const calls = (url: string): CatalogParams[] =>
  get.mock.calls.filter(([called]) => called === url).map(([, config]) => (config as { params: CatalogParams }).params)

const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })

function renderSection(days = 30) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <MemoryRouter>
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 2000, revalidateOnFocus: false }}>
        <ChartAnnouncerProvider>{children}</ChartAnnouncerProvider>
      </SWRConfig>
    </MemoryRouter>
  )
  return render(<FailureGroupsSection days={days} suiteFilter={null} />, { wrapper })
}

beforeEach(() => {
  flags.values = { [CATALOGUE]: true, [ADVANCED]: true }
  project.id = 'p1'
  drill.rows = []
  drill.owner = 'failures-groups'
  drill.openRows.mockReset()
  drill.closeRows.mockReset()
  rowsPanels.length = 0
  groupsBody = failureGroupsResponse({ groups: 6, hostile: true })
  clusters = clustersBody()
  get.mockReset()
  get.mockImplementation((url: string) => {
    const body = url === GROUPS_URL ? groupsBody : url === CLUSTERS_URL ? clusters : undefined
    return body
      ? Promise.resolve({ data: body, headers: { 'x-request-id': 'req-1' } })
      : Promise.reject(Object.assign(new Error('no fixture'), { response: { status: 500, data: {} } }))
  })
  __resetChartConcurrency()
})
afterEach(() => vi.restoreAllMocks())

describe('FailureGroupsSection', () => {
  it('keeps the pinned contract: a named export and the same default', () => {
    expect(Named).toBe(FailureGroupsSection)
  })

  it.each([
    ['both off', {}],
    ['only the catalogue flag', { [CATALOGUE]: true }],
    ['only the advanced flag', { [ADVANCED]: true }],
  ])('%s: renders nothing and asks nothing', async (_name, values) => {
    flags.values = values
    renderSection()
    await settle()
    expect(document.querySelector('[data-catalogue-section]')).toBeNull()
    expect(screen.queryByRole('tab')).toBeNull()
    expect(get).not.toHaveBeenCalled()
  })

  it('both flags: ONE groups request, links included, the window clamped to 90 days', async () => {
    renderSection(365)
    await settle()
    expect(calls(GROUPS_URL)).toEqual([{ include: 'edges', project_id: 'p1', days: 90 }])
    expect(calls(CLUSTERS_URL)).toEqual([])
    expect(screen.getByRole('tab', { name: GROUPS_TAB })).toHaveAttribute('aria-selected', 'true')
    expect(document.querySelector('[data-group-plot="bubbles"]')).not.toBeNull()
    expect(screen.getByRole('heading', { name: 'Failures grouped by error message' })).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
  })

  it('a name in the table opens the group panel; its "View rows" opens the rows by signature and closes the panel', async () => {
    renderSection()
    await settle()
    fireEvent.click(screen.getByRole('button', { name: HOSTILE_GROUP_LABELS[0] }))
    const panel = screen.getByRole('complementary', { name: 'Failure group #1' })
    fireEvent.click(within(panel).getByRole('button', { name: 'View rows' }))
    expect(drill.openRows).toHaveBeenCalledWith('failures-groups', [{ dimension: 'error_signature', value: 'error #0: assertion' }])
    expect(screen.queryByRole('complementary', { name: 'Failure group #1' })).toBeNull()
  })

  it('Enter on a bubble opens its panel ("drill"); its "View rows" button opens the rows', async () => {
    // jsdom lays nothing out: give the plot a width so the circles are drawn and walked.
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      width: 900,
      height: 400,
      x: 0,
      y: 0,
      top: 0,
      left: 0,
      right: 900,
      bottom: 400,
      toJSON: () => ({}),
    } as DOMRect)
    renderSection()
    await settle()
    const surface = document.querySelector('[data-group-plot]') as HTMLElement
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    fireEvent.keyDown(surface, { key: 'Enter' })
    expect(screen.getByRole('complementary', { name: 'Failure group #2' })).toBeInTheDocument()
    fireEvent.click(
      within(document.querySelector('[data-mark-actions]') as HTMLElement).getByRole('button', { name: 'View rows' }),
    )
    expect(drill.openRows).toHaveBeenLastCalledWith('failures-groups', [
      { dimension: 'error_signature', value: 'error #1: timeout' },
    ])
  })

  it('the rows panel: its own selectors only, metric=failures by signature, the page scope WITHOUT include, the mark’s count', async () => {
    drill.rows = [{ dimension: 'error_signature', value: 'error #0: assertion' }]
    renderSection()
    await settle()
    const last = rowsPanels[rowsPanels.length - 1] as RowsPanelProps
    expect(last.selectors).toEqual(drill.rows)
    expect(last.chart).toEqual({ metric: 'failures', groupBy: ['error_signature'] })
    expect(last.scope).toEqual({ project_id: 'p1', days: 30 })
    expect(last.title).toBe(HOSTILE_GROUP_LABELS[0])
    expect(last.expected).toEqual({ y: 400, n: null, asOf: '2026-09-08T12:00:00Z' })
    last.onClose()
    expect(drill.closeRows).toHaveBeenCalled()
  })

  it("another host's rows (the ladder's) do not open this panel", async () => {
    drill.rows = [{ dimension: 'suite', value: 'payments' }]
    renderSection()
    await settle()
    expect((rowsPanels[rowsPanels.length - 1] as RowsPanelProps).selectors).toEqual([])
    expect(screen.queryByTestId('rows-panel')).toBeNull()
  })

  it('a signature selection another host owns (the URL owner entry) does not open this panel (FK4-1)', async () => {
    drill.rows = [{ dimension: 'error_signature', value: 'error #0: assertion' }]
    drill.owner = 'failures-drill'
    renderSection()
    await settle()
    expect((rowsPanels[rowsPanels.length - 1] as RowsPanelProps).selectors).toEqual([])
  })

  it('a shared rows link for a group not in this answer still opens, named by its signature', async () => {
    drill.rows = [{ dimension: 'error_signature', value: 'gone #' }]
    renderSection()
    await settle()
    const last = rowsPanels[rowsPanels.length - 1] as RowsPanelProps
    expect(last.title).toBe('gone #')
    expect(last.expected).toBeNull()
  })

  it('the clusters tab asks only when opened, without a window, and draws the list', async () => {
    renderSection()
    await settle()
    expect(calls(CLUSTERS_URL)).toEqual([])
    fireEvent.mouseDown(screen.getByRole('tab', { name: CLUSTERS_TAB }))
    await settle()
    expect(calls(CLUSTERS_URL)).toEqual([{ project_id: 'p1' }])
    expect(screen.getByRole('heading', { name: 'Tests that fail together (last 60 days, whole project)' })).toBeInTheDocument()
    expect(document.querySelectorAll('[data-cluster-key]')).toHaveLength(2)
  })

  it('before the project resolves: nothing asked, the frame holds its place', async () => {
    project.id = null as unknown as string
    renderSection()
    await settle()
    expect(get).not.toHaveBeenCalled()
    expect(document.querySelector('[data-chart-state]')).toHaveAttribute('data-chart-state', 'loading')
    fireEvent.mouseDown(screen.getByRole('tab', { name: CLUSTERS_TAB }))
    await settle()
    expect(get).not.toHaveBeenCalled()
  })

  it('the panel closes; more groups than the server sends are stated as truncated', async () => {
    const body = failureGroupsResponse({ groups: 6 })
    groupsBody = { ...body, meta: { ...body.meta, truncated: true, truncated_total: 340 } }
    renderSection()
    await settle()
    expect(document.querySelector('[data-chart-truncation]')).toHaveTextContent('Showing top 6 of 340')
    fireEvent.click(screen.getByRole('button', { name: 'Error 0: AssertionError at step 0' }))
    fireEvent.click(screen.getByRole('button', { name: 'Close panel' }))
    expect(screen.queryByRole('complementary', { name: 'Failure group #1' })).toBeNull()
  })

  it('a body with no total_failures and no group is empty', async () => {
    const body = failureGroupsResponse({ groups: 0 })
    const { total_failures: _dropped, ...series } = body.series as unknown as Record<string, unknown>
    groupsBody = { ...body, series }
    renderSection()
    await settle()
    expect(document.querySelector('[data-chart-state]')).toHaveAttribute('data-chart-state', 'filtered-empty')
  })

  it('every failure seen once: drawn with its roll-ups, not "no data"', async () => {
    groupsBody = failureGroupsResponse({ groups: 0 })
    renderSection()
    await settle()
    expect(document.querySelector('[data-group-none]')).not.toBeNull()
    expect(document.querySelector('[data-chart-state]')).toHaveAttribute('data-chart-state', 'ready')
  })

  it('nothing failed at all: the frame says nothing matches', async () => {
    const body = failureGroupsResponse({ groups: 0 })
    groupsBody = { ...body, series: { ...body.series, total_failures: 0 } }
    renderSection()
    await settle()
    expect(document.querySelector('[data-chart-state]')).toHaveAttribute('data-chart-state', 'filtered-empty')
  })

  it('a body that is not a graph is an error frame naming the request, never half-drawn', async () => {
    groupsBody = { meta: null, series: { kind: 'tree', nodes: [] } }
    renderSection()
    await settle()
    expect(document.querySelector('[data-chart-error-kind="invalid-payload"]')).not.toBeNull()
    expect(document.querySelector('[data-group-plot]')).toBeNull()
  })

  it('All Projects: the groups are asked without a project; the clusters are not asked at all', async () => {
    project.id = '__ALL__'
    renderSection()
    await settle()
    expect(calls(GROUPS_URL)).toEqual([{ include: 'edges', days: 30 }])
    fireEvent.mouseDown(screen.getByRole('tab', { name: CLUSTERS_TAB }))
    await settle()
    expect(calls(CLUSTERS_URL)).toEqual([])
    expect(screen.getByText('systemic clusters are computed per project; choose a project to see them')).toBeInTheDocument()
  })
})
