/**
 * W14 AGT-12 (`W14-contract.md` §2.4 test 8, §0.10, §0.13, §0.25): the Runs page's bulk actions.
 *
 *  - Bulk deep (a selection) and Deep all failed are ONE `bulkTriggerPipelines(ids, 'deep')` each — the server
 *    applies the deep gate once — and nothing is fanned out through `triggerDeepPipeline`;
 *  - a refused batch (W04's gate: one 422) is the server's sentence as a `role="note"` line, never a toast;
 *  - "Re-trigger all matching (N)" asks first (an `InlineConfirm` naming the list's COUNT), then sends the list's
 *    OWN filter (`filterFromPage` over the very params `useRuns` received) with a client token;
 *  - a bulk answer with a job opens the progress panel and writes `?bulk_job=`; one without (`tracking: false`)
 *    says it cannot be tracked; `?bulk_job=<id>` on load reopens the panel, a value that is not an id asks nothing.
 *
 * `RunsPage.test.tsx` (the one-argument "Trigger all failed" call) and `RunsPage.inPage.test.tsx` (a
 * three-function agentService mock) stay unedited.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useEffect } from 'react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { TestRun } from '@/types/runs'
import { useRuns } from '@/hooks/useRuns'
import { useTimeWindowStore } from '@/store/timeWindowStore'
import toast from 'react-hot-toast'
import agentService from '@/services/agentService'
import { filterFromPage } from '@/components/agents/bulkJobModel'
import RunsPage from './RunsPage'

vi.mock('@/hooks/useRuns', () => ({ useRuns: vi.fn(), useMostRecentRun: vi.fn(() => ({ data: undefined })) }))
vi.mock('@/hooks/useSuiteOptions', () => ({ useSuiteOptions: () => ({ options: [], isLoading: false }) }))
vi.mock('@/store/projectStore', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/store/projectStore')>()
  const state = {
    activeProjectId: 'proj-1',
    activeProject: { id: 'proj-1', name: 'Project One' },
    projects: [{ id: 'proj-1', name: 'Project One' }],
  }
  return { ...actual, useProjectStore: (selector?: (s: typeof state) => unknown) => (selector ? selector(state) : state) }
})
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }))
vi.mock('@/hooks/useFeatureFlags', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/hooks/useFeatureFlags')>()),
  useFeatureEnabled: () => false,
}))
const perms = vi.hoisted(() => ({ isQaEngineer: true }))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => perms }))
vi.mock('@/services/aiHistoryService', () => ({ aiHistoryService: { getPreviousRun: vi.fn() } }))
vi.mock('@/services/agentService', () => ({
  default: {
    triggerPipeline: vi.fn(),
    triggerDeepPipeline: vi.fn(),
    bulkTriggerPipelines: vi.fn(),
    bulkTriggerFiltered: vi.fn(),
    getBulkJob: vi.fn(),
    cancelBulkJob: vi.fn(),
  },
  newClientToken: () => '0123456789abcdef0123456789abcdef',
}))
vi.mock('@/services/http', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/http')>()),
  getData: vi.fn(async (url: string) => {
    if (url === '/api/v1/runs/ai-verdicts') return { items: [], unavailable: [], max_ids: 25 }
    if (url === '/api/v1/runs/failure-signatures') return { items: [], unavailable: [], max_ids: 50, rows_per_run_cap: 500 }
    if (url === '/api/v1/runs/branches') return { items: [], total: 0, truncated: false, window_days: 30 }
    throw new Error(`unexpected request ${url}`)
  }),
}))

const JOB_ID = 'b0000000-0000-4000-8000-000000000001'
const NOW = Date.now()
const hoursAgo = (h: number) => new Date(NOW - h * 3_600_000).toISOString()
const run = (i: number, over: Partial<TestRun> = {}): TestRun => ({
  id: `run-${i}`, build_number: 100 + i, status: 'FAILED', passed_tests: 8, failed_tests: 2, skipped_tests: 0,
  total_tests: 10, pass_rate: 80, created_at: hoursAgo(40 - i), ...over,
}) as TestRun
const WINDOW: TestRun[] = [run(4), run(3), run(2), run(1, { status: 'PASSED', failed_tests: 0, passed_tests: 10, pass_rate: 100 })]
/** The list answer's COUNT: more runs match than the 4 fetched rows. */
const TOTAL = 37

let location = ''
function Probe() {
  const { search } = useLocation()
  useEffect(() => {
    location = search
  })
  return null
}

