/**
 * BUG-009: every `/settings/*` sub-page offers the same way back, and no page
 * rolls its own.
 *
 * Measured before the fix: of 23 sub-pages, 5 rendered a `btn-secondary` button
 * labelled "Back", 4 rendered a muted breadcrumb labelled "Settings", and
 * **14 rendered nothing**. Three behaviours, one of which was "none".
 *
 * The route-contract test below is the one that matters. Asserting that the
 * component renders for `/settings/profile` proves only that one path works; it
 * is the enumeration over `App.tsx`'s declared routes that makes a *new*
 * sub-page unable to ship without the affordance. That is the actual request —
 * a consistent approach for all pages, not nine fixed pages.
 *
 * Sources are read with `import.meta.glob(..., '?raw')`, the pattern
 * `App.routeTargets.test.ts` and `routeScope.ratchet.test.ts` already use;
 * `node:fs` would need `@types/node`, which this tsconfig does not carry and
 * `npm run type-check` is a CI gate.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import SettingsBackBar from './SettingsBackBar'
import { isSettingsSubPage } from './settingsRoutes'

const APP_SOURCE = Object.values(
  import.meta.glob('../../App.tsx', { query: '?raw', import: 'default', eager: true }),
)[0] as string

const SETTINGS_PAGE_SOURCES = import.meta.glob('../../pages/settings/*.tsx', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

/** Every `settings/...` route declared in App.tsx's route tables. */
function declaredSettingsRoutes(): string[] {
  const matches = [...APP_SOURCE.matchAll(/\{ path: '(settings[^']*)', component: \w+ \}/g)]
  return matches.map((m) => `/${m[1]}`)
}

function renderAt(pathname: string) {
  return render(
    <MemoryRouter initialEntries={[pathname]}>
      <SettingsBackBar />
    </MemoryRouter>,
  )
}

describe('isSettingsSubPage', () => {
  it('is true for a settings sub-page', () => {
    expect(isSettingsSubPage('/settings/profile')).toBe(true)
    expect(isSettingsSubPage('/settings/ai-eval')).toBe(true)
  })

  it('is false for the settings index itself', () => {
    // The index is the destination; offering "Back to Settings" there is a loop.
    expect(isSettingsSubPage('/settings')).toBe(false)
    expect(isSettingsSubPage('/settings/')).toBe(false)
  })

  it('is false elsewhere, and is not fooled by a prefix match', () => {
    expect(isSettingsSubPage('/overview')).toBe(false)
    // Would be true under a naive `startsWith('/settings')`.
    expect(isSettingsSubPage('/settingsomething')).toBe(false)
  })
})

describe('SettingsBackBar rendering', () => {
  it('renders one labelled link to /settings on a sub-page', () => {
    renderAt('/settings/profile')
    const link = screen.getByRole('link', { name: /back to settings/i })
    expect(link).toHaveAttribute('href', '/settings')
  })

  it('renders nothing on the settings index', () => {
    const { container } = renderAt('/settings')
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing outside settings', () => {
    const { container } = renderAt('/runs')
    expect(container).toBeEmptyDOMElement()
  })
})

describe('the route contract — every settings sub-page, not just the fixed ones', () => {
  const routes = declaredSettingsRoutes()

  it('found the settings routes to check', () => {
    // Without this the suite below passes vacuously if the route shape changes.
    expect(routes.length).toBeGreaterThan(20)
    expect(routes).toContain('/settings')
    expect(routes).toContain('/settings/profile')
  })

  it('shows the bar on every declared sub-page and hides it on the index', () => {
    const missing: string[] = []
    for (const route of routes) {
      const { container, unmount } = renderAt(route)
      const shown = container.querySelector('a[href="/settings"]') !== null
      if (shown !== (route !== '/settings')) missing.push(route)
      unmount()
    }
    expect(
      missing,
      'these settings routes disagree with the rule "every sub-page offers one ' +
        'way back, the index offers none"',
    ).toEqual([])
  })
})

describe('AppLayout actually renders it', () => {
  // Without this the suite above proves only that the component works when
  // mounted. Every case renders `<SettingsBackBar />` directly, so deleting the
  // line from AppLayout would leave all of them green and every sub-page
  // without a way back — the original defect, with a passing test suite.
  const LAYOUT_SOURCE = Object.values(
    import.meta.glob('./AppLayout.tsx', { query: '?raw', import: 'default', eager: true }),
  )[0] as string

  it('imports the component', () => {
    expect(LAYOUT_SOURCE).toMatch(/import SettingsBackBar from '\.\/SettingsBackBar'/)
  })

  it('renders it, and above the routed page', () => {
    const bar = LAYOUT_SOURCE.indexOf('<SettingsBackBar />')
    // `lastIndexOf`, not `indexOf`: the explanatory comment above the JSX names
    // `<Outlet />` too, and matching that instead put the "anchor" before the
    // bar and failed a correct layout.
    const outlet = LAYOUT_SOURCE.lastIndexOf('<Outlet />')
    expect(bar, '<SettingsBackBar /> is not rendered by AppLayout').toBeGreaterThan(-1)
    expect(outlet).toBeGreaterThan(-1)
    expect(bar, 'the back link must precede the page content').toBeLessThan(outlet)
  })
})

describe('no settings page re-implements its own back control', () => {
  it('found the settings pages to check', () => {
    expect(Object.keys(SETTINGS_PAGE_SOURCES).length).toBeGreaterThan(20)
  })

  it('no page links to /settings itself', () => {
    // 11 such controls were removed from 9 files. A page-level copy is how the
    // three-way inconsistency arose, and how it would return.
    const offenders: string[] = []
    for (const [file, source] of Object.entries(SETTINGS_PAGE_SOURCES)) {
      if (file.includes('.test.')) continue
      if (/to=["']\/settings["']/.test(source)) offenders.push(file.split('/').pop() as string)
    }
    expect(
      offenders.sort(),
      'the back affordance belongs to AppLayout, so it cannot be forgotten on a ' +
        'new sub-page. A page-level copy renders a second control beside it.',
    ).toEqual([])
  })
})
