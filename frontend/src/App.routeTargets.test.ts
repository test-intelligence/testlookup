/**
 * Every route literal a Playwright suite navigates to must be a route this app
 * actually declares.
 *
 * `App.tsx` used to end with `<Route path="*" element={<Navigate to="/overview" />} />`,
 * so an undeclared path did not 404 — it silently landed on the dashboard. A
 * sweep that visited one therefore asserted "the page is not an error boundary,
 * not an empty shell" against **Overview**, passed, and reported coverage for a
 * page it never opened. Since HON-09 the in-shell catch-all renders "Page not
 * found" (`routing/legacyRedirects.UnmatchedPath`), which a sweep would at
 * least see; this test still catches the typo before any browser runs.
 *
 * That is exactly what `tests/probe-route-sweep.spec.ts` did with
 * `/failure-analysis`: the declared route is `/failures` (App.tsx) and the
 * sidebar links to `/failures`, so the sweep's Failure Analysis entry resolved
 * to `/overview`. `probe-exploratory.spec.ts` did the same with `/tests`
 * (labelled "Test management") and `/flaky` (labelled "Flaky coach").
 * Confirmed live against the homelab on 2026-09-18 — the final pathname after
 * navigating to `/failure-analysis` was `/overview`.
 *
 * This test is the class fix. The instance fix (spelling the routes correctly)
 * would not stop the next one, because nothing else compares the two lists.
 *
 * Sources are loaded with `import.meta.glob(..., '?raw')`, the pattern
 * `routeScope.ratchet.test.ts` and `formLabels.test.ts` already use here.
 * `node:fs` would need `@types/node`, which this tsconfig does not carry — and
 * `npm run type-check` is a CI gate.
 */
import { describe, expect, it } from 'vitest'

const APP_SOURCE = Object.values(
  import.meta.glob('./App.tsx', { query: '?raw', import: 'default', eager: true }),
)[0] as string

/**
 * Every Playwright spec. The live probe specs this guard was written for
 * (`tests/probe-*.spec.ts`) are no longer in the repository; the class of bug
 * applies to any spec that navigates.
 */
const SPEC_SOURCES = import.meta.glob('../tests/**/*.spec.ts', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

/** Route paths declared in `App.tsx`'s two route tables, as `/`-prefixed. */
function declaredRoutes(): string[] {
  const matches = [...APP_SOURCE.matchAll(/\{ path: '([^']+)', component: \w+ \}/g)]
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
    .map((segment) =>
      segment.startsWith(':') ? '[^/]+' : segment.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'),
    )
    .join('/')
  return new RegExp(`^${pattern}$`)
}

/**
 * A backtick-quoted regex literal in a comment looks exactly like a quoted
 * route: `` `/pipeline/i` `` matched and was reported as an undeclared route.
 * A real route never ends in regex flags.
 */
const REGEX_LITERAL = /\/[gimsuy]{1,4}$/

/**
 * Absolute-path route literals a spec navigates to with `page.goto(...)`,
 * either quoted (`goto('/runs')`) or after an interpolated base
 * (`` goto(`${BASE}/runs`) ``). Only navigations count: a quoted path in a
 * `page.route(...)` stub is an API path the spec mocks, not a page it opens.
 */
function routeLiteralsIn(source: string): string[] {
  const found = new Set<string>()
  for (const m of source.matchAll(/goto\(\s*['"`](\/[a-z0-9][a-z0-9/:-]*)/gi)) found.add(m[1])
  for (const m of source.matchAll(/goto\(\s*`\$\{\w+\}(\/[a-z0-9][a-z0-9/:-]*)/gi)) found.add(m[1])
  return [...found].filter((path) => !REGEX_LITERAL.test(path))
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

/**
 * Paths a spec visits precisely BECAUSE they are undeclared: the catch-all's
 * not-found page, and `/audit`, a forwarding address the catch-all redirects
 * to Activity (HON-09: linked by /releases, never routed; bookmarks exist).
 */
const DELIBERATELY_UNDECLARED = new Set(['/this-route-does-not-exist', '/audit'])

function isNonRoute(path: string): boolean {
  return (
    DELIBERATELY_UNDECLARED.has(path) || NON_ROUTE_PREFIXES.some((prefix) => path.startsWith(prefix))
  )
}

describe('Playwright route targets resolve to declared routes', () => {
  const routes = declaredRoutes()
  const matchers = routes.map(toMatcher)

  /**
   * A literal ending in `/` is a prefix a template interpolates onto
   * (`${BASE}/runs/${runId}`), not a destination. Judge it by the route it
   * prefixes — a genuine typo like `/runz/` still prefixes nothing and fails.
   */
  const resolves = (path: string): boolean => {
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

  it('found the Playwright specs to check', () => {
    // Without this the suite below passes vacuously if the glob ever misses.
    expect(Object.keys(SPEC_SOURCES).length).toBeGreaterThan(10)
    const navigations = Object.values(SPEC_SOURCES).flatMap(routeLiteralsIn)
    expect(navigations.length, 'no page.goto route literals found').toBeGreaterThan(20)
  })

  it('every route a Playwright spec navigates to is declared', () => {
    const offenders: string[] = []
    for (const [file, source] of Object.entries(SPEC_SOURCES)) {
      const name = file.split('/').pop()
      for (const path of routeLiteralsIn(source)) {
        if (!isNonRoute(path) && !resolves(path)) offenders.push(`${name}: ${path}`)
      }
    }
    expect(
      offenders.sort(),
      'these paths are not declared in App.tsx, so the SPA catch-all renders ' +
        '"Page not found" for them and the spec asserts against the wrong page',
    ).toEqual([])
  })
})

describe('the in-shell catch-all (HON-09)', () => {
  it('renders the not-found page, not a redirect to /overview', () => {
    // Exactly one `path="*"` route renders UnmatchedPath (inside AppLayout).
    expect(APP_SOURCE.match(/<Route path="\*" element=\{<UnmatchedPath \/>\} \/>/g)).toHaveLength(1)
    // The outer, out-of-shell catch-all may still redirect; the in-shell one
    // may not. Every unknown path is matched by `/*` (AppLayout) first, so a
    // second `*` → /overview inside it would be the old behaviour back.
    const inShell = APP_SOURCE.slice(APP_SOURCE.indexOf('<Route path="/*" element={<AppLayout />}>'))
    const shellBody = inShell.slice(0, inShell.indexOf('</Route>'))
    expect(shellBody).not.toMatch(/path="\*" element=\{<Navigate to="\/overview"/)
  })
})
