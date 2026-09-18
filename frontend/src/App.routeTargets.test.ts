/**
 * Every route literal a Playwright suite navigates to must be a route this app
 * actually declares.
 *
 * `App.tsx` ends with `<Route path="*" element={<Navigate to="/overview" />} />`,
 * so an undeclared path does not 404 — it silently lands on the dashboard. A
 * sweep that visits one therefore asserts "the page is not an error boundary,
 * not an empty shell" against **Overview**, passes, and reports coverage for a
 * page it never opened.
 *
 * That is exactly what `tests/probe-route-sweep.spec.ts` did with
 * `/failure-analysis`: the declared route is `/failures` (App.tsx) and the
 * sidebar links to `/failures`, so the sweep's Failure Analysis entry resolved
 * to `/overview`. Confirmed live against the homelab on 2026-09-18 — the final
 * pathname after navigation was `/overview`.
 *
 * This test is the class fix. The instance fix (spelling the route correctly)
 * would not stop the next one, because nothing else compares the two lists.
 *
 * It reads source text rather than importing the specs: the Playwright files
 * import `@playwright/test`, which cannot load inside the Vitest/jsdom run.
 */
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

const FRONTEND_ROOT = join(__dirname, '..')

/** Route paths declared in `App.tsx`'s two route tables, as `/`-prefixed. */
function declaredRoutes(): string[] {
  const source = readFileSync(join(FRONTEND_ROOT, 'src', 'App.tsx'), 'utf-8')
  const matches = [...source.matchAll(/\{ path: '([^']+)', component: \w+ \}/g)]
  expect(
    matches.length,
    'App.tsx route tables could not be parsed — the entry shape changed',
  ).toBeGreaterThan(50)
  // `/login` and `/reset-password` are declared outside the tables.
  return [...matches.map((m) => `/${m[1]}`), '/login', '/reset-password']
}

/**
 * Turn a declared path into a matcher. `runs/:runId` must accept
 * `/runs/<uuid>`, so each `:param` segment becomes a wildcard segment.
 */
function toMatcher(route: string): RegExp {
  const pattern = route
    .split('/')
    .map((segment) => (segment.startsWith(':') ? '[^/]+' : segment.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
    .join('/')
  return new RegExp(`^${pattern}$`)
}

/** Absolute-path route literals a spec navigates to. */
function routeLiteralsIn(source: string): string[] {
  const found = new Set<string>()
  // Bare string entries in a ROUTES-style array, plus goto()/URL templates.
  for (const m of source.matchAll(/['"`](\/[a-z0-9][a-z0-9/:-]*)['"`]/gi)) {
    found.add(m[1])
  }
  for (const m of source.matchAll(/\$\{BASE\}(\/[a-z0-9][a-z0-9/:-]*)/gi)) {
    found.add(m[1])
  }
  return [...found]
}

/**
 * Paths a spec may legitimately reference that are not SPA routes: API
 * endpoints it stubs or asserts on, and static assets. Kept narrow and
 * prefix-based — a blanket allow-list would defeat the test.
 */
const NON_ROUTE_PREFIXES = [
  '/api/',
  // Served by the backend, not the SPA router — the API reference lives there.
  '/api-docs',
  '/assets/',
  '/static/',
  '/favicon',
  '/app-icon',
]

function isNonRoute(path: string): boolean {
  return NON_ROUTE_PREFIXES.some((prefix) => path.startsWith(prefix))
}

describe('Playwright route targets resolve to declared routes', () => {
  const routes = declaredRoutes()
  const matchers = routes.map(toMatcher)
  /**
   * A literal ending in `/` is a prefix a template interpolates onto
   * (`${BASE}/runs/${runId}`), not a destination. Judge it by the route it
   * prefixes — a genuine typo like `/runz/` still prefixes nothing and fails.
   */
  const resolves = (path: string) => {
    if (matchers.some((matcher) => matcher.test(path))) return true
    if (!path.endsWith('/')) return false
    const trimmed = path.slice(0, -1)
    return (
      matchers.some((matcher) => matcher.test(trimmed)) ||
      routes.some((route) => route.startsWith(path))
    )
  }

  it('parses a plausible number of declared routes', () => {
    expect(routes.length).toBeGreaterThan(50)
    expect(routes).toContain('/failures')
    // The historical offender is NOT a route. If this ever becomes one, the
    // sweep's spelling stops being a defect and this test should be revisited.
    expect(routes).not.toContain('/failure-analysis')
  })

  it('every route literal in probe-route-sweep.spec.ts is declared', () => {
    const source = readFileSync(
      join(FRONTEND_ROOT, 'tests', 'probe-route-sweep.spec.ts'),
      'utf-8',
    )
    const offenders = routeLiteralsIn(source)
      .filter((path) => !isNonRoute(path))
      .filter((path) => !resolves(path))
    expect(
      offenders,
      'these paths are not declared in App.tsx, so the SPA catch-all sends them ' +
        'to /overview and the sweep asserts against the wrong page',
    ).toEqual([])
  })

  it('every route literal in the probe suite is declared', () => {
    const testsDir = join(FRONTEND_ROOT, 'tests')
    const specs = readdirSync(testsDir).filter(
      (name) => name.startsWith('probe-') && name.endsWith('.spec.ts'),
    )
    expect(specs.length, 'no probe specs found — the glob is wrong').toBeGreaterThan(10)

    const offenders: string[] = []
    for (const spec of specs) {
      const source = readFileSync(join(testsDir, spec), 'utf-8')
      for (const path of routeLiteralsIn(source)) {
        if (!isNonRoute(path) && !resolves(path)) {
          offenders.push(`${spec}: ${path}`)
        }
      }
    }
    expect(offenders.sort()).toEqual([])
  })
})