function renderRunsPage(path = '/runs') {
  vi.mocked(useRuns).mockReturnValue({
    data: { items: WINDOW, total: TOTAL, page: 1, size: 500, pages: 1 }, isLoading: false, error: undefined, mutate: vi.fn(),
  } as never)
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false }}>
      <MemoryRouter initialEntries={[path]}><RunsPage /><Probe /></MemoryRouter>
    </SWRConfig>,
  )
}

const ACTIVE_JOB = {
  bulk_job_id: JOB_ID, workflow_type: 'deep', source: 'selection', state: 'active', counters_source: 'redis',
  requested: 2, queued: 2, skipped_in_progress: 0, not_found: 0, matched: null, truncated: false,
  progress: { started: 0, in_progress: 0, completed: 0, passed: 0, failed: 0, not_started: 2 }, note: null,
}
const accepted = (over: Record<string, unknown> = {}) => ({
  queued: 2, not_found: 0, workflow_type: 'deep', not_found_ids: [], requested: 2, skipped_in_progress: 0,
  skipped_in_progress_ids: [], source: 'selection', matched: null, truncated: false, bulk_job_id: JOB_ID,
  tracking: true, replayed: false, ...over,
})
const openMenu = () => fireEvent.click(screen.getByRole('button', { name: 'More actions' }))

beforeEach(() => {
  perms.isQaEngineer = true
  location = ''
  useTimeWindowStore.setState({ days: 30 })
  for (const fn of Object.values(agentService)) vi.mocked(fn as () => unknown).mockReset()
  vi.mocked(agentService.getBulkJob).mockResolvedValue(ACTIVE_JOB as never)
  vi.mocked(toast.error).mockClear()
  vi.mocked(toast.success).mockClear()
})

