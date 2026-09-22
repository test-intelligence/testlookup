/**
 * The report-route ratchet (VIZ-301): the registry keeps describing the code.
 *
 *   1. every registered report route is a real route in App.tsx;
 *   2. every route in App.tsx is either a report route or declared NOT one,
 *      with a reason — a new page cannot land undecided;
 *   3. at every registered route the layout slot mounts EXACTLY ONE report
 *      chrome, and at every other route none;
 *   4. no page renders the chrome or the header itself (the layout's mount
 *      is the only one), and the layout mounts the slot exactly once.
 *
 * Sources are read with `import.meta.glob(?raw)` like `routeScope.ratchet`.
 */
import { describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Suspense } from 'react'

/** `enabled`: viz_report_context (asked by key); `multi`: viz_multi_filters as the store resolved it. */
const flag = vi.hoisted(() => ({ enabled: true, multi: true, asked: [] as string[] }))
vi.mock('@/hooks/useFeatureFlags', () => ({
  useFeatureEnabled: (key: string) => {
    flag.asked.push(key)
    return key === 'viz_report_context' ? flag.enabled : false
  },
}))
vi.mock('@/store/multiFiltersFlag', () => ({ useMultiFiltersEnabled: () => flag.multi }))
vi.mock('./ReportChrome', () => ({
  default: ({ route }: { route: string }) => <div data-report-chrome="" data-route={route} />,
}))

import ReportChromeSlot from './ReportChromeSlot'
import { matchReportRoute, NOT_REPORT_PREFIXES, NOT_REPORT_ROUTES, REPORT_ROUTES } from './reportRoutes'

const APP = Object.values(
  import.meta.glob('../../App.tsx', { query: '?raw', import: 'default', eager: true }) as Record<string, string>,
)[0]
const PAGES = import.meta.glob('../../pages/**/*.tsx', { query: '?raw', import: 'default', eager: true }) as Record<
  string,
  string
>
const LAYOUT = import.meta.glob('../layout/*.tsx', { query: '?raw', import: 'default', eager: true }) as Record<
  string,
  string
>

function appRoutes(): string[] {
  const out = [...(APP ?? '').matchAll(/\{\s*path:\s*'([^']+)',\s*component:\s*\w+\s*\}/g)].map((m) => `/${m[1]}`)
  return Array.from(new Set(out))
}

/** A concrete URL for a pattern: each `:param` becomes a sample value. */
const concrete = (pattern: string) => pattern.replace(/:(\w+)/g, (_, name: string) => `sample-${name}`)

async function mountAt(path: string) {
  const view = render(
    <MemoryRouter initialEntries={[path]}>
      <Suspense fallback={null}>
        <ReportChromeSlot />
      </Suspense>
    </MemoryRouter>,
  )
  // The chrome is lazy: let the (mocked) module resolve.
  await vi.waitFor(() => {
    if (matchReportRoute(path) && flag.enabled && flag.multi) {
      expect(view.container.querySelectorAll('[data-report-chrome]').length).toBeGreaterThan(0)
    }
  })
  const count = view.container.querySelectorAll('[data-report-chrome]').length
  view.unmount()
  return count
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

describe('ReportChromeSlot — one mount per report route', () => {
  it.each(REPORT_ROUTES.map((r) => [r]))('%s mounts exactly one chrome', async (route) => {
    flag.enabled = true
    expect(await mountAt(concrete(route))).toBe(1)
  })

  it('no non-report route mounts it', async () => {
    flag.enabled = true
    for (const route of appRoutes().filter((r) => !(REPORT_ROUTES as readonly string[]).includes(r))) {
      expect(await mountAt(concrete(route)), route).toBe(0)
    }
  })

  it('a report route with the flag off mounts nothing', async () => {
    flag.enabled = false
    expect(await mountAt('/overview')).toBe(0)
    flag.enabled = true
  })

  // Fix round B (M1): with viz_multi_filters off the pages keep their legacy
  // scope (page-local suite, one release) while the chrome would read and
  // write the global multi-value stores -- chrome and page would disagree.
  // The chrome renders ONLY with both flags on.
  it.each([
    [true, true, 1],
    [true, false, 0],
    [false, true, 0],
    [false, false, 0],
  ])('viz_report_context=%s, viz_multi_filters=%s -> %i chrome', async (reportContext, multi, expected) => {
    flag.enabled = reportContext
    flag.multi = multi
    flag.asked = []
    try {
      expect(await mountAt('/trends')).toBe(expected)
      expect(flag.asked).toContain('viz_report_context')
    } finally {
      flag.enabled = true
      flag.multi = true
    }
  })

  it('does not treat a sub-path or a sibling as a report route', () => {
    expect(matchReportRoute('/runs/compare')).toBe('/runs/compare')
    expect(matchReportRoute('/runs/abc/intelligence')).toBe('/runs/:runId/intelligence')
    expect(matchReportRoute('/runs/abc')).toBeNull()
    expect(matchReportRoute('/release-gate/abc')).toBeNull()
    expect(matchReportRoute('/overview/extra')).toBeNull()
  })
})

describe('the layout owns the only mount', () => {
  it('found the page and layout sources (fail closed)', () => {
    expect(Object.keys(PAGES).length).toBeGreaterThan(40)
    expect(Object.keys(LAYOUT).length).toBeGreaterThan(3)
  })

  const MOUNTS_CHROME = /<(ReportChrome|ReportChromeView|ReportChromeSlot|ReportContextHeader)\b/

  it('detects a page-level mount when it sees one (positive control)', () => {
    expect(MOUNTS_CHROME.test('return <ReportContextHeader meta={m} allProjects={false} />')).toBe(true)
    expect(MOUNTS_CHROME.test('return <ReportChromeView {...p} />')).toBe(true)
    expect(MOUNTS_CHROME.test('<ReportContextHeaderish />')).toBe(false)
  })

  it('no product page renders the chrome or a context header itself', () => {
    const offenders = Object.entries(PAGES)
      .filter(([file]) => !file.includes('/pages/dev/') && !/\.test\.tsx$/.test(file))
      .filter(([, src]) => MOUNTS_CHROME.test(src))
      .map(([file]) => file)
    expect(offenders, 'a page mounting its own header would render two on a report route').toEqual([])
  })

  it('the layout mounts the slot exactly once', () => {
    const mounts = Object.entries(LAYOUT)
      .filter(([file]) => !/\.test\.tsx$/.test(file))
      // `<runtime.ReportChromeSlot />`: the slot ships in the lazy multi-filters
      // runtime (components/layout/multiFiltersRuntime.ts), mounted by AppLayout.
      .reduce((n, [, src]) => n + (src.match(/<(?:\w+\.)?ReportChromeSlot\b/g)?.length ?? 0), 0)
    expect(mounts).toBe(1)
  })
})
