/**
 * The route → scope registry.
 *
 * The bug this exists to stop: a link offered from a state in which its
 * destination cannot show anything. `OverviewPage` linked to `/flaky-coach`
 * from a card holding a cross-project count, while All Projects — the app's
 * DEFAULT selection — is exactly the state that page refuses to render in.
 *
 * Most of these cases are about `normalizeRoutePath`, because a scope check
 * that silently fails to match reads as "reachable", which is the original bug
 * with an extra step.
 */
import { describe, expect, it } from 'vitest'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import {
  isRouteReachable,
  normalizeRoutePath,
  routeScope,
  singleProjectRoutes,
} from './routeScope'

const A_PROJECT = 'aaaaaaaa-0000-0000-0000-000000000001'

describe('routeScope', () => {
  it('reports the declared routes as single-project', () => {
    for (const route of singleProjectRoutes()) {
      expect(routeScope(route), `${route} lost its declaration`).toBe(
        'single-project',
      )
    }
  })

  it('treats an undeclared route as unrestricted', () => {
    // Absence means unrestricted, deliberately: the registry declares
    // restrictions. The alternative — defaulting to restricted — would put a
    // "pick a project" qualifier on every link in the app.
    expect(routeScope('/runs')).toBe('any')
    expect(routeScope('/coverage')).toBe('any')
    expect(routeScope('/a-page-that-does-not-exist')).toBe('any')
  })

  it('names the eight pages that gate on All Projects and no others', () => {
    // A literal list, so widening the registry is a decision someone makes
    // rather than a side effect. The ratchet test proves this list matches the
    // code; this one proves it matches intent.
    expect(singleProjectRoutes()).toEqual([
      // /activity joined in the Activity ledger epic (ACT): a feed of "what
      // happened" has no meaning across a tenant, so the page renders the
      // project picker prompt in All Projects mode.
      '/activity',
      '/flaky-coach',
      // /reviews joined in E8.5: the review queue is listed per project, so
      // All Projects mode renders the picker prompt instead of a queue.
      '/reviews',
      '/settings/api-keys',
      '/settings/github',
      '/settings/gitlab',
      '/settings/retention',
      '/settings/webhooks',
    ])
  })
})

describe('normalizeRoutePath', () => {
  it('ignores a query string', () => {
    // Real call sites carry them — `/test-management?tab=Test+Suites`. Matching
    // raw would miss every parameterised link.
    expect(normalizeRoutePath('/flaky-coach?days=30')).toBe('/flaky-coach')
    expect(routeScope('/flaky-coach?days=30')).toBe('single-project')
  })

  it('ignores a fragment', () => {
    expect(normalizeRoutePath('/settings/retention#purge')).toBe(
      '/settings/retention',
    )
    expect(routeScope('/settings/retention#purge')).toBe('single-project')
  })

  it('adds a missing leading slash', () => {
    expect(normalizeRoutePath('flaky-coach')).toBe('/flaky-coach')
    expect(routeScope('flaky-coach')).toBe('single-project')
  })

  it('drops a trailing slash without eating the root', () => {
    expect(normalizeRoutePath('/flaky-coach/')).toBe('/flaky-coach')
    expect(normalizeRoutePath('/')).toBe('/')
  })

  it('does not match a route that merely starts the same', () => {
    // `/settings/github-actions` is a different page. Prefix matching here
    // would put a scope qualifier on links that do not need one.
    expect(routeScope('/settings/github-actions')).toBe('any')
    expect(routeScope('/flaky-coach-archive')).toBe('any')
  })
})

describe('isRouteReachable', () => {
  it('lets any scope reach an unrestricted route', () => {
    expect(isRouteReachable('/runs', ALL_PROJECTS_ID)).toBe(true)
    expect(isRouteReachable('/runs', null)).toBe(true)
    expect(isRouteReachable('/runs', A_PROJECT)).toBe(true)
  })

  it('refuses a single-project route while All Projects is active', () => {
    // The reported bug, as one assertion.
    expect(isRouteReachable('/flaky-coach', ALL_PROJECTS_ID)).toBe(false)
  })

  it('refuses a single-project route when nothing is selected', () => {
    // The store promotes null to the sentinel on read, but a component can
    // observe the pre-hydration value. Answering "reachable" there produces
    // exactly the dead end this registry exists to prevent.
    expect(isRouteReachable('/flaky-coach', null)).toBe(false)
  })

  it('allows a single-project route once one project is pinned', () => {
    expect(isRouteReachable('/flaky-coach', A_PROJECT)).toBe(true)
  })
})
