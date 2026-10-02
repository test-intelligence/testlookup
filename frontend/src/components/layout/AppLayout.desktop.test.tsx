/**
 * VIZ-106 (Wave 2.6, OD-9): at 1024 px and above the app shell is the shell
 * it was before the narrow-screen drawer existed — the same elements, the
 * same attributes, the same classes.
 *
 * The snapshot beside this file was written from origin/main's shell
 * (71c022e0, before any VIZ-106 edit), and every later run compares the
 * desktop render against it. The drawer's own styling lives ONLY in
 * `max-lg:` utilities, which apply below 1024 px and nowhere else, so they
 * are removed before the comparison; any other class, attribute or element
 * that appears or moves at desktop width fails here.
 *
 * The one intended desktop addition is the presentation-mode toggle, a row
 * of the sidebar footer (PLAN OD-13, moved off the top bar: see Sidebar.tsx);
 * it is marked `data-presentation-toggle` and left out of the comparison by
 * name. The top bar and the page column gain nothing. `AppLayout.drawer.test.tsx` covers the
 * narrow shell itself.
 */
import { render } from '@testing-library/react'
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
vi.mock('./ScopeUrlSyncGate', () => ({ default: () => null }))

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
 * text trimmed. The presentation toggle is skipped (the intended addition).
 */
function desktopShape(node: Element, depth = 0): string[] {
  if (node.hasAttribute('data-presentation-toggle')) return []
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

  it.each(['/overview', '/trends', '/settings/profile'])('%s: elements, attributes and desktop classes are unchanged', (path) => {
    const { container } = renderShell(path)
    const root = container.firstElementChild
    if (!root) throw new Error('AppLayout rendered nothing')
    expect(desktopShape(root).join('\n')).toMatchSnapshot()
  })

  it('the one addition is the presentation toggle in the sidebar footer, after Settings; the top bar gains nothing; no drawer piece renders', () => {
    const { container } = renderShell('/overview')
    const toggles = container.querySelectorAll('[data-presentation-toggle]')
    expect(toggles).toHaveLength(1)
    const toggle = toggles[0]
    expect(toggle.closest('aside')).not.toBeNull()
    expect(toggle.closest('nav')).toBeNull()
    expect(toggle.previousElementSibling?.getAttribute('href')).toBe('/settings')
    expect(container.querySelector('header [data-presentation-toggle]')).toBeNull()
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
