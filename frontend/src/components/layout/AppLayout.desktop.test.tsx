/**
 * The desktop app shell (1024 px and above), element by element: the same
 * elements, attributes and classes on every run.
 *
 * The snapshot beside this file was first written from origin/main's shell
 * (71c022e0, VIZ-106) and re-written ONCE, deliberately, for the UX redesign
 * P1 shell: the flat `navConfig` sidebar with section labels and the rail
 * toggle, Admin in its footer, the Help menu before the bell, the theme and
 * presentation controls moved into the account menu, and the section tabs
 * above a multi-page section's pages (Trends · Coverage · Explorer on
 * `/trends`), and once more for P5: on a settings page the "Back to Settings"
 * bar is gone and the page sits in the settings layout (its grouped sub-nav,
 * loaded lazily: the case waits for it). Any other change at desktop width
 * fails here. The drawer's own
 * styling lives ONLY in `max-lg:` utilities, which apply below 1024 px, so
 * they are removed before the comparison. `AppLayout.drawer.test.tsx` covers
 * the narrow shell itself.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AppLayout from './AppLayout'

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ isAdmin: true, canAccessManagement: true }),
}))
vi.mock('@/hooks/useFeatureFlags', () => ({
  useFeatureEnabled: () => false,
  useFeatureFlags: () => ({ flags: [], isLoading: false, isError: false, refresh: vi.fn() }),
}))
vi.mock('@/hooks/useAIConfig', () => ({ useAIConfig: () => ({ data: undefined }) }))
vi.mock('@/hooks/useMyFailuresCountUnscoped', () => ({
  useMyFailuresCountUnscoped: () => ({ data: { count: 3 } }),
}))
vi.mock('@/hooks/useLiveRunningCount', () => ({ useLiveRunningCount: () => 1 }))
vi.mock('@/hooks/useNotifications', () => ({
  useUnreadCount: () => ({ data: { unread: 2 } }),
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

// Unchanged neighbours whose DOM depends on requests: stubbed identically on
// both sides of the comparison.
vi.mock('./ReleasePicker', () => ({ ReleasePicker: () => <div data-stub="release-picker" /> }))
vi.mock('./AppVersionBadge', () => ({ default: () => <div data-stub="app-version" /> }))
vi.mock('./DegradedBanner', () => ({ default: () => null }))

/** The Tailwind v4 `max-lg:` query, as the shell's narrow switch reads it. */
const NARROW_QUERY = '(width < 64rem)'

function installViewport(narrow: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: query === NARROW_QUERY ? narrow : false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  })
}

/** A `max-lg:` (or any `max-<bp>:`) utility applies only below its breakpoint. */
const NARROW_ONLY = /^max-[a-z0-9]+:/

/**
 * The shell as a desktop browser sees it: one line per element with every
 * attribute, `class` without its narrow-only utilities, React ids normalised,
 * text trimmed.
 */
function desktopShape(node: Element, depth = 0): string[] {
  const pad = '  '.repeat(depth)
  const attrs = Array.from(node.attributes)
    .map((a) => {
      if (a.name === 'class') {
        const kept = a.value.split(/\s+/).filter((c) => c && !NARROW_ONLY.test(c))
        return kept.length ? `class="${kept.join(' ')}"` : ''
      }
      return `${a.name}="${a.value.replace(/:r[0-9a-z]+:|«r[0-9a-z]+»/g, ':id:')}"`
    })
    .filter(Boolean)
    .sort()
  const lines = [`${pad}<${node.tagName.toLowerCase()}${attrs.length ? ' ' + attrs.join(' ') : ''}>`]
  for (const child of Array.from(node.childNodes)) {
    if (child.nodeType === Node.TEXT_NODE) {
      const text = child.textContent?.trim()
      if (text) lines.push(`${pad}  "${text}"`)
    } else if (child.nodeType === Node.ELEMENT_NODE) {
      lines.push(...desktopShape(child as Element, depth + 1))
    }
  }
  return lines
}

function renderShell(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path={path} element={<div>Page</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

describe('AppLayout at 1024 px and above: the shell origin/main shipped', () => {
  beforeEach(() => installViewport(false))
  afterEach(() => {
    // Leave jsdom as it found it: no matchMedia.
    delete (window as { matchMedia?: unknown }).matchMedia
  })

  it.each(['/overview', '/trends', '/settings/profile'])('%s: elements, attributes and desktop classes are unchanged', async (path) => {
    const { container } = renderShell(path)
    // The settings layout is a lazy chunk: the shape is the one a user sees once it is in.
    if (path.startsWith('/settings')) await screen.findByRole('navigation', { name: 'Settings' })
    const root = container.firstElementChild
    if (!root) throw new Error('AppLayout rendered nothing')
    expect(desktopShape(root).join('\n')).toMatchSnapshot()
  })

  it('P1: Help sits before the bell, the theme and presentation controls are in the closed account menu, no drawer piece renders', () => {
    const { container } = renderShell('/overview')
    const header = container.querySelector('header') as HTMLElement
    const help = header.querySelector('[data-help-menu]') as HTMLElement
    const bell = header.querySelector('button[aria-label^="Notifications"]') as HTMLElement
    expect(help.compareDocumentPosition(bell) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(container.querySelector('[data-presentation-toggle]')).toBeNull()
    expect(container.querySelector('[data-theme-picker]')).toBeNull()
    expect(container.querySelector('aside [data-stub="app-version"]')).toBeNull()
    expect(container.querySelector('[aria-label="Navigation menu"]')).toBeNull()
    expect(container.querySelector('[data-shell-backdrop]')).toBeNull()
    expect(container.querySelector('[role="dialog"]')).toBeNull()
  })

  it('measures something: a desktop class change or a new desktop attribute is caught', () => {
    const { container } = renderShell('/overview')
    const aside = container.querySelector('aside') as HTMLElement
    const before = desktopShape(container.firstElementChild as Element).join('\n')
    aside.classList.add('max-lg:fixed')
    expect(desktopShape(container.firstElementChild as Element).join('\n')).toBe(before)
    aside.classList.add('lg:hidden')
    expect(desktopShape(container.firstElementChild as Element).join('\n')).not.toBe(before)
    aside.classList.remove('lg:hidden')
    aside.setAttribute('role', 'dialog')
    expect(desktopShape(container.firstElementChild as Element).join('\n')).not.toBe(before)
  })
})
