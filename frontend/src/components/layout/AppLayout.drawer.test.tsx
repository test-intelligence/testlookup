/**
 * VIZ-106 (Wave 2.6, OD-9): below 1024 px the sidebar is a drawer, opened
 * from a labelled menu button in the top bar. While open it is a modal
 * dialog: focus moves in, Tab stays inside, Escape closes it and focus goes
 * back to the menu button; it closes on every navigation and when the window
 * widens to the desktop shell. The desktop shell itself is held by
 * `AppLayout.desktop.test.tsx`.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AppLayout, { NARROW_SHELL_QUERY, SHELL_NAV_ID } from './AppLayout'

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ isAdmin: true, canAccessManagement: true }),
}))
vi.mock('@/hooks/useFeatureFlags', () => ({
  useFeatureEnabled: () => false,
  useFeatureFlags: () => ({ flags: [], isLoading: false, isError: false, refresh: vi.fn() }),
}))
vi.mock('@/hooks/useAIConfig', () => ({ useAIConfig: () => ({ data: undefined }) }))
vi.mock('@/hooks/useMyFailuresCountUnscoped', () => ({
  useMyFailuresCountUnscoped: () => ({ data: { count: 0 } }),
}))
vi.mock('@/hooks/useNotifications', () => ({
  useUnreadCount: () => ({ data: { unread: 0 } }),
  useNotificationHistory: () => ({ data: [], mutate: vi.fn() }),
  invalidateNotifications: vi.fn(),
}))

const stores = vi.hoisted(() => {
  const project = {
    activeProject: { id: 'p1', name: 'Core UI' },
    activeProjectId: 'p1',
    projects: [{ id: 'p1', name: 'Core UI' }],
    refreshProjects: () => Promise.resolve([{ id: 'p1', name: 'Core UI' }]),
    setActiveProject: () => {},
    setAllProjects: () => {},
  }
  const auth = {
    user: { full_name: 'Test User', username: 'tester', role: 'ADMIN', email: 't@example.com', avatar_color: 'blue' },
  }
  const select = <S,>(state: S) => {
    const hook = (selector?: (s: S) => unknown) => (selector ? selector(state) : state)
    return Object.assign(hook, { getState: () => state })
  }
  return { useProjectStore: select(project), useAuthStore: select(auth) }
})
vi.mock('@/store/projectStore', () => ({ useProjectStore: stores.useProjectStore, ALL_PROJECTS_ID: 'all' }))
vi.mock('@/store/authStore', () => ({ useAuthStore: stores.useAuthStore }))
vi.mock('./ReleasePicker', () => ({ ReleasePicker: () => null }))
vi.mock('./AppVersionBadge', () => ({ default: () => null }))
vi.mock('./DegradedBanner', () => ({ default: () => null }))

/** A controllable `matchMedia`: `setNarrow` fires the change the way a resize does. */
const viewport = { narrow: true, listeners: new Set<() => void>() }
function installViewport() {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      get matches() {
        return query === NARROW_SHELL_QUERY ? viewport.narrow : false
      },
      media: query,
      onchange: null,
      addEventListener: (_: string, cb: () => void) => viewport.listeners.add(cb),
      removeEventListener: (_: string, cb: () => void) => viewport.listeners.delete(cb),
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  })
}
function setNarrow(narrow: boolean) {
  viewport.narrow = narrow
  act(() => viewport.listeners.forEach((cb) => cb()))
}

function Page({ name }: { name: string }) {
  const navigate = useNavigate()
  return (
    <div>
      <p>{name} page</p>
      {/* Navigation that does not come from the drawer (a redirect, a link in the page). */}
      <button type="button" onClick={() => navigate('/trends')}>
        Go to trends from the page
      </button>
    </div>
  )
}

