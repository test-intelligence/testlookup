import { describe, expect, it, vi } from 'vitest'

/**
 * Regression guard: `refreshUsers()` must invalidate every users key.
 *
 * `useUsers` keys on the bare string when called with no arguments and on
 * `['/api/v1/users', params]` once filters are passed. `refreshUsers` mutated
 * only the string, so the moment the page started sending filters to the
 * server, a role or status change would appear to succeed while the row kept
 * its old value until SWR revalidated on its own.
 *
 * This is the trap that comes free with moving a filter server-side: the SWR
 * key stops being a constant, and every hand-written `mutate(literal)` quietly
 * stops matching it.
 *
 * 2026-09-19: the matcher was right and `refreshUsers()` STILL did nothing in
 * the running app. It called the `mutate` exported by the `swr` module, which
 * is bound to SWR's default cache, while `main.tsx` supplies
 * `provider: () => new Map()` — so it iterated an empty cache and matched
 * nothing. This test could not see that, because mocking `swr` wholesale
 * removes the very thing that was wrong: WHICH cache the mutate belongs to.
 * The seam is now `utils/swrCacheMutate`, whose `appMutate` delegates to the
 * provider-bound mutate published by `SwrMutateBridge`. The behavioural proof
 * lives in `useFeatureFlags.invalidation.test.tsx`, which renders under a real
 * provider instead of mocking it away.
 */

// vi.mock is hoisted above const declarations -- vi.hoisted keeps the spy
// available to the factory.
const { mutate } = vi.hoisted(() => ({ mutate: vi.fn() }))

vi.mock('swr', () => ({ default: vi.fn() }))
vi.mock('@/utils/swrCacheMutate', () => ({ appMutate: mutate }))
vi.mock('@/services/userManagementService', () => ({
  userManagementService: {
    listUsers: vi.fn(),
    listApiKeys: vi.fn(),
    listProjectMembers: vi.fn(),
  },
}))

import { refreshUsers } from '@/hooks/useUserManagement'

describe('refreshUsers key matching', () => {
  it('matches both the bare string and the filtered array key', () => {
    mutate.mockClear()
    refreshUsers()

    const matcher = mutate.mock.calls[0][0]
    expect(typeof matcher).toBe('function')
    expect(matcher('/api/v1/users')).toBe(true)
    expect(matcher(['/api/v1/users', { role: 'ADMIN' }])).toBe(true)
    expect(matcher(['/api/v1/users', { page_size: 200, is_active: false }])).toBe(true)
  })

  it('does not invalidate unrelated keys', () => {
    mutate.mockClear()
    refreshUsers()

    const matcher = mutate.mock.calls[0][0]
    expect(matcher('/api/v1/keys')).toBe(false)
    expect(matcher(['/api/v1/projects/p1/members'])).toBe(false)
    expect(matcher(null)).toBe(false)
  })
})
