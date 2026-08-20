import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * Regression guard: the user list must not filter a truncated page, and must
 * not render a capped list as if it were complete.
 *
 * The defect
 * ----------
 * `useUsers()` was called with no arguments, so it fetched exactly one page —
 * the API defaults to `page_size=50`. The role and status selectors then
 * filtered *that page* in JavaScript. On the measured deployment (132 users,
 * ordered by full_name):
 *
 *     role         DB truth   shown by the UI
 *     QA_LEAD          121         44
 *     QA_ENGINEER        5          2     <-- confidently wrong
 *     VIEWER             5          3     <-- confidently wrong
 *     ADMIN              1          1
 *
 * Filtering a truncated page answers a different question than the operator
 * asked, and nothing on screen said the list was partial. A silent cap is
 * its own defect.
 *
 * The fix has two halves and this guard pins both: filters go to the server,
 * and hitting the page cap renders a visible notice.
 */

const useUsersMock = vi.fn()

vi.mock('@/hooks/useUserManagement', () => ({
  useUsers: (params?: unknown) => useUsersMock(params),
  useApiKeys: () => ({ data: [], isLoading: false }),
  refreshUsers: vi.fn(),
  refreshApiKeys: vi.fn(),
}))
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ isAdmin: true, canManageUsers: true }),
}))
vi.mock('@/services/projectsService', () => ({
  projectsService: { listProjects: vi.fn().mockResolvedValue([]) },
}))
vi.mock('@/services/userManagementService', () => ({
  userManagementService: {
    listUsers: vi.fn(),
    updateUserRole: vi.fn(),
    updateUserStatus: vi.fn(),
  },
}))
vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}))
vi.mock('./ProjectMembersTab', () => ({ ProjectMembersTab: () => null }))

import UserManagementPage from './UserManagementPage'

function userRows(n: number) {
  return Array.from({ length: n }, (_, i) => ({
    id: `u${i}`,
    email: `u${i}@example.com`,
    username: `u${i}`,
    full_name: `User ${i}`,
    role: 'QA_ENGINEER',
    is_active: true,
    created_at: '2026-01-01T00:00:00Z',
  }))
}

describe('UserManagementPage list completeness', () => {
  beforeEach(() => {
    useUsersMock.mockReset()
  })

  it('asks the server for a page size rather than taking the default', () => {
    useUsersMock.mockReturnValue({ data: userRows(3), isLoading: false })
    render(<UserManagementPage />)

    const params = useUsersMock.mock.calls[0][0]
    expect(params).toBeDefined()
    expect(params.page_size).toBeGreaterThan(50)
  })

  it('renders a truncation notice when the page cap is hit', () => {
    useUsersMock.mockReturnValue({ data: userRows(200), isLoading: false })
    render(<UserManagementPage />)

    expect(screen.getByRole('status').textContent).toMatch(/Showing the first 200 users/i)
  })

  it('renders no notice when the list fits', () => {
    useUsersMock.mockReturnValue({ data: userRows(12), isLoading: false })
    render(<UserManagementPage />)

    expect(screen.queryByRole('status')).toBeNull()
  })

  it('sends the chosen role to the server and does not re-filter locally', () => {
    // This has to CHOOSE a filter. With the selectors left at their defaults
    // a client-side filter is a no-op, so an assertion made in the default
    // state survives reverting the fix -- verified: that exact mutation did
    // not kill this guard until the fireEvent below was added.
    const rows = userRows(4)
    rows[0].role = 'ADMIN'
    rows[1].is_active = false
    useUsersMock.mockReturnValue({ data: rows, isLoading: false })
    render(<UserManagementPage />)

    const roleSelect = screen.getAllByRole('combobox')[0]
    fireEvent.change(roleSelect, { target: { value: 'ADMIN' } })

    // Half one: the filter reached the server.
    const params = useUsersMock.mock.calls[useUsersMock.mock.calls.length - 1][0]
    expect(params.role).toBe('ADMIN')

    // Half two: nothing is filtered again on the client. The server was
    // asked for ADMINs; whatever it returned is what the operator sees, so
    // the non-ADMIN rows in this fixture must all still render.
    for (const r of rows) {
      expect(screen.getByText(r.email)).toBeTruthy()
    }
  })
})
