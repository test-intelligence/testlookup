/**
 * D-37: the sidebar badge (77) and the Inbox's "Assigned to me" (38) disagreed
 * with nothing on screen to say why. The badge counts your failures in every
 * project; the Inbox lists one. With a project pinned, the Inbox now says how
 * many more are in other projects, and that line switches to All Projects.
 * D-38: the repeat chip's title reads "Failed N times in the last {days}".
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import MyFailuresPage from './MyFailuresPage'
import type { MyFailureListResponse } from '@/types/myFailures'
import { useTimeWindowStore } from '@/store/timeWindowStore'

const state = vi.hoisted(() => ({ projectId: 'p1' as string, isQaLead: false }))
const setActiveProject = vi.hoisted(() => vi.fn())
const mockList = vi.fn()
const mockCount = vi.fn()

vi.mock('@/services/myFailuresService', () => ({
  myFailuresService: {
    list: (...args: unknown[]) => mockList(...args),
    count: (...args: unknown[]) => mockCount(...args),
  },
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (s: Record<string, unknown>) => unknown) =>
    selector({
      activeProjectId: state.projectId,
      activeProject: state.projectId === '__ALL__' ? null : { id: 'p1', name: 'Checkout' },
      setActiveProject,
    })),
}))

vi.mock('@/store/authStore', () => ({
  useAuthStore: vi.fn((selector: (s: { user: { id: string; username: string } }) => unknown) =>
    selector({ user: { id: 'u1', username: 'engineer' } })),
}))

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ isQaLead: state.isQaLead }),
}))

vi.mock('react-hot-toast', () => ({ default: { error: vi.fn(), success: vi.fn() } }))

const ITEM = {
  id: 'cc1', test_name: 'test_checkout_pays', suite_name: 'Checkout', status: 'FAILED',
  severity: 'major', error_message: null, created_at: new Date(Date.now() - 3_600_000).toISOString(),
  test_run_id: 'run-1', build_number: '42', run_seq: 7, project_id: 'p1', project_name: 'Checkout',
  navigation_url: '/runs/run-1/tests/cc1', class_name: null, failure_category: null, duration_ms: null,
  failure_count: 9,
} as MyFailureListResponse['items'][number]

function listOf(unresolved: number): MyFailureListResponse {
  return { items: [ITEM], total: unresolved, page: 1, size: 25, pages: 2, unresolved_total: unresolved }
}

function renderPage() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter initialEntries={['/my-failures']}>
        <MyFailuresPage />
      </MemoryRouter>
    </SWRConfig>,
  )
}

describe('Inbox: the badge and the list reconcile (D-37)', () => {
  beforeEach(() => {
    mockList.mockReset()
    mockCount.mockReset()
    setActiveProject.mockReset()
    state.projectId = 'p1'
    state.isQaLead = false
    useTimeWindowStore.setState({ days: 14 })
    mockList.mockResolvedValue(listOf(38))
    mockCount.mockResolvedValue({ count: 77 })
  })

  it('a pinned project says how many more are in other projects, and the line opens All Projects', async () => {
    renderPage()
    const line = await screen.findByTestId('inbox-elsewhere')
    expect(line).toHaveTextContent('39 more in other projects')
    // Asked over the page's window, across every project, so the difference is the other projects only.
    expect(mockCount).toHaveBeenCalledWith({ days: 14 })
    fireEvent.click(line)
    expect(setActiveProject).toHaveBeenCalledWith(null)
  })

  it('the subtitle names the window', async () => {
    renderPage()
    expect(await screen.findByText(/assigned to you across Checkout in the last 14 days/)).toBeInTheDocument()
  })

  it('no line when the counts agree', async () => {
    mockCount.mockResolvedValue({ count: 38 })
    renderPage()
    await waitFor(() => expect(mockCount).toHaveBeenCalled())
    await screen.findByText('test_checkout_pays')
    expect(screen.queryByTestId('inbox-elsewhere')).toBeNull()
  })

  it('no line in All Projects: the list already spans them', async () => {
    state.projectId = '__ALL__'
    renderPage()
    await screen.findByText('test_checkout_pays')
    expect(screen.queryByTestId('inbox-elsewhere')).toBeNull()
  })

  it('the repeat chip says what it counts (D-38)', async () => {
    renderPage()
    expect(await screen.findByTitle('Failed 9 times in the last 14 days')).toHaveTextContent('9')
  })
})
