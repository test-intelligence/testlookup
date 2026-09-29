/**
 * VIZ-104 (Wave 2.5, PLAN 8.1) — the page-level chart announcer is mounted ONCE,
 * in `AppLayout`, for every routed page.
 *
 * Without a provider `useChartAnnouncer()` is `null`: a `ChartFrame` on a
 * production page would draw but never announce a change, and the report
 * chrome's filtered-dataset line would fall back to a live region of its own.
 * With TWO, a page would carry two pairs of live regions. So this holds both
 * halves: the layout provides exactly one, and no module a routed page renders
 * mounts another (the dev gallery and report-context pages, which do, are
 * routed outside the layout).
 */
import { act, render } from '@testing-library/react'
import { useEffect } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import AppLayout from './AppLayout'
import { ANNOUNCE_DEBOUNCE_MS, useChartAnnouncer } from '@/components/charts/ChartAnnouncer'

vi.mock('./TopBar', () => ({
  default: () => <div>TopBar Stub</div>,
}))

afterEach(() => vi.useRealTimers())

/** A page that reports one chart change, as a ChartFrame does after its first load. */
function ReportingPage({ seen }: { seen: (present: boolean) => void }) {
  const announcer = useChartAnnouncer()
  useEffect(() => {
    seen(announcer !== null)
    announcer?.report('frame-1', 'Execution trend', 'updated')
  }, [announcer, seen])
  return <div>Reporting page</div>
}

function renderLayout(page: React.ReactNode) {
  return render(
    <MemoryRouter initialEntries={['/overview']}>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="/overview" element={page} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

describe('AppLayout · the one chart announcer', () => {
  it('gives every routed page an announcer, with exactly one polite and one assertive region', () => {
    const seen = vi.fn()
    renderLayout(<ReportingPage seen={seen} />)
    expect(seen).toHaveBeenCalledWith(true)
    expect(document.querySelectorAll('[data-chart-announcer="polite"]')).toHaveLength(1)
    expect(document.querySelectorAll('[data-chart-announcer="assertive"]')).toHaveLength(1)
    expect(document.querySelectorAll('[data-chart-announcer]')).toHaveLength(2)
  })

  it('announces a routed page’s chart change through that one region', () => {
    vi.useFakeTimers()
    renderLayout(<ReportingPage seen={() => {}} />)
    const polite = document.querySelector('[data-chart-announcer="polite"]') as HTMLElement
    expect(polite.textContent).toBe('')
    act(() => {
      vi.advanceTimersByTime(ANNOUNCE_DEBOUNCE_MS)
    })
    expect(polite.textContent).toBe('Execution trend chart: updated')
    expect(polite.closest('main')).not.toBeNull()
  })
})

/** Every non-test source that renders `<ChartAnnouncerProvider`, keyed by path. */
const SOURCES = import.meta.glob(['/src/**/*.tsx', '!/src/**/*.test.tsx'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

const APP_SOURCE = Object.entries(
  import.meta.glob('/src/App.tsx', { query: '?raw', import: 'default', eager: true }) as Record<string, string>,
)[0][1]

/** The mounts allowed: the layout, and the two dev pages routed OUTSIDE it (checked below). */
const ALLOWED_MOUNTS = [
  '/src/components/layout/AppLayout.tsx',
  '/src/pages/dev/ChartGalleryPage.tsx',
  '/src/pages/dev/ReportContextPage.tsx',
]

describe('AppLayout · no second announcer', () => {
  it('is the only production module that mounts a ChartAnnouncerProvider', () => {
    expect(Object.keys(SOURCES).length).toBeGreaterThan(100)
    const mounts = Object.entries(SOURCES)
      .filter(([, source]) => /<ChartAnnouncerProvider[\s>]/.test(source))
      .map(([path]) => path)
      .sort()
    expect(mounts).toEqual([...ALLOWED_MOUNTS].sort())
  })

  it('routes the dev pages that mount their own provider outside the layout', () => {
    const layoutRoute = APP_SOURCE.indexOf('element={<AppLayout />}')
    expect(layoutRoute).toBeGreaterThan(0)
    for (const path of ['/__charts', '/__report-context']) {
      const at = APP_SOURCE.indexOf(`path="${path}"`)
      expect(at, `${path} is routed`).toBeGreaterThan(0)
      expect(at, `${path} is routed before (outside) the AppLayout route`).toBeLessThan(layoutRoute)
    }
  })
})
