/**
 * FAIL-05: the Runs toolbar's search, branch and AI-verdict filters. The URL
 * carries them (`?q=&branch=&verdict=`), so a reload or a shared link
 * restores the view; the search asks once per pause, not once per key; the
 * branch select lists one project's branches and is absent in All Projects.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { TestRun } from '@/types/runs'
import { useRuns } from '@/hooks/useRuns'
import { getData } from '@/services/http'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import { useTimeWindowStore } from '@/store/timeWindowStore'
import RunsPage, { SEARCH_DEBOUNCE_MS } from './RunsPage'

vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(),
  useMostRecentRun: vi.fn(() => ({ data: undefined })),
}))
vi.mock('@/hooks/useSuiteOptions', () => ({
  useSuiteOptions: () => ({ options: [], isLoading: false }),
}))
const scope = vi.hoisted(() => ({ projectId: 'proj-1' as string }))
vi.mock('@/store/projectStore', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/store/projectStore')>()
  const state = () => ({
    activeProjectId: scope.projectId,
    activeProject: scope.projectId === actual.ALL_PROJECTS_ID ? null : { id: scope.projectId, name: 'Project One' },
    projects: [{ id: 'proj-1', name: 'Project One' }],
  })
  return {
    ...actual,
    useProjectStore: (selector?: (s: ReturnType<typeof state>) => unknown) => (selector ? selector(state()) : state()),
  }
})
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }))
vi.mock('@/hooks/useFeatureFlags', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/hooks/useFeatureFlags')>()),
  useFeatureEnabled: () => false,
}))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => ({ isQaEngineer: false }) }))
vi.mock('@/services/aiHistoryService', () => ({ aiHistoryService: { getPreviousRun: vi.fn() } }))
vi.mock('@/services/agentService', () => ({ default: {} }))
vi.mock('@/services/http', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/http')>()),
  getData: vi.fn(async (url: string, config?: { params?: Record<string, unknown> }) => {
    const ids = ([] as string[]).concat((config?.params?.run_id as string[] | undefined) ?? [])
    if (url === '/api/v1/runs/ai-verdicts') return { items: [], unavailable: ids, max_ids: 25 }
    if (url === '/api/v1/runs/failure-signatures') return { items: [], unavailable: ids, max_ids: 50, rows_per_run_cap: 500 }
    if (url === '/api/v1/runs/branches') {
      return {
        items: [{ branch: 'main', runs: 3, last_run_at: null }, { branch: 'release/2.4', runs: 1, last_run_at: null }],
        total: 2, truncated: false, window_days: 30,
      }
    }
    throw new Error(`unexpected request ${url}`)
  }),
}))

function run(i: number, over: Partial<TestRun> = {}): TestRun {
  return {
    id: `run-${i}`, build_number: 1230 + i, status: 'PASSED', branch: 'main',
    passed_tests: 10, failed_tests: 0, skipped_tests: 0, total_tests: 10, pass_rate: 100,
    created_at: new Date(Date.now() - i * 3_600_000).toISOString(),
    ...over,
  }
}

/** The current URL's query string, rendered so a test can read it. */
function Probe() {
  return <output data-testid="where">{useLocation().search}</output>
}
const url = () => new URLSearchParams(screen.getByTestId('where').textContent ?? '')

function renderPage(path = '/runs', runs: TestRun[] = [run(1), run(2)]) {
  vi.mocked(useRuns).mockReturnValue({
    data: { items: runs, total: runs.length, page: 1, size: 500, pages: 1 },
    isLoading: false,
    error: undefined,
    mutate: vi.fn(),
  } as never)
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false }}>
      <MemoryRouter initialEntries={[path]}>
        <Routes><Route path="*" element={<><RunsPage /><Probe /></>} /></Routes>
      </MemoryRouter>
    </SWRConfig>,
  )
}

const runsParams = () => vi.mocked(useRuns).mock.calls.map(([params]) => (params ?? {}) as Record<string, unknown>)
const lastParams = () => runsParams()[runsParams().length - 1]
const searchBox = () => screen.getByRole('searchbox', { name: 'Search runs' }) as HTMLInputElement
const branchSelect = () => screen.queryByRole('combobox', { name: 'Branch' }) as HTMLSelectElement | null
const verdictSelect = () => screen.getByRole('combobox', { name: 'AI verdict' }) as HTMLSelectElement
const branchRequests = () => vi.mocked(getData).mock.calls.filter(([url]) => url === '/api/v1/runs/branches')

beforeEach(() => {
  scope.projectId = 'proj-1'
  useTimeWindowStore.setState({ days: 30 })
  vi.mocked(useRuns).mockClear()
  vi.mocked(getData).mockClear()
})

