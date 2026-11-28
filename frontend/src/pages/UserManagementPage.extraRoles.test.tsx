/**
 * Extra job roles on Settings › Users (enterprise roles E00-S05, D-16).
 *
 * The role pickers offer the five ranks and the four job roles (never AGENT); the table shows a person's extra
 * roles under their role; Edit, Add and Invite send `additional_roles` only when some are chosen or changed,
 * and the primary role is never repeated as an extra one.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { UserItem } from '@/services/userManagementService'

const users = vi.hoisted(() => ({ items: [] as unknown[] }))
vi.mock('@/hooks/useUserManagement', () => ({
  useUsersPage: () => ({ data: { items: users.items, total: users.items.length }, isLoading: false }),
  refreshUsers: vi.fn(),
  refreshInvitations: vi.fn(),
}))
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ isAdmin: true, canManageUsers: true }),
}))
vi.mock('@/services/projectsService', () => ({
  projectsService: { list: vi.fn().mockResolvedValue([{ id: 'p1', name: 'Payments' }]) },
}))
const svc = vi.hoisted(() => ({
  createUser: vi.fn(),
  inviteUser: vi.fn(),
  updateUserProfile: vi.fn(),
}))
vi.mock('@/services/userManagementService', () => ({
  userManagementService: {
    updateUserRole: vi.fn(),
    updateUserStatus: vi.fn(),
    createUser: (...a: unknown[]) => svc.createUser(...a),
    inviteUser: (...a: unknown[]) => svc.inviteUser(...a),
    updateUserProfile: (...a: unknown[]) => svc.updateUserProfile(...a),
    getUserMemberships: vi.fn().mockResolvedValue([]),
    addProjectMember: vi.fn(),
    removeProjectMember: vi.fn(),
  },
}))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))
vi.mock('./ProjectMembersTab', () => ({ ProjectMembersTab: () => null }))

import UserManagementPage from './UserManagementPage'

const PRIYA = {
  id: 'priya',
  email: 'priya@testlookup.example.com',
  username: 'priya',
  full_name: 'Priya Nair',
  role: 'QA_ENGINEER',
  additional_roles: ['RELEASE_MANAGER'],
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
} as UserItem

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/users']}>
      <UserManagementPage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  users.items = [PRIYA]
  svc.updateUserProfile.mockResolvedValue(PRIYA)
  svc.createUser.mockResolvedValue({ id: 'n', username: 'newbie', temp_password: 'tmp-1' })
  svc.inviteUser.mockResolvedValue({ id: 'i', email: 'x@example.com', role: 'QA_ENGINEER', expires_at: '', invitation_link: 'l' })
})

describe('Settings › Users — extra job roles', () => {
  it('offers the ranks and the job roles, never AGENT, and shows extra roles in the table', () => {
    renderPage()
    const row = screen.getByText('Priya Nair').closest('tr') as HTMLElement
    const select = within(row).getByLabelText('Role for priya') as HTMLSelectElement
    const values = Array.from(select.options).map((o) => o.value)
    expect(values).toEqual([
      'VIEWER', 'TESTER', 'QA_ENGINEER', 'QA_LEAD', 'ADMIN',
      'RELEASE_MANAGER', 'PROJECT_MANAGER', 'PRODUCT_MANAGER', 'DEVELOPER',
    ])
    expect(within(row).getByText('+ Release Manager')).toBeInTheDocument()
  })

  it('Edit sends the changed extra roles, in catalogue order, and nothing when unchanged', async () => {
    renderPage()
    fireEvent.click(within(screen.getByText('Priya Nair').closest('tr') as HTMLElement).getByTitle('Edit user'))
    const form = screen.getByRole('region', { name: 'Edit User' })
    expect(within(form).getByLabelText('Release Manager')).toBeChecked()
    // The primary role is not offered as an extra one.
    expect(within(form).queryByLabelText('QA Engineer')).toBeNull()

    fireEvent.click(within(form).getByLabelText('Developer'))
    fireEvent.click(within(form).getByRole('button', { name: 'Save Changes' }))
    await waitFor(() => expect(svc.updateUserProfile).toHaveBeenCalledWith('priya', {
      additional_roles: ['RELEASE_MANAGER', 'DEVELOPER'],
    }))
  })

  it('Edit can clear the extra roles', async () => {
    renderPage()
    fireEvent.click(within(screen.getByText('Priya Nair').closest('tr') as HTMLElement).getByTitle('Edit user'))
    const form = screen.getByRole('region', { name: 'Edit User' })
    fireEvent.click(within(form).getByLabelText('Release Manager'))
    fireEvent.click(within(form).getByRole('button', { name: 'Save Changes' }))
    await waitFor(() => expect(svc.updateUserProfile).toHaveBeenCalledWith('priya', { additional_roles: [] }))
  })

  it('Add User sends extra roles only when some are chosen', async () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Add User' }))
    const form = screen.getByRole('region', { name: 'Add User' })
    fireEvent.change(within(form).getByLabelText('Email address'), { target: { value: 'new@example.com' } })
    fireEvent.change(within(form).getByLabelText('Username'), { target: { value: 'newbie' } })
    fireEvent.click(within(form).getByLabelText('Product Manager'))
    fireEvent.click(within(form).getByRole('button', { name: 'Create User' }))
    await waitFor(() => expect(svc.createUser).toHaveBeenCalledWith(
      'new@example.com', 'newbie', 'QA_ENGINEER', undefined, ['PRODUCT_MANAGER'],
    ))
  })

  it('Invite gives the extra roles globally and in each chosen project', async () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Invite User' }))
    const form = screen.getByRole('region', { name: 'Invite User' })
    fireEvent.change(within(form).getByLabelText('Email address'), { target: { value: 'x@example.com' } })
    fireEvent.click(within(form).getByLabelText('Release Manager'))
    fireEvent.click(await within(form).findByLabelText('Payments'))
    expect(within(form).getByText(/They join these projects as QA Engineer \+ Release Manager/)).toBeInTheDocument()
    fireEvent.click(within(form).getByRole('button', { name: /Send Invitation|Create Invitation|Invite/ }))
    await waitFor(() => expect(svc.inviteUser).toHaveBeenCalledWith(
      'x@example.com', 'QA_ENGINEER',
      [{ project_id: 'p1', role: 'QA_ENGINEER', additional_roles: ['RELEASE_MANAGER'] }],
      ['RELEASE_MANAGER'],
    ))
  })
})
