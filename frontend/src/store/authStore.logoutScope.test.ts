/**
 * Security M1 (E3 review): the release filter is persisted
 * (`tl.release-filter`) and outlived a logout — the next person to sign in on
 * the machine inherited the previous user's scope (and learned which release
 * they were looking at). Logout clears the store and its saved entry, and the
 * suite filter's entry an older (multi-select) build may have left.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }))

import { useAuthStore } from './authStore'
import { useReleaseStore } from './releaseStore'

const PROJECT = 'aaaaaaaa-0000-4000-8000-000000000001'
const R1 = '11111111-0000-4000-8000-000000000001'

beforeEach(() => {
  localStorage.clear()
  useAuthStore.setState({ token: 't', refreshToken: 'r', isAuthenticated: true })
  useReleaseStore.getState().setActiveRelease(R1, PROJECT)
  localStorage.setItem('tl.suite-filter', JSON.stringify({ state: { activeSuiteNames: ['payments'] }, version: 0 }))
})

describe('logout clears the saved report scope', () => {
  it('empties the release store and its saved entry, and a stale suite entry', () => {
    expect(localStorage.getItem('tl.release-filter')).toContain(R1)

    useAuthStore.getState().logout()

    expect(useReleaseStore.getState().activeReleaseId).toBeNull()
    expect(useReleaseStore.getState().scopedProjectId).toBeNull()
    const savedRelease = localStorage.getItem('tl.release-filter')
    expect(savedRelease === null || !savedRelease.includes(R1)).toBe(true)
    expect(localStorage.getItem('tl.suite-filter')).toBeNull()
  })
})
