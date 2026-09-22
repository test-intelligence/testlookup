/**
 * Security M1, the other half: logout clears the saved release/suite filters
 * even when their stores were never loaded in this page. The scope stores are
 * no longer imported by the eager `authStore` (store/logoutReset.ts), so a
 * session that never opened a scope-aware view has them only in
 * `localStorage` — and the next person to sign in must not inherit them.
 *
 * This file deliberately imports nothing but `authStore`.
 */
import { expect, it, vi } from 'vitest'

vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }))

import { useAuthStore } from './authStore'

it('removes both saved filter entries on logout with the scope stores unloaded', () => {
  localStorage.setItem('tl.release-filter', JSON.stringify({ state: { activeReleaseIds: ['r1'] }, version: 0 }))
  localStorage.setItem('tl.suite-filter', JSON.stringify({ state: { activeSuiteNames: ['payments'] }, version: 0 }))
  useAuthStore.setState({ token: 't', refreshToken: 'r', isAuthenticated: true })

  useAuthStore.getState().logout()

  expect(localStorage.getItem('tl.release-filter')).toBeNull()
  expect(localStorage.getItem('tl.suite-filter')).toBeNull()
  expect(useAuthStore.getState().isAuthenticated).toBe(false)
})
