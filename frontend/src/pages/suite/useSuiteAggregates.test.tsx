/**
 * The suites list's run columns read (UX redesign P4): one request for the
 * active project, joined by trimmed suite name — and NONE when disabled (the
 * All-Projects view, where a name could join the wrong project's suite).
 */
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useProjectStore } from '@/store/projectStore'
import { useSuiteAggregates } from './useSuiteAggregates'

const listSuites = vi.fn()
vi.mock('@/services/testManagementService', () => ({
  testManagementService: { listSuites: (...args: unknown[]) => listSuites(...args) },
}))

function wrapper({ children }: { children: ReactNode }) {
  return <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{children}</SWRConfig>
}

describe('useSuiteAggregates', () => {
  beforeEach(() => {
    listSuites.mockReset()
    listSuites.mockResolvedValue([
      { suite_name: ' Auth ', pass_rate: 75, failed_count: 1, total_executions: 80, last_run_at: null, last_run_id: null },
      { suite_name: 'Checkout', pass_rate: 100, failed_count: 0, total_executions: 10, last_run_at: null, last_run_id: null },
    ])
    act(() => useProjectStore.setState({ activeProjectId: 'p1' }))
  })

  it('asks for the active project, and keys the rows by trimmed suite name', async () => {
    const { result } = renderHook(() => useSuiteAggregates(true), { wrapper })
    await waitFor(() => expect(result.current.loaded).toBe(true))
    expect(listSuites).toHaveBeenCalledTimes(1)
    expect(listSuites).toHaveBeenCalledWith('p1')
    expect(result.current.byName.get('Auth')?.pass_rate).toBe(75)
    expect(result.current.byName.get('Checkout')?.total_executions).toBe(10)
    expect(result.current.byName.size).toBe(2)
  })

  it('disabled: no request, nothing joined', async () => {
    const { result } = renderHook(() => useSuiteAggregates(false), { wrapper })
    // Give a request the chance to go out.
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(listSuites).not.toHaveBeenCalled()
    expect(result.current.loaded).toBe(false)
    expect(result.current.byName.size).toBe(0)
  })

  it('a failed read is reported, not an empty list', async () => {
    listSuites.mockRejectedValue(new Error('boom'))
    const { result } = renderHook(() => useSuiteAggregates(true), { wrapper })
    await waitFor(() => expect(result.current.error).toBeTruthy())
    expect(result.current.loaded).toBe(false)
  })
})
