/**
 * W11 GATE-04: Trends' pass-rate line is the policy's GO line.
 *
 * The bands ride the page's EXISTING summary read (`useDashboardSummary`:
 * `summary.readiness_policy`): the dashed line is the policy's `yellow_min`,
 * the zone under `orange_min` is shaded (the target's `floor`), and the
 * takeaway names the policy. With no `readiness_policy` (no evidence, an
 * entry cached before the deploy) the defaults apply — 95 / 90, labelled
 * `default bands` — never an unlabelled line.
 */
import { render, screen } from '@testing-library/react'
import type { ComponentProps } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type TimeSeriesChartFrame from '@/components/charts/TimeSeriesChartFrame'
import TrendsPage from './TrendsPage'

const frames = vi.hoisted(() => ({ timeSeries: [] as unknown[] }))

vi.mock('@/components/charts/TimeSeriesChartFrame', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/components/charts/TimeSeriesChartFrame')>()
  const Real = actual.default
  function SpiedTimeSeriesChartFrame(props: ComponentProps<typeof TimeSeriesChartFrame>) {
    frames.timeSeries.push(props)
    return <Real {...props} />
  }
  return { ...actual, default: SpiedTimeSeriesChartFrame }
})
vi.mock('@/utils/errorReporting', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/utils/errorReporting')>(),
  reportBoundaryError: vi.fn(),
}))
vi.mock('@/services/savedViewsService', () => ({
  listSavedViews: vi.fn(async () => []),
  createSavedView: vi.fn(),
  updateSavedView: vi.fn(),
  deleteSavedView: vi.fn(),
}))
vi.mock('@/hooks/useReleases', () => ({ useReleases: () => ({ data: { items: [] } }) }))
vi.mock('@/components/reports/catalogue/TrendsCatalogue', () => ({
  default: () => <div data-testid="trends-catalogue" />,
}))
vi.mock('@/hooks/useMetrics', () => ({ useTrendData: vi.fn(), useDashboardSummary: vi.fn(), useCoverage: vi.fn() }))
vi.mock('@/hooks/useRuns', () => ({ useRuns: vi.fn() }))
vi.mock('@/hooks/useSuiteOptions', () => ({ useSuiteOptions: vi.fn(() => ({ options: [], isLoading: false })) }))
vi.mock('@/hooks/useAnalyticsView', () => ({ useAnalyticsView: () => ({ widgetIds: [], setWidgets: vi.fn(), save: vi.fn() }) }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: { activeProjectId: string; activeProject: { name: string } | null }) => unknown) =>
    selector({ activeProjectId: 'proj-1', activeProject: { name: 'Payments' } })),
}))
vi.mock('@/hooks/useTrendSummary', () => ({
  useTrendSummary: () => ({ summary: null, isLoading: false, error: null }),
}))
vi.mock('@/hooks/useSuiteSparklines', () => ({
  useSuiteSparklines: () => ({ lineOf: () => undefined, state: 'ready' }),
}))
vi.mock('@/hooks/useTimelineNotes', () => ({
  useTimelineNotes: () => ({ notes: [], total: 0, truncated: false, isLoading: false, unavailable: false, mutate: async () => undefined }),
  useTimelineNoteActions: () => ({ projectId: 'proj-1', create: vi.fn(), update: vi.fn(), remove: vi.fn() }),
}))
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ role: 'VIEWER', isQaLead: false, isQaEngineer: false, isAdmin: false, hasRole: () => false }),
}))

type FrameProps = ComponentProps<typeof TimeSeriesChartFrame>

function lastTimeSeries(): FrameProps {
  const props = frames.timeSeries[frames.timeSeries.length - 1] as FrameProps | undefined
  if (!props) throw new Error('the pass-rate frame was not rendered')
  return props
}

const PAYMENTS_POLICY = {
  id: '11111111-2222-3333-4444-555555555555',
  name: 'Payments gate',
  version: 4,
  level: 'project',
  bands: { orange_min: 92, yellow_min: 97, green_min: 99 },
}

async function renderTrends(summary: Record<string, unknown>) {
  const { useTrendData, useDashboardSummary, useCoverage } = await import('@/hooks/useMetrics')
  const { useRuns } = await import('@/hooks/useRuns')
  const { shiftDayIso, utcDayIso } = await import('@/utils/calendarDay')
  // Four measured days at 88 % (88 of 100 each): the window's rate is 88.0 %.
  const trend = [10, 11, 12, 13].map((back) => ({
    date: shiftDayIso(utcDayIso(), -back), passed: 88, failed: 12, skipped: 0, broken: 0, total: 100, pass_rate: 88,
  }))
  vi.mocked(useTrendData).mockReturnValue({ data: { data: trend }, isLoading: false } as never)
  vi.mocked(useDashboardSummary).mockReturnValue({ data: summary } as never)
  vi.mocked(useCoverage).mockReturnValue({ data: { summary: {}, suites: [] } } as never)
  vi.mocked(useRuns).mockReturnValue({ data: { items: [] }, isLoading: false } as never)
  const { useTimeWindowStore } = await import('@/store/timeWindowStore')
  useTimeWindowStore.setState({ days: 14 })
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter initialEntries={['/trends']}>
        <Routes><Route path="/trends" element={<TrendsPage />} /></Routes>
      </MemoryRouter>
    </SWRConfig>,
  )
  await screen.findByRole('heading', { name: 'Trends' })
}

beforeEach(() => {
  frames.timeSeries.length = 0
})

describe('TrendsPage — the GO line comes from the policy (W11 GATE-04)', () => {
  it('a project policy (97 / 92) draws its GO line, shades below 92 and names the policy in the takeaway', async () => {
    await renderTrends({ avg_pass_rate_7d: { value: 88, trend: null }, readiness_policy: PAYMENTS_POLICY })
    const { rateTarget, takeaway } = lastTimeSeries()
    expect(rateTarget?.value).toBe(97)
    expect(rateTarget?.label).toBe('GO ≥ 97%')
    expect(rateTarget?.floor).toEqual({ value: 92, label: 'No-Go < 92%' })
    expect(takeaway).toContain('the GO line (97%, Payments gate v4)')
    expect(takeaway).toBe('88.0% over the last 14 days, 9 pp below the GO line (97%, Payments gate v4)')
    // The legend carries the two short labels only (the chart's own test draws
    // them); the policy's name is never in a legend label.
    expect(`${rateTarget?.label} ${rateTarget?.floor?.label}`).not.toContain('Payments gate')
  })

  it('no readiness_policy reads the default bands, 95 / 90, labelled', async () => {
    await renderTrends({ avg_pass_rate_7d: { value: 88, trend: null } })
    const { rateTarget, takeaway } = lastTimeSeries()
    expect(rateTarget).toEqual({ value: 95, label: 'GO ≥ 95%', floor: { value: 90, label: 'No-Go < 90%' } })
    expect(takeaway).toBe('88.0% over the last 14 days, 7 pp below the GO line (95%, default bands)')
  })

  it('a null readiness_policy (no evidence) is the default bands too', async () => {
    await renderTrends({ readiness_policy: null })
    expect(lastTimeSeries().rateTarget?.value).toBe(95)
    expect(lastTimeSeries().takeaway).toContain('(95%, default bands)')
  })
})
