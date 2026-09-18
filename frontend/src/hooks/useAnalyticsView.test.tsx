import { createElement, type ReactNode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const controls = vi.hoisted(() => ({ projectId: 'project-a' }))
const http = vi.hoisted(() => ({
  getData: vi.fn(),
  postData: vi.fn(),
  patchData: vi.fn(),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__all_projects__',
  useProjectStore: (selector: (state: { activeProjectId: string }) => unknown) => selector({
    activeProjectId: controls.projectId,
  }),
}))
vi.mock('@/store/authStore', () => ({
  useAuthStore: (selector: (state: { user: { id: string } }) => unknown) => selector({
    user: { id: 'user-a' },
  }),
}))
vi.mock('@/services/http', () => http)

import {
  getPageWidgets,
  MAX_INSTANCES_PER_PAGE,
} from '@/components/analytics/widgetRegistry'
import { useAnalyticsView } from './useAnalyticsView'

function wrapper({ children }: { children: ReactNode }) {
  return createElement(
    SWRConfig,
    { value: { provider: () => new Map(), dedupingInterval: 0 } },
    children,
  )
}

const savedView = (id: string, instances: unknown) => ({
  id,
  user_id: 'user-a',
  project_id: controls.projectId,
  page: 'dashboard',
  filters: { page: 'dashboard', instances },
})

describe('useAnalyticsView persistence authority', () => {
  beforeEach(() => {
    controls.projectId = 'project-a'
    vi.clearAllMocks()
    http.postData.mockResolvedValue({ id: 'new-view' })
    http.patchData.mockResolvedValue({})
  })

  it('offers only widget IDs that the analytics pages actually render', () => {
    expect(getPageWidgets('dashboard').map(item => item.id)).toEqual([
      'total_executions_kpi', 'avg_pass_rate_kpi', 'active_defects_kpi',
      'flaky_tests_kpi', 'new_failures_kpi', 'infra_failures_kpi', 'avg_duration_kpi',
    ])
    expect(getPageWidgets('trends').map(item => item.id)).toEqual([
      'daily_breakdown', 'trends_kpis', 'pass_rate_trend',
    ])
    expect(getPageWidgets('coverage').map(item => item.id)).toEqual([
      'pass_rate_by_suite', 'coverage_kpis',
    ])
    expect(getPageWidgets('defects').map(item => item.id)).toEqual([
      'defect_kpis', 'defect_category_bar',
    ])
    expect(getPageWidgets('failures').map(item => item.id)).toEqual([
      'failure_category_pie', 'top_failing_bar', 'flaky_leaderboard_table', 'failures_kpis',
    ])
  })

  it('requests one page and repairs a stale or malformed stored layout', async () => {
    const entries = Array.from({ length: 14 }, (_, index) => ({
      instanceId: index < 2 ? 'duplicate-id' : `instance-${index}`,
      templateId: index === 2 ? 'removed-widget' : 'total_executions_kpi',
      chartType: 'pie',
    }))
    http.getData.mockResolvedValue([savedView('view-a', entries)])

    const { result } = renderHook(() => useAnalyticsView('dashboard'), { wrapper })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(http.getData).toHaveBeenCalledWith('/api/v1/saved-views', {
      params: { project_id: 'project-a', page: 'dashboard' },
    })
    expect(result.current.instances.length).toBeLessThanOrEqual(MAX_INSTANCES_PER_PAGE)
    expect(result.current.widgetIds).not.toContain('removed-widget')
    expect(new Set(result.current.instances.map(item => item.instanceId)).size)
      .toBe(result.current.instances.length)
    expect(result.current.instances.every(item => item.chartType === undefined)).toBe(true)
  })

  it('persists the newly selected widgets instead of the previous render', async () => {
    http.getData.mockResolvedValue([
      savedView('view-a', [{ instanceId: 'old', templateId: 'total_executions_kpi' }]),
    ])
    const { result } = renderHook(() => useAnalyticsView('dashboard'), { wrapper })
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.widgetIds).toEqual(['total_executions_kpi'])

    await act(async () => {
      await result.current.setWidgets(['avg_pass_rate_kpi'])
    })

    expect(http.patchData).toHaveBeenCalledTimes(1)
    expect(http.patchData).toHaveBeenCalledWith(
      '/api/v1/saved-views/view-a',
      expect.objectContaining({
        filters: expect.objectContaining({
          instances: [expect.objectContaining({ templateId: 'avg_pass_rate_kpi' })],
        }),
      }),
    )
  })

  it('uses a shared layout without trying to patch the other user view', async () => {
    http.getData.mockResolvedValue([
      {
        ...savedView('shared-view', [{ instanceId: 'shared', templateId: 'total_executions_kpi' }]),
        user_id: 'user-b',
      },
      savedView('owned-view', [{ instanceId: 'owned', templateId: 'avg_pass_rate_kpi' }]),
    ])
    const { result } = renderHook(() => useAnalyticsView('dashboard'), { wrapper })
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.widgetIds).toEqual(['avg_pass_rate_kpi'])

    await act(async () => {
      await result.current.setWidgets(['total_executions_kpi'])
    })

    expect(http.patchData).toHaveBeenCalledWith(
      '/api/v1/saved-views/owned-view',
      expect.any(Object),
    )
    expect(http.patchData).not.toHaveBeenCalledWith(
      '/api/v1/saved-views/shared-view',
      expect.any(Object),
    )
  })

  it('hydrates after a failed request is retried successfully', async () => {
    http.getData
      .mockRejectedValueOnce(new Error('temporary outage'))
      .mockResolvedValueOnce([
        savedView('view-a', [{ instanceId: 'restored', templateId: 'avg_pass_rate_kpi' }]),
      ])
    const { result } = renderHook(() => useAnalyticsView('dashboard'), { wrapper })
    await waitFor(() => expect(result.current.error).toBeTruthy())
    expect(http.postData).not.toHaveBeenCalled()

    await act(async () => {
      await result.current.retry()
    })

    await waitFor(() => expect(result.current.widgetIds).toEqual(['avg_pass_rate_kpi']))
  })

  it('does not create a duplicate view while the initial layout is loading', async () => {
    let resolveViews: (value: unknown[]) => void = () => undefined
    http.getData.mockReturnValue(new Promise(resolve => { resolveViews = resolve }))
    const { result } = renderHook(() => useAnalyticsView('dashboard'), { wrapper })

    await act(async () => {
      await result.current.setWidgets(['total_executions_kpi'])
    })
    expect(http.postData).not.toHaveBeenCalled()

    await act(async () => resolveViews([]))
    await waitFor(() => expect(result.current.loading).toBe(false))
  })

  it('never patches the previous project view after a project switch', async () => {
    http.getData.mockImplementation((_path: string, options: { params: { project_id: string } }) => (
      options.params.project_id === 'project-a'
        ? Promise.resolve([
            savedView('view-a', [{ instanceId: 'old', templateId: 'total_executions_kpi' }]),
          ])
        : Promise.resolve([])
    ))
    const { result, rerender } = renderHook(() => useAnalyticsView('dashboard'), { wrapper })
    await waitFor(() => expect(result.current.loading).toBe(false))

    vi.clearAllMocks()
    http.postData.mockResolvedValue({ id: 'view-b' })
    controls.projectId = 'project-b'
    rerender()
    await waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => {
      await result.current.setWidgets(['total_executions_kpi'])
    })

    expect(http.patchData).not.toHaveBeenCalled()
    expect(http.postData).toHaveBeenCalledWith(
      '/api/v1/saved-views',
      expect.objectContaining({ project_id: 'project-b' }),
    )
  })
})
