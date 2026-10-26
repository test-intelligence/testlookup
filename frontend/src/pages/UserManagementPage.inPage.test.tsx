/**
 * W27b PAN-06: /users renders its forms IN THE PAGE (`docs/deferred-scope-2026-10-13/work/W27b-contract.md` §2.3).
 *
 *  - Add User and Invite User open right after the Users tab toolbar, one at a time (one slot): a clean form
 *    is replaced at once, a typed one asks first (`Discard your changes to "…"?`);
 *  - a row's Edit user opens in a full-width row right under that row (`td[colspan="4"]`), one per table;
 *  - nothing is a modal: no `role="dialog"`, no `[aria-modal]`;
 *  - a users re-read (a filter change) never unmounts an open toolbar form; the row form closes on a filter
 *    change, after the question when it has typed input.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { UserItem } from '@/services/userManagementService'

const users = vi.hoisted(() => ({
  state: { data: [] as unknown[] | undefined, isLoading: false },
  calls: [] as unknown[],
}))

vi.mock('@/hooks/useUserManagement', () => ({
  // ADM-03: the page reads a server page ({items, total}); the fixtures stay plain lists.
  useUsersPage: (params: unknown) => {
    users.calls.push(params)
    const rows = users.state.data
    return { ...users.state, data: rows ? { items: rows, total: rows.length } : undefined }
  },
  refreshUsers: vi.fn(),
}))
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ isAdmin: true, canManageUsers: true }),
}))
vi.mock('@/services/projectsService', () => ({
  projectsService: { list: vi.fn().mockResolvedValue([]) },
}))
vi.mock('@/services/userManagementService', () => ({
  userManagementService: {
    updateUserRole: vi.fn(),
    updateUserStatus: vi.fn(),
    createUser: vi.fn(),
    inviteUser: vi.fn(),
    updateUserProfile: vi.fn(),
    getUserMemberships: vi.fn().mockResolvedValue([]),
    addProjectMember: vi.fn(),
    removeProjectMember: vi.fn(),
  },
}))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))
vi.mock('./ProjectMembersTab', () => ({ ProjectMembersTab: () => null }))

import UserManagementPage from './UserManagementPage'

function user(id: string, name: string): UserItem {
  return {
    id,
    email: `${id}@example.test`,
    username: id,
    full_name: name,
    role: 'QA_ENGINEER',
    is_active: true,
    created_at: '2026-01-01T00:00:00Z',
  } as UserItem
}
const PRIYA = user('priya', 'Priya Nair')
const OMAR = user('omar', 'Omar Haddad')

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/users']}>
      <UserManagementPage />
    </MemoryRouter>,
  )
}

/** The toolbar: the row holding the two filter selects and the admin buttons. */
const toolbar = () => (screen.getByRole('combobox', { name: 'Filter by role' }).closest('div') as HTMLElement).parentElement as HTMLElement
const rowOf = (name: string) => screen.getByText(name).closest('tr') as HTMLElement

beforeEach(() => {
  users.state = { data: [PRIYA, OMAR], isLoading: false }
  users.calls.length = 0
})

describe('UserManagementPage — Add User and Invite User in the page', () => {
  it('Add User opens right after the toolbar, as a region; nothing is a modal', () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Add User' }))
    const section = screen.getByRole('region', { name: 'Add User' })
    expect(toolbar().nextElementSibling).toBe(section)
    expect(section.querySelector('#add-user-title')).toHaveTextContent('Add User')
    expect(within(section).getByLabelText('Email address'), 'focus moves to the first field').toHaveFocus()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.querySelector('[aria-modal]')).toBeNull()
  })

  it('Add User and Invite User replace each other (one slot); a typed form asks first', () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Add User' }))
    fireEvent.click(screen.getByRole('button', { name: 'Invite User' }))
    expect(screen.queryByRole('region', { name: 'Add User' }), 'a clean form is replaced at once').toBeNull()
    const invite = screen.getByRole('region', { name: 'Invite User' })
    expect(toolbar().nextElementSibling).toBe(invite)

    fireEvent.change(within(invite).getByLabelText('Email address'), { target: { value: 'new.hire@example.test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add User' }))
    const question = within(invite).getByRole('group')
    expect(question).toHaveTextContent('Discard your changes to "Invite User"?')
    expect(screen.queryByRole('region', { name: 'Add User' })).toBeNull()

    fireEvent.click(within(question).getByRole('button', { name: 'Keep editing' }))
    expect(within(invite).getByLabelText('Email address')).toHaveValue('new.hire@example.test')

    fireEvent.click(screen.getByRole('button', { name: 'Add User' }))
    fireEvent.click(within(within(invite).getByRole('group')).getByRole('button', { name: 'Discard changes' }))
    expect(screen.queryByRole('region', { name: 'Invite User' })).toBeNull()
    expect(screen.getByRole('region', { name: 'Add User' })).toBeInTheDocument()
  })

  it('Cancel closes the form; a users re-read (filter change) never unmounts an open form', () => {
    const view = renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Add User' }))
    fireEvent.change(within(screen.getByRole('region', { name: 'Add User' })).getByLabelText('Username'), { target: { value: 'jdoe' } })

    // The list re-reads (a new filter key with no data yet): the table waits, the form stays with its text.
    fireEvent.change(screen.getByRole('combobox', { name: 'Filter by role' }), { target: { value: 'ADMIN' } })
    users.state = { data: undefined, isLoading: true }
    view.rerender(
      <MemoryRouter initialEntries={['/users']}>
        <UserManagementPage />
      </MemoryRouter>,
    )
    expect(screen.queryByRole('table')).toBeNull()
    expect(within(screen.getByRole('region', { name: 'Add User' })).getByLabelText('Username')).toHaveValue('jdoe')
    expect(users.calls[users.calls.length - 1]).toEqual({ page: 1, page_size: 50, role: 'ADMIN' })

    fireEvent.click(within(screen.getByRole('region', { name: 'Add User' })).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('region', { name: 'Add User' })).toBeNull()
  })

  it('with nothing open, the loading state is today\'s spinner (no toolbar)', () => {
    users.state = { data: undefined, isLoading: true }
    renderPage()
    expect(screen.queryByRole('combobox', { name: 'Filter by role' })).toBeNull()
    expect(screen.queryByRole('table')).toBeNull()
  })
})

