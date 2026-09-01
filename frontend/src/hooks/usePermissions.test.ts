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
