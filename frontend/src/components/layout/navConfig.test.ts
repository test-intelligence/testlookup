import { matchPath } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { ADMIN_ITEM, NAV_ITEMS, owningNavItem, visibleNavItems } from './navConfig'

const APP_SOURCE: string = Object.values(
  import.meta.glob('../../App.tsx', { query: '?raw', import: 'default', eager: true }),
)[0] as string

/** Every routed path in App.tsx, as `/pattern`. */
const ROUTES = [...APP_SOURCE.matchAll(/path: '([^']+)'/g)].map((m) => `/${m[1]}`)

function routed(path: string): boolean {
  const pathname = path.split(/[?#]/)[0]
  return ROUTES.some((pattern) => matchPath({ path: pattern, end: true }, pathname) !== null)
}

/** A path under `prefix` that some route serves (the prefix itself, or a child pattern). */
function prefixServed(prefix: string): boolean {
  return ROUTES.some((pattern) => pattern === prefix || pattern.startsWith(`${prefix}/`))
}

describe('navConfig (UX redesign P1)', () => {
  it('reads the routes out of App.tsx', () => {
    // Without this the checks below could pass over an empty list.
    expect(ROUTES.length).toBeGreaterThan(50)
  })

  it('every item, and every section tab, links to a routed page', () => {
    const links = [...NAV_ITEMS, ADMIN_ITEM].flatMap((item) => [item.to, ...(item.tabs ?? []).map((t) => t.to)])
    expect(links.filter((to) => !routed(to))).toEqual([])
  })

  it('every owned prefix is served by a route (a typo would own nothing)', () => {
    const prefixes = [...NAV_ITEMS, ADMIN_ITEM].flatMap((item) => item.owns)
    expect(prefixes.filter((p) => !prefixServed(p))).toEqual([])
  })

  it('every section tab belongs to the item that shows it', () => {
    const strays = NAV_ITEMS.flatMap((item) =>
      (item.tabs ?? []).filter((tab) => owningNavItem(tab.to.split('?')[0])?.id !== item.id).map((tab) => `${item.id}: ${tab.to}`),
    )
    expect(strays).toEqual([])
  })

  it('ids are unique (tests and e2e select on them)', () => {
    const ids = [...NAV_ITEMS, ADMIN_ITEM].map((i) => i.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('11 places for a viewer, 13 for a QA lead or admin (with Ask AI; from 31)', () => {
    expect(visibleNavItems({ canAccessManagement: false, chatEnabled: true })).toHaveLength(11)
    expect(visibleNavItems({ canAccessManagement: true, chatEnabled: true }).length + 1).toBe(13)
  })

  it('the longest owned prefix wins', () => {
    expect(owningNavItem('/coverage')?.id).toBe('trends')
    expect(owningNavItem('/coverage/suite')?.id).toBe('suites')
    expect(owningNavItem('/release-gate')?.id).toBe('release-gate')
    expect(owningNavItem('/releases')?.id).toBe('releases')
    expect(owningNavItem('/settings/ai')?.id).toBe('admin')
    expect(owningNavItem('/settings/profile')).toBeNull()
    expect(owningNavItem('/search')).toBeNull()
    // A prefix owns a path segment, not a string: /runsX is not Runs.
    expect(owningNavItem('/runsX')).toBeNull()
  })
})
