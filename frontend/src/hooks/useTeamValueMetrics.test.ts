/**
 * W08 TRND-08: `useTeamValueMetrics` asks only when enabled AND one project
 * is pinned, and every answer goes through the guard (a malformed answer is
 * `malformed`, never a crash).
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getByTeam = vi.hoisted(() => vi.fn())
vi.mock('@/services/teamValueMetricsService', () => ({ teamValueMetricsService: { getByTeam } }))

import { ALL_PROJECTS_ID } from '@/store/projectStore'
import { useTeamValueMetrics } from './useTeamValueMetrics'

const wrapper = ({ children }: { children: ReactNode }) =>
  createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false } }, children)

const VALID = {
  window_days: 30,
  measured: true,
  reason: null,
  rules_configured: true,
  teams: [],
  not_attributable_hours: 0,
  headline_hours: 0,
}

describe('useTeamValueMetrics', () => {
  beforeEach(() => {
    getByTeam.mockReset()
  })

  it('asks nothing while disabled (the Overview view)', async () => {
    getByTeam.mockResolvedValue(VALID)
    const { result } = renderHook(() => useTeamValueMetrics('proj-1', 30, false), { wrapper })
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(getByTeam).not.toHaveBeenCalled()
    expect(result.current.teams).toBeNull()
    expect(result.current.malformed).toBe(false)
  })

  it('asks nothing in All Projects or with no project', async () => {
    getByTeam.mockResolvedValue(VALID)
    renderHook(() => useTeamValueMetrics(ALL_PROJECTS_ID, 30, true), { wrapper })
    renderHook(() => useTeamValueMetrics(null, 30, true), { wrapper })
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(getByTeam).not.toHaveBeenCalled()
  })

  it('asks for the pinned project and window, and parses the answer', async () => {
    getByTeam.mockResolvedValue(VALID)
    const { result } = renderHook(() => useTeamValueMetrics('proj-1', 90, true), { wrapper })
    await waitFor(() => expect(result.current.teams).not.toBeNull())
    expect(getByTeam).toHaveBeenCalledWith('proj-1', 90)
    expect(result.current.malformed).toBe(false)
  })

  it('a malformed answer is `malformed`, not a crash', async () => {
    getByTeam.mockResolvedValue({ teams: 'nope' })
    const { result } = renderHook(() => useTeamValueMetrics('proj-1', 30, true), { wrapper })
    await waitFor(() => expect(result.current.malformed).toBe(true))
    expect(result.current.teams).toBeNull()
  })

  it('a failed read is `malformed` too (the view says "not available")', async () => {
    getByTeam.mockRejectedValue(new Error('500'))
    const { result } = renderHook(() => useTeamValueMetrics('proj-1', 30, true), { wrapper })
    await waitFor(() => expect(result.current.malformed).toBe(true))
  })
})
