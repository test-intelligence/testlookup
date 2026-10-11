/**
 * The route/action sweeps (`tests/sweeps`) visit every route in App.tsx. This
 * fails when App.tsx gains a route the sweeps lack -- or drops one they still
 * list -- so the "no page answers 500" check cannot quietly fall behind.
 */
import { describe, expect, it } from 'vitest'

import { MISSING_ID, ROUTE_TEMPLATES, concreteRoutes } from '../../tests/sweeps/routes'

// The repo's pattern for reading a source file in a test (App.routeTargets.test.ts).
const APP = Object.values(
  import.meta.glob('../App.tsx', { query: '?raw', import: 'default', eager: true }),
)[0] as string
const APP_ROUTES = [...APP.matchAll(/path: '([^']+)'/g)].map((m) => m[1])

describe('the sweeps cover every route', () => {
  it('lists exactly the routes App.tsx serves', () => {
    expect(APP_ROUTES.length).toBeGreaterThan(60)
    expect([...ROUTE_TEMPLATES].sort()).toEqual([...new Set(APP_ROUTES)].sort())
  })

  it('fills every parameter, with a stand-in for an id the project lacks', () => {
    const routes = concreteRoutes({ failedRun: 'r1', failedTest: 't1', passedRun: 'r2', passedTest: 't2' })
    expect(routes.every((r) => !r.includes(':'))).toBe(true)
    expect(routes).toContain('/runs/r1/tests/t1')
    expect(routes).toContain('/runs/r2/tests/t2')
    expect(routes).toContain(`/suites/${MISSING_ID}`)
    expect(routes).toContain('/docs/does-not-exist')
  })
})
