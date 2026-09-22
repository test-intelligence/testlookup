/**
 * Security M1 (E3 review): the release and suite filters are persisted
 * (`tl.release-filter`, `tl.suite-filter`) and outlived a logout — the next
 * person to sign in on the machine inherited the previous user's scope (and
 * learned which releases/suites they were looking at). Logout clears both
 * stores, their saved entries, and the settled data scope.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }))

import { useAuthStore } from './authStore'
import { selectReleaseIds, useReleaseStore } from './releaseStore'
import { useSuiteStore } from './suiteStore'
import { settleScopeNow, useSettledScopeStore } from './settledScope'
import { useMultiFiltersFlagStore } from './multiFiltersFlag'

const PROJECT = 'aaaaaaaa-0000-4000-8000-000000000001'
const R1 = '11111111-0000-4000-8000-000000000001'
const R2 = '22222222-0000-4000-8000-000000000002'

beforeEach(() => {
  localStorage.clear()
  useMultiFiltersFlagStore.setState({ enabled: true, resolved: true })
  useAuthStore.setState({ token: 't', refreshToken: 'r', isAuthenticated: true })
  useReleaseStore.getState().setActiveReleases([R1, R2], PROJECT)
  useSuiteStore.getState().setActiveSuites(['payments', 'cart'], PROJECT)
  settleScopeNow()
})

describe('logout clears the saved report scope', () => {
  it('empties the release and suite stores, their saved entries, and the settled scope', () => {
    expect(localStorage.getItem('tl.release-filter')).toContain(R2)
    expect(localStorage.getItem('tl.suite-filter')).toContain('payments')
    expect(useSettledScopeStore.getState().suiteNames).toEqual(['cart', 'payments'])

    useAuthStore.getState().logout()

    expect(selectReleaseIds(useReleaseStore.getState())).toEqual([])
    expect(useReleaseStore.getState().scopedProjectId).toBeNull()
    expect(useSuiteStore.getState().activeSuiteNames).toEqual([])
    const savedRelease = localStorage.getItem('tl.release-filter')
    const savedSuite = localStorage.getItem('tl.suite-filter')
    expect(savedRelease === null || !savedRelease.includes(R1)).toBe(true)
    expect(savedSuite === null || !savedSuite.includes('payments')).toBe(true)
    // At once, not 250 ms later: no request may go out under the old scope.
    expect(useSettledScopeStore.getState().releaseIds).toEqual([])
    expect(useSettledScopeStore.getState().suiteNames).toEqual([])
  })
})
