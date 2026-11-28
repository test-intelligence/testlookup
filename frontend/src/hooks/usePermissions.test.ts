import { describe, expect, it, vi } from 'vitest'

const mockUseAuthStore = vi.hoisted(() => vi.fn())

vi.mock('@/store/authStore', () => ({
  useAuthStore: mockUseAuthStore,
}))

import { usePermissions } from './usePermissions'

describe('usePermissions', () => {
  it('treats legacy UserRole-prefixed roles as their canonical value', () => {
    mockUseAuthStore.mockImplementation((selector: (state: { user: { role: string } }) => unknown) =>
      selector({ user: { role: 'UserRole.ADMIN' } }),
    )

    const permissions = usePermissions()

    expect(permissions.role).toBe('ADMIN')
    expect(permissions.canGenerateApiKeys).toBe(true)
    expect(permissions.canManageUsers).toBe(true)
  })

  it('falls back to viewer permissions when no user is loaded', () => {
    mockUseAuthStore.mockImplementation((selector: (state: { user: null }) => unknown) =>
      selector({ user: null }),
    )

    const permissions = usePermissions()

    expect(permissions.role).toBe('VIEWER')
    expect(permissions.canGenerateApiKeys).toBe(false)
    expect(permissions.canManageUsers).toBe(false)
  })

  it('normalizes QA engineer and keeps QA lead retirement authority separate', () => {
    mockUseAuthStore.mockImplementation((selector: (state: { user: { role: string } }) => unknown) =>
      selector({ user: { role: 'UserRole.QA_ENGINEER' } }),
    )

    const engineer = usePermissions()
    expect(engineer.role).toBe('QA_ENGINEER')
    expect(engineer.isQaEngineer).toBe(true)
    expect(engineer.isQaLead).toBe(false)

    mockUseAuthStore.mockImplementation((selector: (state: { user: { role: string } }) => unknown) =>
      selector({ user: { role: 'QA_LEAD' } }),
    )
    const lead = usePermissions()
    expect(lead.isQaEngineer).toBe(true)
    expect(lead.isQaLead).toBe(true)
  })
})

/**
 * Enterprise roles E00: a job role ranks as VIEWER on every server guard that names no permission, and this
 * shell hook answers by rank, so it reads one as VIEWER. (It stays as small as it was: the eager bundle has a
 * few bytes of headroom. A page that needs the job role's name uses `normalizeRole` from `@/lib/roles`; per-
 * permission answers come from `useMyPermissions().can(...)`.)
 */
describe('usePermissions — job roles (enterprise roles E00)', () => {
  it.each(['RELEASE_MANAGER', 'PROJECT_MANAGER', 'PRODUCT_MANAGER', 'DEVELOPER', 'AGENT'])(
    'gives %s exactly what a VIEWER gets',
    (role) => {
      mockUseAuthStore.mockImplementation((selector: (state: { user: { role: string } }) => unknown) =>
        selector({ user: { role } }),
      )
      const permissions = usePermissions()
      expect(permissions.role).toBe('VIEWER')
      expect(permissions.hasRole('VIEWER')).toBe(true)
      expect(permissions.hasRole('TESTER')).toBe(false)
      expect(permissions.isQaEngineer).toBe(false)
      expect(permissions.canGenerateApiKeys).toBe(false)
      expect(permissions.canManageUsers).toBe(false)
    },
  )

  it('still reads an unknown role as VIEWER', () => {
    mockUseAuthStore.mockImplementation((selector: (state: { user: { role: string } }) => unknown) =>
      selector({ user: { role: 'SUPERUSER' } }),
    )
    expect(usePermissions().role).toBe('VIEWER')
  })
})
