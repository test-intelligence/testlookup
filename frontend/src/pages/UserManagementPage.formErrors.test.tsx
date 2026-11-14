/**
 * Add User, Invite User and Edit User show the server's refusal in the form (2026-11-14).
 *
 * The owner could not create `topu_admin`: the server refuses an e-mail on a reserved domain (`.local`,
 * `.test`) with a 422 naming the field, but the page answered "Failed to create user" next to the shared
 * interceptor's toast, both gone in seconds. Each form now sends its request with `suppressToast` and
 * renders the reason itself, until the next submit. Edit User handed a 422's list to `toast.error`.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import toast from 'react-hot-toast'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { UserItem } from '@/services/userManagementService'

vi.mock('@/hooks/useUserManagement', () => ({
  useUsersPage: () => ({ data: { items: [PRIYA], total: 1 }, isLoading: false }),
  refreshUsers: vi.fn(),
  refreshInvitations: vi.fn(),
}))
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ isAdmin: true, canManageUsers: true }),
}))
vi.mock('@/services/projectsService', () => ({
  projectsService: { list: vi.fn().mockResolvedValue([]) },
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
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
} as UserItem

/** The body FastAPI 0.136 / Pydantic 2.14 / email-validator 2.3 return for `topu_admin@corp.local`. */
const RESERVED_DOMAIN_REASON =
  'value is not a valid email address: The part after the @-sign is a special-use or reserved name that cannot be used with email.'
function refused(status: number, detail: unknown) {
  return Object.assign(new Error(`Request failed with status code ${status}`), { response: { status, data: { detail } } })
}
const reservedDomain = () =>
  refused(422, [
    {
      type: 'value_error',
      loc: ['body', 'email'],
      msg: RESERVED_DOMAIN_REASON,
      input: 'topu_admin@corp.local',
      ctx: { reason: 'The part after the @-sign is a special-use or reserved name that cannot be used with email.' },
    },
  ])
const SENTENCE = `Validation error: email — ${RESERVED_DOMAIN_REASON}`

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/users']}>
      <UserManagementPage />
    </MemoryRouter>,
  )
}

function openAddUser() {
  fireEvent.click(screen.getByRole('button', { name: 'Add User' }))
  const form = screen.getByRole('region', { name: 'Add User' })
  fireEvent.change(within(form).getByLabelText('Email address'), { target: { value: 'topu_admin@corp.local' } })
  fireEvent.change(within(form).getByLabelText('Username'), { target: { value: 'topu_admin' } })
  return form
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('Add User shows the server\'s refusal in the form', () => {
  it('a reserved e-mail domain reads as the field and its reason, with no toast', async () => {
    svc.createUser.mockRejectedValue(reservedDomain())
    renderPage()
    const form = openAddUser()

    fireEvent.click(within(form).getByRole('button', { name: 'Create User' }))

    expect(await within(form).findByRole('alert')).toHaveTextContent(SENTENCE)
    expect(toast.error, 'the form owns the message: no generic "Failed to create user" toast').not.toHaveBeenCalled()
    // The form stays, with what was typed, so the admin can correct the e-mail.
    expect(within(form).getByLabelText('Email address')).toHaveValue('topu_admin@corp.local')
    expect(within(form).getByRole('button', { name: 'Create User' })).toBeEnabled()
  })

  it('a 409 sentence is shown as it is', async () => {
    svc.createUser.mockRejectedValue(refused(409, 'Email or username already registered'))
    renderPage()
    const form = openAddUser()

    fireEvent.click(within(form).getByRole('button', { name: 'Create User' }))

    expect(await within(form).findByRole('alert')).toHaveTextContent(/^Email or username already registered$/)
  })

  it('no response at all keeps the old sentence', async () => {
    svc.createUser.mockRejectedValue(new Error('Network Error'))
    renderPage()
    const form = openAddUser()

    fireEvent.click(within(form).getByRole('button', { name: 'Create User' }))

    expect(await within(form).findByRole('alert')).toHaveTextContent(/^Failed to create user$/)
  })

  it('the next submit clears the message; a success shows the temporary password', async () => {
    svc.createUser
      .mockRejectedValueOnce(reservedDomain())
      .mockResolvedValueOnce({ id: 'u-1', username: 'topu_admin', temp_password: 'tmp-pass-123' })
    renderPage()
    const form = openAddUser()
    fireEvent.click(within(form).getByRole('button', { name: 'Create User' }))
    await within(form).findByRole('alert')

    fireEvent.change(within(form).getByLabelText('Email address'), { target: { value: 'topu_admin@testlookup.example.com' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Create User' }))

    expect(await within(form).findByText('tmp-pass-123')).toBeInTheDocument()
    expect(within(form).queryByRole('alert')).toBeNull()
    expect(svc.createUser).toHaveBeenLastCalledWith('topu_admin@testlookup.example.com', 'topu_admin', 'QA_ENGINEER', undefined)
  })
})

describe('Invite User shows the server\'s refusal in the form', () => {
  it('a reserved e-mail domain reads as the field and its reason, with no toast', async () => {
    svc.inviteUser.mockRejectedValue(reservedDomain())
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Invite User' }))
    const form = screen.getByRole('region', { name: 'Invite User' })
    fireEvent.change(within(form).getByLabelText('Email address'), { target: { value: 'topu_admin@corp.local' } })

    fireEvent.click(within(form).getByRole('button', { name: 'Send Invitation' }))

    expect(await within(form).findByRole('alert')).toHaveTextContent(SENTENCE)
    expect(toast.error).not.toHaveBeenCalled()
  })
})

describe('Edit User shows the server\'s refusal in the form', () => {
  it('a 422 reads as a sentence in the form, which stays open; no toast gets the raw list', async () => {
    svc.updateUserProfile.mockRejectedValue(reservedDomain())
    renderPage()
    fireEvent.click(within(screen.getByText('Priya Nair').closest('tr') as HTMLElement).getByTitle('Edit user'))
    const form = screen.getByRole('region', { name: 'Edit User' })
    fireEvent.change(within(form).getByLabelText('Email'), { target: { value: 'priya@corp.local' } })

    fireEvent.click(within(form).getByRole('button', { name: 'Save Changes' }))

    expect(await within(form).findByRole('alert')).toHaveTextContent(SENTENCE)
    expect(svc.updateUserProfile).toHaveBeenCalledWith('priya', { email: 'priya@corp.local' })
    expect(toast.error).not.toHaveBeenCalled()
    await waitFor(() => expect(within(form).getByRole('button', { name: 'Save Changes' })).toBeEnabled())
    expect(screen.getByRole('region', { name: 'Edit User' })).toBe(form)
  })
})
