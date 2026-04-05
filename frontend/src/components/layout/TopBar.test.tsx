import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import TopBar from './TopBar'

const mocked = vi.hoisted(() => {
  const navigate = vi.fn()
  const setActiveProject = vi.fn()
  const setAllProjects = vi.fn()
  const logout = vi.fn()
  const markAllRead = vi.fn(async () => {})
  const refreshLogs = vi.fn()

  const projectStoreState = {
    activeProject: null as null | { id: string; name: string },
    activeProjectId: null as null | string,
    setActiveProject,
    setAllProjects,
  }

  const authState = {
    user: {
      full_name: 'Test User',
      username: 'tester',
      role: 'admin',
      email: 'test@example.com',
    },
  }

  const useProjectStore = vi.fn((selector?: (s: typeof projectStoreState) => unknown) =>
    selector ? selector(projectStoreState) : projectStoreState,
  )
  const useAuthStore = vi.fn((selector?: (s: typeof authState) => unknown) =>
    selector ? selector(authState) : authState,
  )
  ;(useAuthStore as unknown as { getState: () => { logout: () => void } }).getState = () => ({
    logout,
  })

  return {
    navigate,
    setActiveProject,
    setAllProjects,
    logout,
    markAllRead,
    refreshLogs,
    useProjectStore,
    useAuthStore,
    projectStoreState,
  }
})

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return {
    ...(actual as Record<string, unknown>),
    useNavigate: () => mocked.navigate,
  }
})

vi.mock('@/store/projectStore', () => ({
  useProjectStore: mocked.useProjectStore,
  ALL_PROJECTS_ID: 'all',
}))

vi.mock('@/store/authStore', () => ({
  useAuthStore: mocked.useAuthStore,
}))

vi.mock('@/services/projectsService', () => ({
  projectsService: {
    list: vi.fn(async () => [{ id: 'p1', name: 'Core UI' }]),
  },
}))

vi.mock('@/hooks/useNotifications', () => ({
  useUnreadCount: () => ({ data: { unread: 3 } }),
  useNotificationHistory: () => ({
    data: [{ id: 'n1', title: 'Run failed', status: 'failed', channel: 'email', is_read: false, created_at: new Date().toISOString() }],
    mutate: mocked.refreshLogs,
  }),
  invalidateNotifications: vi.fn(),
}))

vi.mock('@/services/notificationService', () => ({
  notificationService: {
    markAllRead: mocked.markAllRead,
  },
}))

describe('TopBar', () => {
  beforeEach(() => {
    mocked.navigate.mockClear()
    mocked.setActiveProject.mockClear()
    mocked.setAllProjects.mockClear()
    mocked.logout.mockClear()
    mocked.markAllRead.mockClear()
    mocked.refreshLogs.mockClear()
    mocked.projectStoreState.activeProjectId = null
    mocked.projectStoreState.activeProject = null
  })

  it('navigates to search page on Enter', () => {
    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>,
    )

    const input = screen.getByPlaceholderText('Search tests, errors… (Enter)')
    fireEvent.change(input, { target: { value: 'timeout issue' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(mocked.navigate).toHaveBeenCalledWith('/search?q=timeout%20issue')
  })

  it('loads and selects projects from selector', async () => {
    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByRole('option', { name: 'Core UI' })).toBeInTheDocument()
    })

    const select = screen.getByRole('combobox')
    fireEvent.change(select, { target: { value: 'p1' } })
    expect(mocked.setActiveProject).toHaveBeenCalledWith({ id: 'p1', name: 'Core UI' })
  })

  it('shows "All Projects" option in project selector', async () => {
    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByRole('option', { name: 'All Projects' })).toBeInTheDocument()
    })
  })

  it('calls setAllProjects when "All Projects" is selected', async () => {
    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByRole('option', { name: 'All Projects' })).toBeInTheDocument()
    })

    const select = screen.getByRole('combobox')
    fireEvent.change(select, { target: { value: 'all' } })
    expect(mocked.setAllProjects).toHaveBeenCalledTimes(1)
    expect(mocked.setActiveProject).not.toHaveBeenCalled()
  })

  it('opens notification panel and marks all as read', async () => {
    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Notifications' }))
    expect(screen.getByText('Notifications')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Mark all read' }))
    await waitFor(() => {
      expect(mocked.markAllRead).toHaveBeenCalledTimes(1)
      expect(mocked.refreshLogs).toHaveBeenCalledTimes(1)
    })
  })
})