describe('RunsPage — bulk deep goes through the server', () => {
  it('Bulk deep on a selection: ONE bulkTriggerPipelines(ids, "deep"), no per-run fan-out; the panel opens', async () => {
    vi.mocked(agentService.bulkTriggerPipelines).mockResolvedValue(accepted() as never)
    renderRunsPage()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select #104' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select #103' }))
    fireEvent.click(screen.getByRole('button', { name: 'Bulk deep' }))
    await waitFor(() => expect(agentService.bulkTriggerPipelines).toHaveBeenCalledTimes(1))
    expect(agentService.bulkTriggerPipelines).toHaveBeenCalledWith(['run-4', 'run-3'], 'deep')
    expect(agentService.triggerDeepPipeline).not.toHaveBeenCalled()
    const panel = await screen.findByRole('region', { name: 'Bulk AI analysis' })
    expect(await within(panel).findByText('2 requested · 2 queued · 0 already running · 0 not found')).toBeInTheDocument()
    expect(location).toBe(`?bulk_job=${JOB_ID}`)
    // The panel replaces the bulk success toast.
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('Deep all failed: ONE request for the window\'s failed runs', async () => {
    vi.mocked(agentService.bulkTriggerPipelines).mockResolvedValue(accepted({ requested: 3, queued: 3 }) as never)
    renderRunsPage()
    openMenu()
    fireEvent.click(screen.getByRole('menuitem', { name: 'Deep all failed (3)' }))
    await waitFor(() => expect(agentService.bulkTriggerPipelines).toHaveBeenCalledWith(['run-4', 'run-3', 'run-2'], 'deep'))
    expect(agentService.bulkTriggerPipelines).toHaveBeenCalledTimes(1)
    expect(agentService.triggerDeepPipeline).not.toHaveBeenCalled()
  })

  it('a refused batch is the server\'s sentence in the panel\'s place — no toast, nothing else asked', async () => {
    const sentence = 'Deep investigation requires LLM or Auto mode. Current mode: rules.'
    vi.mocked(agentService.bulkTriggerPipelines).mockRejectedValue(
      Object.assign(new Error('422'), { response: { status: 422, data: { detail: sentence } } }),
    )
    renderRunsPage()
    openMenu()
    fireEvent.click(screen.getByRole('menuitem', { name: 'Deep all failed (3)' }))
    const note = await screen.findByText(sentence)
    expect(note).toHaveAttribute('role', 'note')
    expect(toast.error).not.toHaveBeenCalled()
    expect(screen.queryByRole('region', { name: 'Bulk AI analysis' })).toBeNull()
    expect(agentService.triggerDeepPipeline).not.toHaveBeenCalled()
  })

  it('an untracked batch (the job record could not be written) says so instead of a panel', async () => {
    vi.mocked(agentService.bulkTriggerPipelines).mockResolvedValue(accepted({ queued: 3, bulk_job_id: null, tracking: false }) as never)
    renderRunsPage()
    openMenu()
    fireEvent.click(screen.getByRole('menuitem', { name: 'Trigger all failed (3)' }))
    expect(await screen.findByText('Queued 3 pipelines. Progress cannot be tracked right now.')).toHaveAttribute('role', 'note')
    expect(screen.queryByRole('region', { name: 'Bulk AI analysis' })).toBeNull()
    expect(agentService.getBulkJob).not.toHaveBeenCalled()
  })
})

describe('RunsPage — Re-trigger all matching', () => {
  it('asks first, naming the list\'s COUNT; confirm sends the list\'s own filter with a client token', async () => {
    vi.mocked(agentService.bulkTriggerFiltered).mockResolvedValue(
      accepted({ source: 'filter', requested: TOTAL, queued: 35, skipped_in_progress: 2, matched: TOTAL }) as never,
    )
    renderRunsPage('/runs?q=build&verdict=NO_GO')
    openMenu()
    fireEvent.click(screen.getByRole('menuitem', { name: `Re-trigger all matching (${TOTAL})` }))
    expect(agentService.bulkTriggerFiltered).not.toHaveBeenCalled()
    const confirm = screen.getByTestId('runs-retrigger-confirm')
    expect(confirm).toHaveTextContent(`Re-trigger the AI pipeline for ${TOTAL} runs matching this filter?`)
    fireEvent.click(within(confirm).getByRole('button', { name: 'Re-trigger' }))
    await waitFor(() => expect(agentService.bulkTriggerFiltered).toHaveBeenCalledTimes(1))
    const [filter, workflowType, token] = vi.mocked(agentService.bulkTriggerFiltered).mock.calls[0]
    // Exactly the list's request: what useRuns received, plus the active project.
    expect(filter).toEqual(filterFromPage({ projectId: 'proj-1', release: null, params: vi.mocked(useRuns).mock.lastCall?.[0] as Record<string, unknown> }))
    expect(filter).toEqual({ project_id: 'proj-1', days: 30, q: 'build', recommendation: 'NO_GO' })
    expect(workflowType).toBe('offline')
    expect(token).toMatch(/^[0-9a-f]{32}$/)
    expect(await screen.findByRole('region', { name: 'Bulk AI analysis' })).toBeInTheDocument()
    expect(screen.queryByTestId('runs-retrigger-confirm')).toBeNull()
    expect(agentService.bulkTriggerPipelines).not.toHaveBeenCalled()
  })

  it('Cancel on the question sends nothing; the item is disabled without the QA Engineer role', () => {
    renderRunsPage()
    openMenu()
    fireEvent.click(screen.getByRole('menuitem', { name: `Re-trigger all matching (${TOTAL})` }))
    fireEvent.click(within(screen.getByTestId('runs-retrigger-confirm')).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByTestId('runs-retrigger-confirm')).toBeNull()
    expect(agentService.bulkTriggerFiltered).not.toHaveBeenCalled()
  })

  it('without the QA Engineer role the item is disabled', () => {
    perms.isQaEngineer = false
    renderRunsPage()
    openMenu()
    expect(screen.getByRole('menuitem', { name: `Re-trigger all matching (${TOTAL})` })).toBeDisabled()
  })
})

describe('RunsPage — ?bulk_job=', () => {
  it('reopens the tracked job on load', async () => {
    renderRunsPage(`/runs?bulk_job=${JOB_ID}`)
    expect(await screen.findByRole('region', { name: 'Bulk AI analysis' })).toBeInTheDocument()
    expect(agentService.getBulkJob).toHaveBeenCalledWith(JOB_ID)
    fireEvent.click(await screen.findByRole('button', { name: 'Close' }))
    await waitFor(() => expect(location).toBe(''))
    expect(screen.queryByRole('region', { name: 'Bulk AI analysis' })).toBeNull()
  })

  it('a value that is not an id is never put in a path', () => {
    renderRunsPage('/runs?bulk_job=..%2F..%2Fadmin')
    expect(screen.queryByRole('region', { name: 'Bulk AI analysis' })).toBeNull()
    expect(agentService.getBulkJob).not.toHaveBeenCalled()
  })

  it('no ?bulk_job=: no panel and no bulk-job read on load', () => {
    renderRunsPage()
    expect(screen.queryByRole('region', { name: 'Bulk AI analysis' })).toBeNull()
    expect(agentService.getBulkJob).not.toHaveBeenCalled()
  })
})
