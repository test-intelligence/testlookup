import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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

  const projects = [{ id: 'p1', name: 'Core UI' }]
  const refreshProjects = vi.fn(async () => projects)

  const projectStoreState = {
    activeProject: null as null | { id: string; name: string },
    activeProjectId: null as null | string,
    projects,
    refreshProjects,
    setActiveProject,
    setAllProjects,
  }

  const authState = {
    user: {
      full_name: 'Test User',
      username: 'tester',
      role: 'ADMIN',
      email: 'test@example.com',
      avatar_color: null,
    },
  }

  const useProjectStore = vi.fn((selector?: (s: typeof projectStoreState) => unknown) =>
    selector ? selector(projectStoreState) : projectStoreState,
  )
  ;(useProjectStore as unknown as { getState: () => typeof projectStoreState }).getState = () =>
    projectStoreState
  const useAuthStore = vi.fn((selector?: (s: typeof authState) => unknown) =>
    selector ? selector(authState) : authState,
  )
  ;(useAuthStore as unknown as { getState: () => { logout: () => void } }).getState = () => ({
    logout,
  })

  const DEFAULT_HISTORY = [
    { id: 'n1', title: 'Run failed', status: 'failed', channel: 'email', is_read: false, created_at: new Date().toISOString() },
  ] as Array<Record<string, unknown>>
  const history = { logs: DEFAULT_HISTORY }

  // W12 SRCH-03: the typeahead's read, answered per test.
  const typeahead = {
    answer: undefined as undefined | { items: Array<Record<string, unknown>> },
    calls: [] as Array<{ query: string; projectId: string | null; enabled: boolean }>,
  }
  const useSearchTypeahead = vi.fn((query: string, projectId: string | null, enabled: boolean) => {
    typeahead.calls.push({ query, projectId, enabled })
    const debounced = enabled && query.trim().length >= 2 ? query.trim() : null
    return {
      debounced,
      settling: false,
      data: debounced ? typeahead.answer : undefined,
      error: undefined,
      isLoading: false,
    }
  })

  return {
    typeahead,
    useSearchTypeahead,
    navigate,
    setActiveProject,
    setAllProjects,
    logout,
    markAllRead,
    refreshLogs,
    useProjectStore,
    useAuthStore,
    projectStoreState,
    authState,
    history,
    DEFAULT_HISTORY,
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
    data: mocked.history.logs,
    mutate: mocked.refreshLogs,
  }),
  invalidateNotifications: vi.fn(),
}))

vi.mock('@/services/notificationService', () => ({
  notificationService: {
    markAllRead: mocked.markAllRead,
  },
}))

