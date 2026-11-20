/**
 * The Jira picker route needs QA engineer. A lower role is never asked, so a
 * viewer or tester opening a page that uses the hook sees no refusal.
 */
import type { ReactNode } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { useAuthStore } from '@/store/authStore'

const mockMetadata = vi.fn()
vi.mock('@/services/defectJiraService', () => ({
  defectJiraService: { metadata: (...a: unknown[]) => mockMetadata(...a) },
  clusterEpicService: {},
}))

import { useJiraDefectMetadata } from './useJiraDefects'

const wrapper = ({ children }: { children: ReactNode }) => (
  <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{children}</SWRConfig>
)

function signInAs(role: string) {
  useAuthStore.setState({ user: { id: 'u1', role } as never })
}

describe('useJiraDefectMetadata asks only for roles the route accepts', () => {
  beforeEach(() => {
    mockMetadata.mockReset()
    mockMetadata.mockResolvedValue({ available: true, projects: [] })
  })

  it.each(['VIEWER', 'TESTER'])('does not call the route for a %s', async (role) => {
    signInAs(role)
    const { result } = renderHook(() => useJiraDefectMetadata('p1'), { wrapper })
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(mockMetadata).not.toHaveBeenCalled()
    expect(result.current.metadata).toBeUndefined()
    expect(result.current.isError).toBe(false)
  })

  it.each(['QA_ENGINEER', 'QA_LEAD', 'ADMIN'])('calls the route for a %s', async (role) => {
    signInAs(role)
    const { result } = renderHook(() => useJiraDefectMetadata('p1'), { wrapper })
    await waitFor(() => expect(result.current.metadata).toEqual({ available: true, projects: [] }))
    expect(mockMetadata).toHaveBeenCalledWith('p1')
  })
})
