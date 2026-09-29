/**
 * VIZ-407 "Promote zoom to filter", end to end through the address bar: the
 * Apply action writes the window through the report filter bar's own setter,
 * and — with `viz_multi_filters` on — the real URL sync (`useScopeUrlSyncCore`,
 * as AppLayout mounts it) carries it into `?window=`. A link with that window
 * then opens with it. Driven through a real data router, as the sync's own
 * tests are.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { Outlet, RouterProvider, createMemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }))
vi.mock('@/services/releasesService', () => ({
  releasesService: { list: vi.fn(async () => ({ items: [], total: 0 })), get: vi.fn() },
}))
vi.mock('@/services/suitesService', () => ({ suitesService: { list: vi.fn(async () => ({ items: [], total: 0 })) } }))
vi.mock('@/services/runsService', () => ({ runsService: { list: vi.fn(async () => ({ items: [], total: 0 })) } }))
vi.mock('@/services/api', () => ({ api: { get: vi.fn(async () => ({ data: {} })) } }))
vi.mock('recharts', () => {
  const pass = ({ children }: { children?: ReactNode }) => <div>{children}</div>
  return {
    // The recharts hooks Wave 2.4's tooltip content reads (`ChartTooltip`'s `usePlotArea`, the bars' `useXAxisScale`): listed so a mock that ever renders that content does not throw.
    usePlotArea: () => undefined,
    useXAxisScale: () => undefined,
    ResponsiveContainer: pass,
    ComposedChart: pass,
    CartesianGrid: () => null,
    Legend: () => null,
    XAxis: () => null,
    YAxis: () => null,
    Tooltip: () => null,
    Line: () => null,
    Bar: pass,
    Cell: () => null,
    ReferenceLine: () => null,
    ReferenceDot: () => null,
  }
})

import { useScopeUrlSyncCore } from '@/hooks/useScopeUrlSync'
import { useMultiFiltersFlagStore } from '@/store/multiFiltersFlag'
import { useProjectStore } from '@/store/projectStore'
import { useReleaseStore } from '@/store/releaseStore'
import { useSuiteStore } from '@/store/suiteStore'
import { useTimeWindowStore } from '@/store/timeWindowStore'
import { useScopeNoticeStore } from '@/store/scopeNoticeStore'
import { REPORT_WINDOW_OPTIONS } from '@/components/filters/filterOptions'
import TimeSeriesChartFrame from '../TimeSeriesChartFrame'
import { buildTimeSeriesModel } from '../timeSeriesModel'
import { addUtcDays } from '../seriesAlignment'
import { RESET_ZOOM_LABEL } from './ChartRangeBrush'

const D = (i: number) => addUtcDays('2026-09-01', i)
const model = buildTimeSeriesModel({
  points: Array.from({ length: 30 }, (_, i) => ({ x: D(i), rate: 90, rateReason: null, executions: 10, n: 10, partial: false })),
})

function Host() {
  useScopeUrlSyncCore()
  return <Outlet />
}

function Trends() {
  return (
    <TimeSeriesChartFrame
      title="Pass rate trend"
      headingLevel={2}
      model={model}
      state={{ status: 'ready', data: {}, meta: null, revalidating: false }}
      zoom={{ applyAsWindow: { windowOptions: REPORT_WINDOW_OPTIONS } }}
    />
  )
}

function mount(url: string) {
  const router = createMemoryRouter([{ path: '/', element: <Host />, children: [{ path: 'trends', element: <Trends /> }] }], {
    initialEntries: [url],
  })
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <RouterProvider router={router} />
    </SWRConfig>,
  )
  return router
}

const windowParam = (router: ReturnType<typeof createMemoryRouter>) =>
  new URLSearchParams(router.state.location.search).get('window')

beforeEach(() => {
  localStorage.clear()
  useMultiFiltersFlagStore.setState({ enabled: true })
  useProjectStore.setState({ activeProjectId: null })
  useReleaseStore.setState({ activeReleaseId: null, activeReleaseIds: [], scopedProjectId: null })
  useSuiteStore.setState({ activeSuiteNames: [], scopedProjectId: null })
  useTimeWindowStore.setState({ days: 30 })
  useScopeNoticeStore.setState({ notices: [] })
})

describe('Apply as time filter — the URL round trip', () => {
  it('writes the brushed window into ?window= and the zoom gives way to it', async () => {
    const router = mount('/trends?window=30')
    await waitFor(() => expect(windowParam(router)).toBe('30'))
    const start = screen.getByRole('slider', { name: 'Start of zoom range' })
    fireEvent.keyDown(start, { key: 'End' })
    for (let i = 1; i < 14; i++) fireEvent.keyDown(start, { key: 'ArrowLeft' })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Apply as time filter: last 14 days' }))
    })
    await waitFor(() => expect(windowParam(router)).toBe('14'))
    expect(useTimeWindowStore.getState().days).toBe(14)
    expect(screen.queryByRole('button', { name: RESET_ZOOM_LABEL })).toBeNull()
  })

  it('a link carrying that window opens with it', async () => {
    const router = mount('/trends?window=14')
    await waitFor(() => expect(useTimeWindowStore.getState().days).toBe(14))
    expect(windowParam(router)).toBe('14')
  })
})