function renderShell(path = '/overview') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="/overview" element={<Page name="Overview" />} />
          <Route path="/trends" element={<Page name="Trends" />} />
          <Route path="/runs" element={<Page name="Runs" />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

const menuButton = () => screen.getByRole('button', { name: 'Navigation menu' })
const drawer = () => screen.queryByRole('dialog', { name: 'Navigation' })
const openDrawer = () => {
  fireEvent.click(menuButton())
  const dialog = drawer()
  if (!dialog) throw new Error('the drawer did not open')
  return dialog
}
/** Tab / Shift+Tab as the browser delivers them: to document, from the focused element. */
const tab = (shift = false) => fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Tab', shiftKey: shift })

beforeEach(() => {
  viewport.narrow = true
  viewport.listeners.clear()
  installViewport()
})
afterEach(() => {
  delete (window as { matchMedia?: unknown }).matchMedia
})

describe('AppLayout below 1024 px: the navigation drawer', () => {
  it('starts closed: the sidebar is hidden, and a labelled menu button in the top bar controls it', () => {
    const { container } = renderShell()
    const aside = container.querySelector('aside')
    expect(aside).toHaveClass('max-lg:hidden')
    expect(aside).toHaveAttribute('id', SHELL_NAV_ID)
    expect(aside).not.toHaveAttribute('role')
    expect(drawer()).toBeNull()

    const menu = menuButton()
    expect(within(container.querySelector('header') as HTMLElement).getByRole('button', { name: 'Navigation menu' })).toBe(menu)
    expect(menu).toHaveAttribute('aria-expanded', 'false')
    expect(menu).toHaveAttribute('aria-controls', SHELL_NAV_ID)
    expect(container.querySelector('[data-shell-backdrop]')).toBeNull()
  })

  it('opens as a modal dialog over the page, and moves focus into it', () => {
    const { container } = renderShell()
    const dialog = openDrawer()
    expect(dialog.tagName).toBe('ASIDE')
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toHaveClass('max-lg:fixed')
    expect(dialog).not.toHaveClass('max-lg:hidden')
    expect(menuButton()).toHaveAttribute('aria-expanded', 'true')
    expect(container.querySelector('[data-shell-backdrop]')).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Close navigation' })).toHaveFocus()
    expect(within(dialog).getByRole('navigation', { name: 'Main navigation' })).toBeInTheDocument()
  })

  it('traps focus: Tab from the last control wraps to the first, Shift+Tab from the first to the last, and focus behind the drawer is pulled back in', () => {
    renderShell()
    const dialog = openDrawer()
    const inside = within(dialog)
    const first = inside.getByRole('button', { name: 'Close navigation' })
    // The last control in the drawer: the presentation toggle in its footer.
    const last = inside.getByRole('button', { name: 'Presentation mode' })

    last.focus()
    tab()
    expect(first).toHaveFocus()

    first.focus()
    tab(true)
    expect(last).toHaveFocus()

    // Focus that escaped to the page behind (a click, a script) comes back in on the next Tab.
    screen.getByRole('button', { name: 'Go to trends from the page' }).focus()
    tab()
    expect(dialog).toContainElement(document.activeElement as HTMLElement)
  })

  it('Escape closes it and returns focus to the menu button', () => {
    renderShell()
    openDrawer()
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    expect(drawer()).toBeNull()
    expect(menuButton()).toHaveFocus()
    expect(menuButton()).toHaveAttribute('aria-expanded', 'false')
  })

  it('the close button and the backdrop close it too', () => {
    const { container } = renderShell()
    openDrawer()
    fireEvent.click(screen.getByRole('button', { name: 'Close navigation' }))
    expect(drawer()).toBeNull()
    expect(menuButton()).toHaveFocus()

    openDrawer()
    fireEvent.click(container.querySelector('[data-shell-backdrop]') as HTMLElement)
    expect(drawer()).toBeNull()
  })

  it('closes when a link in it is followed', () => {
    renderShell('/overview')
    const dialog = openDrawer()
    fireEvent.click(within(dialog).getByRole('link', { name: 'My Failures' }))
    expect(drawer()).toBeNull()
  })

  it('closes on a navigation that does not come from the drawer', () => {
    renderShell('/overview')
    openDrawer()
    fireEvent.click(screen.getByRole('button', { name: 'Go to trends from the page' }))
    expect(screen.getByText('Trends page')).toBeInTheDocument()
    expect(drawer()).toBeNull()
  })

  it('closes when the window widens to the desktop shell, where the menu button is gone', () => {
    const { container } = renderShell()
    openDrawer()
    setNarrow(false)
    expect(drawer()).toBeNull()
    expect(screen.queryByRole('button', { name: 'Navigation menu' })).toBeNull()
    expect(container.querySelector('aside')).not.toHaveAttribute('id')
    // Narrow again: closed, not re-opened by stale state.
    setNarrow(true)
    expect(drawer()).toBeNull()
  })

  it('carries the presentation toggle in the drawer footer, operable from inside the dialog', () => {
    const { container } = renderShell()
    const header = container.querySelector('header') as HTMLElement
    expect(within(header).queryByRole('button', { name: 'Presentation mode' })).toBeNull()
    const dialog = openDrawer()
    const toggle = within(dialog).getByRole('button', { name: 'Presentation mode' })
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-pressed', 'true')
    expect(drawer()).not.toBeNull() // toggling is not a navigation
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
  })
})
