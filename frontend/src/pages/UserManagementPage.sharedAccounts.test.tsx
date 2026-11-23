/**
 * D-79: archiving a project turns its shared "<Project> QA Lead" login off,
 * and the Users list showed it as one more inactive person with an Activate
 * button. The row now says it is a shared project account, explains why it is
 * off and how it comes back (the project's password reset after a restore,
 * S-D-3), and offers no Activate switch that would bypass that.
 */
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

const rows = vi.hoisted(() => ({ items: [] as unknown[] }))

vi.mock('@/hooks/useUserManagement', () => ({
  useUsersPage: () => ({ data: { items: rows.items, total: rows.items.length }, isLoading: false }),
  refreshUsers: vi.fn(),
}))
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ isAdmin: true, canManageUsers: true }),
}))
vi.mock('@/services/projectsService', () => ({
  projectsService: { list: vi.fn().mockResolvedValue([]) },
}))
vi.mock('@/services/userManagementService', () => ({
  userManagementService: { updateUserRole: vi.fn(), updateUserStatus: vi.fn() },
}))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))
vi.mock('./ProjectMembersTab', () => ({ ProjectMembersTab: () => null }))

import UserManagementPage from './UserManagementPage'

const base = { role: 'QA_LEAD', created_at: '2026-01-01T00:00:00Z' }

function rowOf(userId: string) {
  return document.querySelector(`[data-user-row="${userId}"]`) as HTMLElement
}

describe('UserManagementPage: shared project accounts (D-79)', () => {
  it('marks an archived project QA lead as a shared account, explains it, and offers no Activate', () => {
    rows.items = [
      { ...base, id: 'qa', email: 'qalead-staging@x.test', username: 'qalead-staging', full_name: 'Staging QA Lead', is_active: false, is_synthetic: true },
      { ...base, id: 'p', email: 'pat@x.test', username: 'pat', full_name: 'Pat Person', is_active: false },
    ]
    render(<MemoryRouter initialEntries={['/users']}><UserManagementPage /></MemoryRouter>)

    const shared = rowOf('qa')
    expect(within(shared).getByText('Shared project account')).toBeInTheDocument()
    expect(within(shared).getByText('Inactive')).toHaveAttribute('title', expect.stringContaining('An admin turns it back on'))
    expect(within(shared).queryByRole('button', { name: 'Activate' })).toBeNull()

    // A person who is inactive keeps the switch and carries no shared-account note.
    const person = rowOf('p')
    expect(within(person).queryByText('Shared project account')).toBeNull()
    expect(within(person).getByRole('button', { name: 'Activate' })).toBeInTheDocument()
  })

  it('an active shared account is still marked and can still be turned off', () => {
    rows.items = [
      { ...base, id: 'qa', email: 'qalead-web@x.test', username: 'qalead-web', full_name: 'Web QA Lead', is_active: true, is_synthetic: true },
    ]
    render(<MemoryRouter initialEntries={['/users']}><UserManagementPage /></MemoryRouter>)
    const shared = rowOf('qa')
    expect(within(shared).getByText('Shared project account')).toBeInTheDocument()
    expect(within(shared).getByRole('button', { name: 'Deactivate' })).toBeInTheDocument()
    expect(screen.queryByTitle(/An admin turns it back on/)).toBeNull()
  })
})
