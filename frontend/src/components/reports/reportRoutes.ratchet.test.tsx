/**
 * The report-route ratchet (VIZ-301): the registry keeps describing the code.
 *
 *   1. every registered report route is a real route in App.tsx;
 *   2. every route in App.tsx is either a report route or declared NOT one,
 *      with a reason — a new page cannot land undecided.
 *
 * (The report-context panel these routes once mounted is gone, Phase D M0;
 * the registry stays for saved views.) Sources are read with
 * `import.meta.glob(?raw)` like `routeScope.ratchet`.
 */
import { describe, expect, it } from 'vitest'
import { matchReportRoute, NOT_REPORT_PREFIXES, NOT_REPORT_ROUTES, REPORT_ROUTES } from './reportRoutes'

const APP = Object.values(
  import.meta.glob('../../App.tsx', { query: '?raw', import: 'default', eager: true }) as Record<string, string>,
)[0]

function appRoutes(): string[] {
  const out = [...(APP ?? '').matchAll(/\{\s*path:\s*'([^']+)',\s*component:\s*\w+\s*\}/g)].map((m) => `/${m[1]}`)
  return Array.from(new Set(out))
}

describe('report routes — held to App.tsx', () => {
  const routes = appRoutes()

  it('parsed the route table (fail closed)', () => {
    expect(APP, 'App.tsx not globbed').toBeTypeOf('string')
    expect(routes.length, 'no routes parsed out of App.tsx — the table shape changed').toBeGreaterThan(40)
  })

  it('every registered report route is a real route', () => {
    const missing = REPORT_ROUTES.filter((r) => !routes.includes(r))
    expect(missing, 'registered report routes with no App.tsx route').toEqual([])
  })

  it('every App.tsx route is classified: a report, or not one with a reason', () => {
    const unclassified = routes.filter(
      (r) =>
        !(REPORT_ROUTES as readonly string[]).includes(r) &&
        !(r in NOT_REPORT_ROUTES) &&
        !NOT_REPORT_PREFIXES.some((p) => r.startsWith(p)),
    )
    expect(
      unclassified,
      'decide whether these are report routes: add them to REPORT_ROUTES or NOT_REPORT_ROUTES in reportRoutes.ts',
    ).toEqual([])
  })

  it('declares nothing stale as not-a-report, and nothing is in both lists', () => {
    expect(Object.keys(NOT_REPORT_ROUTES).filter((r) => !routes.includes(r))).toEqual([])
    expect(REPORT_ROUTES.filter((r) => r in NOT_REPORT_ROUTES)).toEqual([])
    for (const reason of Object.values(NOT_REPORT_ROUTES)) expect(reason.trim()).not.toBe('')
  })

  it('covers exactly the story list (VIZ-301)', () => {
    expect([...REPORT_ROUTES].sort()).toEqual(
      [
        '/overview',
        '/trends',
        '/coverage',
        '/coverage/suite',
        '/failures',
        '/defects',
        '/reports/summary',
        '/value-metrics',
        '/intelligence',
        '/runs/:runId/intelligence',
        '/release-gate',
        '/runs/compare',
        '/flaky-coach',
      ].sort(),
    )
  })
})

describe('matchReportRoute', () => {
  it('does not treat a sub-path or a sibling as a report route', () => {
    expect(matchReportRoute('/runs/compare')).toBe('/runs/compare')
    expect(matchReportRoute('/runs/abc/intelligence')).toBe('/runs/:runId/intelligence')
    expect(matchReportRoute('/runs/abc')).toBeNull()
    expect(matchReportRoute('/release-gate/abc')).toBeNull()
    expect(matchReportRoute('/overview/extra')).toBeNull()
  })
})