describe('RunsPage — the toolbar filters (FAIL-05)', () => {
  it('lays the three new controls out after the suite, in the contract\'s words', () => {
    renderPage()
    const toolbar = document.querySelector('[data-runs-toolbar]') as HTMLElement
    expect(within(toolbar).getByRole('searchbox', { name: 'Search runs' })).toHaveAttribute('placeholder', 'Build, branch, job or commit…')
    expect(Array.from(verdictSelect().options, (o) => o.textContent)).toEqual(['Any verdict', 'Go', 'Conditional', 'No-Go', 'No verdict'])
    // An unfiltered page asks exactly what it asked before: no q / branch / recommendation key at all.
    expect(Object.keys(lastParams()).sort()).toEqual(['days', 'page', 'size'])
  })

  it('typing asks ONCE after the pause, not once per key, and the URL gains q', async () => {
    renderPage()
    for (const text of ['1', '12', '123', '1234']) fireEvent.change(searchBox(), { target: { value: text } })
    expect(searchBox().value).toBe('1234')
    // useRuns records its params while React renders; the probe shows the URL
    // only once that render commits. Wait for both, or a slow runner reads the
    // URL in between.
    await waitFor(() => {
      expect(lastParams()).toMatchObject({ q: '1234' })
      expect(url().get('q')).toBe('1234')
    }, { timeout: SEARCH_DEBOUNCE_MS * 5 })
    const asked = runsParams().map((p) => p.q).filter((q) => q !== undefined)
    expect(new Set(asked)).toEqual(new Set(['1234']))
  })

  it('Escape clears the search at once', async () => {
    renderPage('/runs?q=239')
    expect(searchBox().value).toBe('239')
    fireEvent.keyDown(searchBox(), { key: 'Escape' })
    expect(searchBox().value).toBe('')
    await waitFor(() => expect(url().has('q')).toBe(false))
    expect(lastParams().q).toBeUndefined()
  })

  it('a link with ?q=&branch=&verdict= restores all three controls and asks with them', async () => {
    renderPage('/runs?q=1234&branch=main&verdict=NO_GO')
    expect(searchBox().value).toBe('1234')
    expect(verdictSelect().value).toBe('NO_GO')
    await waitFor(() => expect(branchSelect()?.value).toBe('main'))
    expect(lastParams()).toMatchObject({ q: '1234', branch: 'main', recommendation: 'NO_GO' })
    expect(screen.getByText(/· filtered ·/)).toBeInTheDocument()
  })

  it('picking a branch and a verdict writes the URL and asks with branch + recommendation', async () => {
    renderPage()
    await waitFor(() => expect(Array.from(branchSelect()?.options ?? [], (o) => o.textContent)).toEqual(['All branches', 'main', 'release/2.4']))
    expect(branchRequests()[0][1]).toMatchObject({ params: { project_id: 'proj-1', days: 30 } })
    fireEvent.change(branchSelect() as HTMLSelectElement, { target: { value: 'main' } })
    fireEvent.change(verdictSelect(), { target: { value: 'NO_GO' } })
    await waitFor(() => {
      expect(lastParams()).toMatchObject({ branch: 'main', recommendation: 'NO_GO' })
      expect(url().get('branch')).toBe('main')
      expect(url().get('verdict')).toBe('NO_GO')
    })
    fireEvent.change(verdictSelect(), { target: { value: 'none' } })
    await waitFor(() => expect(lastParams()).toMatchObject({ recommendation: 'none' }))
  })

  it('in All Projects there is no branch select, no branch request, and a URL branch is not applied', async () => {
    scope.projectId = ALL_PROJECTS_ID
    renderPage('/runs?branch=main')
    expect(branchSelect()).toBeNull()
    expect(lastParams().branch).toBeUndefined()
    await new Promise((r) => setTimeout(r, 0))
    expect(branchRequests()).toEqual([])
  })

  it('an unknown verdict in the URL is ignored and dropped', async () => {
    renderPage('/runs?verdict=MAYBE&q=7')
    expect(verdictSelect().value).toBe('')
    expect(lastParams().recommendation).toBeUndefined()
    await waitFor(() => expect(url().has('verdict')).toBe(false))
    expect(url().get('q')).toBe('7')
  })

  it('nothing matches: "No runs match these filters." and Clear filters empties all three', async () => {
    renderPage('/runs?q=nope&verdict=GO', [])
    const empty = document.querySelector('[data-runs-empty]') as HTMLElement
    expect(empty).toHaveTextContent('No runs match these filters.')
    fireEvent.click(within(empty).getByRole('button', { name: 'Clear filters' }))
    await waitFor(() => expect(url().toString()).toBe(''))
    expect(searchBox().value).toBe('')
    expect(lastParams().q).toBeUndefined()
    expect(lastParams().recommendation).toBeUndefined()
    expect(document.querySelector('[data-runs-empty]')).toHaveTextContent('No runs in the window.')
  })

  it('a filter change resets the table to its first page', async () => {
    renderPage('/runs', Array.from({ length: 30 }, (_, i) => run(i + 1)))
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    expect(screen.getByText(/Page 2 of 2/)).toBeInTheDocument()
    fireEvent.change(verdictSelect(), { target: { value: 'GO' } })
    await waitFor(() => expect(screen.getByText(/Page 1 of 2/)).toBeInTheDocument())
  })
})