vi.mock('@/hooks/useSearchTypeahead', () => ({
  useSearchTypeahead: mocked.useSearchTypeahead,
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
    mocked.history.logs = mocked.DEFAULT_HISTORY
    mocked.authState.user.role = 'ADMIN'
    ;(mocked.authState.user as { avatar_color: string | null }).avatar_color = null
    mocked.typeahead.answer = undefined
    mocked.typeahead.calls = []
  })

  // W29a UIX-01 (L3): the initials are the shared avatar map's ink on its
  // tint. White initials were ~1.3:1 on a light theme's 15 % tint (blank).
  describe('the avatar initials (W29a UIX-01)', () => {
    function initials(): HTMLElement {
      render(
        <MemoryRouter>
          <TopBar />
        </MemoryRouter>,
      )
      return screen.getByText('TU')
    }

    it('no stored colour: the blue family, ink on its tint, never white', () => {
      const el = initials()
      expect(el.className).not.toMatch(/text-white/)
      expect(el.style.background).toBe('var(--color-accent-muted)')
      expect(el.style.color).toBe('var(--ui-info-ink)')
    })

    it.each([
      ['red', 'var(--status-failed-bg)', 'var(--status-failed-ink)'],
      ['amber', 'var(--status-broken-bg)', 'var(--status-broken-ink)'],
      ['teal', 'var(--status-passed-bg)', 'var(--status-passed-ink)'],
      ['violet', 'var(--status-flaky-bg)', 'var(--status-flaky-ink)'],
      ['slate', 'var(--color-bg-hover)', 'var(--color-text)'],
    ])('a stored %s avatar: %s behind %s initials', (key, bg, fg) => {
      ;(mocked.authState.user as { avatar_color: string | null }).avatar_color = key
      const el = initials()
      expect(el.className).not.toMatch(/text-white/)
      expect(el.style.background).toBe(bg)
      expect(el.style.color).toBe(fg)
    })
  })

  // W29a UIX-07: Sign out is a UI danger, not a test result.
  it('Sign out is painted with the danger UI token', () => {
    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Account menu' }))
    const signOut = screen.getByRole('menuitem', { name: /Sign out/ })
    expect(signOut.className).toContain('text-[var(--ui-danger)]')
    expect(signOut.className).not.toContain('--status-failed')
  })

  // W29a UIX-13: the unread count on its solid red bubble is the card colour,
  // not white (white was 2.5–4.1:1 on the dark themes' solid failed fill).
  it('the unread count reads --color-text-on-status on its bubble', () => {
    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>,
    )
    const bubble = screen.getByRole('button', { name: 'Notifications (3 unread)' }).querySelector('span')
    expect(bubble).not.toBeNull()
    expect(bubble?.textContent).toBe('3')
    expect(bubble?.className).toContain('text-[var(--color-text-on-status)]')
    expect(bubble?.className).not.toMatch(/text-white/)
  })

  it('navigates to search page on Enter', () => {
    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>,
    )

    const input = screen.getByPlaceholderText('Search tests, runs, defects… (Enter)')
    fireEvent.change(input, { target: { value: 'timeout issue' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(mocked.navigate).toHaveBeenCalledWith('/search?q=timeout%20issue')
  })

  it('focuses the global search box when "/" is pressed', () => {
    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>,
    )

    const input = screen.getByPlaceholderText('Search tests, runs, defects… (Enter)')
    expect(input).not.toHaveFocus()

    fireEvent.keyDown(document.body, { key: '/' })
    expect(input).toHaveFocus()
  })

  it('does not hijack "/" typed into another field', () => {
    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>,
    )

    const input = screen.getByPlaceholderText('Search tests, runs, defects… (Enter)')
    const select = screen.getByRole('combobox', { name: /select project/i })

    // A "/" originating in the project select must not yank focus to search.
    fireEvent.keyDown(select, { key: '/' })
    expect(input).not.toHaveFocus()
  })

  it('ignores "/" pressed with a modifier key', () => {
    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>,
    )

    const input = screen.getByPlaceholderText('Search tests, runs, defects… (Enter)')
    fireEvent.keyDown(document.body, { key: '/', ctrlKey: true })
    expect(input).not.toHaveFocus()
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

    const select = screen.getByRole('combobox', { name: /select project/i })
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

    const select = screen.getByRole('combobox', { name: /select project/i })
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

    fireEvent.click(screen.getByRole('button', { name: /Notifications/ }))
    expect(screen.getByText('Notifications')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Mark all read' }))
    await waitFor(() => {
      expect(mocked.markAllRead).toHaveBeenCalledTimes(1)
      expect(mocked.refreshLogs).toHaveBeenCalledTimes(1)
    })
  })

  // Audit 1.4: the profile menu used to open on CSS hover only — no click
  // handler, no aria-expanded, no keyboard path — so Sign out was unreachable
  // by keyboard/touch. It is now a real disclosure button + menu.
  it('opens the profile menu on click and signs out', () => {
    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>,
    )

    // Closed by default: no menu, trigger reports collapsed.
    const trigger = screen.getByRole('button', { name: 'Account menu' })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()

    fireEvent.click(trigger)
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText(/test@example\.com/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('menuitem', { name: /Sign out/ }))
    expect(mocked.logout).toHaveBeenCalledTimes(1)
  })

  // UX redesign P1: the account menu holds everything personal.
  it('the account menu has My profile, My notifications, the theme and presentation mode', () => {
    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Account menu' }))
    // The role as people say it, not its code.
    expect(screen.getByText(/^Admin · test@example\.com$/)).toBeInTheDocument()
    expect(screen.queryByText(/ADMIN/)).not.toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /My profile/ })).toHaveAttribute('href', '/settings/profile')
    expect(screen.getByRole('menuitem', { name: /My notifications/ })).toHaveAttribute('href', '/settings/my-notifications')
    expect(screen.getByRole('listbox', { name: 'Color theme' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Presentation mode/ })).toHaveAttribute('aria-pressed')
    // The theme is no longer a top-bar control of its own.
    expect(screen.queryByTitle('Change color theme')).not.toBeInTheDocument()
  })

  it('the bell links to My notifications, which every role can open', async () => {
    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>,
    )
    fireEvent.click(screen.getByRole('button', { name: /^Notifications/ }))
    const links = screen.getAllByRole('link').filter((a) => /Settings|Manage notification/.test(a.textContent ?? ''))
    expect(links.length).toBe(2)
    for (const a of links) expect(a).toHaveAttribute('href', '/settings/my-notifications')
  })

  // NTF-01: a bell entry links only to the server's two allow-listed shapes,
  // re-checked by role (/releases is a management route).
  describe('bell entry links', () => {
    const RUN = '0f8fad5b-d9cb-469f-a165-70867728950e'
    const REL = '7c9e6679-7425-40de-944b-e07fc1f90ae7'
    const entry = (id: string, title: string, link?: unknown) => ({
      id, title, status: 'pending', channel: 'email', is_read: false, created_at: new Date().toISOString(),
      ...(link === undefined ? {} : { link }),
    })

    function openBell() {
      render(
        <MemoryRouter>
          <TopBar />
        </MemoryRouter>,
      )
      fireEvent.click(screen.getByRole('button', { name: /^Notifications/ }))
    }

    it('a run decision entry is a link with exactly that href, and a click closes the popover', () => {
      mocked.history.logs = [entry('g1', 'Quality gate failed: Checkout build 142 — test_pay is FAILED', `/release-gate/${RUN}`)]
      mocked.authState.user.role = 'VIEWER'
      openBell()
      const link = screen.getByRole('link', { name: /Quality gate failed: Checkout build 142/ })
      expect(link.getAttribute('href')).toBe(`/release-gate/${RUN}`)
      fireEvent.click(link)
      expect(screen.queryByRole('link', { name: /Quality gate failed: Checkout build 142/ })).toBeNull()
      expect(screen.queryByRole('button', { name: 'Mark all read' })).toBeNull()
    })

    it('a release entry is a link for a QA lead and text for a viewer', () => {
      mocked.history.logs = [entry('g2', 'Quality gate failed: 2026.10 — test_pay (checkout-e2e) is FAILED', `/releases/${REL}`)]
      mocked.authState.user.role = 'QA_LEAD'
      openBell()
      expect(screen.getByRole('link', { name: /Quality gate failed: 2026\.10/ }).getAttribute('href')).toBe(`/releases/${REL}`)
    })

    it('...and for a viewer the same entry is text', () => {
      mocked.history.logs = [entry('g2', 'Quality gate failed: 2026.10 — test_pay (checkout-e2e) is FAILED', `/releases/${REL}`)]
      mocked.authState.user.role = 'VIEWER'
      openBell()
      expect(screen.getByText(/Quality gate failed: 2026\.10/)).toBeInTheDocument()
      expect(screen.queryByRole('link', { name: /Quality gate failed/ })).toBeNull()
    })

    it.each([
      ['no link', undefined],
      ['null', null],
      ['an absolute URL', `https://example.com/releases/${REL}`],
      ['protocol-relative', '//example.com'],
      ['another route', '/settings'],
      ['not a uuid', '/releases/x'],
    ])('an entry with %s is text', (_name, link) => {
      mocked.history.logs = [entry('t1', 'Something happened', link)]
      openBell()
      expect(screen.getByText('Something happened')).toBeInTheDocument()
      expect(screen.queryByRole('link', { name: 'Something happened' })).toBeNull()
    })

    it('a hostile title is React text inside the link', () => {
      mocked.history.logs = [entry('h1', '<img src=x onerror=alert(1)>', `/release-gate/${RUN}`)]
      openBell()
      const link = screen.getByRole('link', { name: '<img src=x onerror=alert(1)>' })
      expect(link.querySelector('img')).toBeNull()
    })
  })

  // W12 SRCH-03: the typeahead is a listbox anchored under the input (never a
  // dialog or a portal), and while closed the input is exactly as before.
  describe('the search typeahead (W12)', () => {
    const MATCH = {
      entity_type: 'test_run', entity_id: 'run-7', title: 'build-1042', subtitle: 'main · FAILED',
      project_id: 'p1', project_name: 'Core UI', navigation_url: '/runs/run-7', relevance_score: 0.8,
      match_reasons: [], metadata: {},
    }

    function input(): HTMLInputElement {
      return screen.getByPlaceholderText('Search tests, runs, defects… (Enter)') as HTMLInputElement
    }

    it('closed: no list, no request, and the input carries no new attribute', () => {
      render(
        <MemoryRouter>
          <TopBar />
        </MemoryRouter>,
      )
      expect(screen.queryByRole('listbox', { name: 'Matches' })).toBeNull()
      expect(input().getAttributeNames().sort()).toEqual(['class', 'placeholder', 'type', 'value'])
      expect(mocked.typeahead.calls.every((call) => call.enabled === false)).toBe(true)
    })

    it('typing opens the matches under the input; a click selects the project and opens the match; Escape closes', () => {
      mocked.typeahead.answer = { items: [MATCH] }
      render(
        <MemoryRouter>
          <TopBar />
        </MemoryRouter>,
      )
      fireEvent.change(input(), { target: { value: 'build' } })
      // No project pinned here: the read is the caller's whole scope.
      expect(mocked.typeahead.calls[mocked.typeahead.calls.length - 1]).toEqual({ query: 'build', projectId: null, enabled: true })

      const list = screen.getByRole('listbox', { name: 'Matches' })
      // Anchored inside the input's own wrapper: not a dialog, not portaled.
      expect(input().parentElement?.contains(list)).toBe(true)
      expect(list.closest('[role="dialog"]')).toBeNull()
      const option = within(list).getByRole('option', { name: /build-1042/ })
      expect(option).toHaveTextContent('main · FAILED')
      expect(option).toHaveTextContent('Runs')
      expect(within(list).getByRole('option', { name: /Search everything for “build”/ })).toBeInTheDocument()

      fireEvent.click(option)
      expect(mocked.setActiveProject).toHaveBeenCalledWith({ id: 'p1', name: 'Core UI' })
      expect(mocked.navigate).toHaveBeenCalledWith('/runs/run-7')
      expect(input().value).toBe('')
      expect(screen.queryByRole('listbox', { name: 'Matches' })).toBeNull()

      fireEvent.change(input(), { target: { value: 'timeout' } })
      expect(screen.getByRole('listbox', { name: 'Matches' })).toBeInTheDocument()
      fireEvent.keyDown(input(), { key: 'Escape' })
      expect(screen.queryByRole('listbox', { name: 'Matches' })).toBeNull()
      expect(input().value).toBe('timeout')
      expect(mocked.typeahead.calls[mocked.typeahead.calls.length - 1]?.enabled).toBe(false)

      // Enter still runs the full search, and closes the list.
      fireEvent.change(input(), { target: { value: 'timeout issue' } })
      fireEvent.keyDown(input(), { key: 'Enter' })
      expect(mocked.navigate).toHaveBeenLastCalledWith('/search?q=timeout%20issue')
      expect(screen.queryByRole('listbox', { name: 'Matches' })).toBeNull()
    })
  })

  it('closes the profile menu on Escape', () => {
    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>,
    )

    const trigger = screen.getByRole('button', { name: 'Account menu' })
    fireEvent.click(trigger)
    expect(screen.getByRole('menu')).toBeInTheDocument()

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
  })
})