describe('UserManagementPage — Edit user under its row', () => {
  it('opens in a full-width row right under its row (td[colspan="4"]), one per table', () => {
    renderPage()
    fireEvent.click(within(rowOf('Priya Nair')).getByTitle('Edit user'))
    const section = screen.getByRole('region', { name: 'Edit User' })
    const cell = section.closest('td') as HTMLElement
    expect(cell).toHaveAttribute('colspan', '4')
    expect(cell.closest('tr')).toHaveAttribute('data-in-page-row')
    expect(rowOf('Priya Nair').nextElementSibling).toBe(cell.closest('tr'))
    expect(within(section).getByLabelText('Full Name')).toHaveValue('Priya Nair')
    expect(document.querySelector('[aria-modal]')).toBeNull()

    // Another row's Edit user replaces a clean form (one per table) and opens under ITS row.
    fireEvent.click(within(rowOf('Omar Haddad')).getByTitle('Edit user'))
    expect(screen.getAllByRole('region', { name: 'Edit User' })).toHaveLength(1)
    expect(rowOf('Omar Haddad').nextElementSibling?.contains(screen.getByRole('region', { name: 'Edit User' }))).toBe(true)
    expect(within(screen.getByRole('region', { name: 'Edit User' })).getByLabelText('Full Name')).toHaveValue('Omar Haddad')

    // The toolbar slot is a different section: Add User opens beside the row form.
    fireEvent.click(screen.getByRole('button', { name: 'Add User' }))
    expect(screen.getByRole('region', { name: 'Add User' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Edit User' })).toBeInTheDocument()
  })

  it('a filter change closes the row form; with typed input it asks first, and Keep editing keeps the filter', () => {
    renderPage()
    fireEvent.click(within(rowOf('Priya Nair')).getByTitle('Edit user'))
    fireEvent.change(within(screen.getByRole('region', { name: 'Edit User' })).getByLabelText('Full Name'), { target: { value: 'Priya N.' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Filter by status' }), { target: { value: 'inactive' } })

    const section = screen.getByRole('region', { name: 'Edit User' })
    const question = within(section).getByRole('group')
    expect(question).toHaveTextContent('Discard your changes to "Edit User"?')
    expect(screen.getByRole('combobox', { name: 'Filter by status' })).toHaveValue('all')
    fireEvent.click(within(question).getByRole('button', { name: 'Keep editing' }))
    expect(within(section).getByLabelText('Full Name')).toHaveValue('Priya N.')
    expect(screen.getByRole('combobox', { name: 'Filter by status' })).toHaveValue('all')

    fireEvent.change(screen.getByRole('combobox', { name: 'Filter by status' }), { target: { value: 'inactive' } })
    fireEvent.click(within(within(section).getByRole('group')).getByRole('button', { name: 'Discard changes' }))
    expect(screen.queryByRole('region', { name: 'Edit User' })).toBeNull()
    expect(screen.getByRole('combobox', { name: 'Filter by status' })).toHaveValue('inactive')
    expect(users.calls[users.calls.length - 1]).toEqual({ page: 1, page_size: 50, is_active: false })
  })
})
