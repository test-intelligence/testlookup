/**
 * W17b ADM-01 / ADM-02 on /users (admin):
 * - the Pending invitations tab lists open invitations (expiry, projects) with Resend / Revoke;
 *   Resend shows the new copyable link in the row;
 * - a user row's "Send password reset link" issues a single-use link, shown once in the row
 *   ("Reset link emailed to x" or "SMTP is not configured — copy this link"); never on your own row.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const invitations = vi.hoisted(() => vi.fn())
const svc = vi.hoisted(() => ({
  listUsers: vi.fn(),
  updateUserRole: vi.fn(),
  updateUserStatus: vi.fn(),
  revokeInvitation: vi.fn(),
  resendInvitation: vi.fn(),
  issuePasswordResetLink: vi.fn(),
}))
const refreshInvitations = vi.hoisted(() => vi.fn())

vi.mock('@/hooks/useUserManagement', () => ({
  useUsersPage: () => ({
    data: {
      items: [
        { id: 'me', email: 'admin@testlookup.dev', username: 'admin', full_name: 'Admin', role: 'ADMIN', is_active: true, created_at: '' },
        { id: 'v1', email: 'viewer@testlookup.dev', username: 'viewer', full_name: 'Dev Viewer', role: 'VIEWER', is_active: true, created_at: '' },
      ],
      total: 2,
    },
    isLoading: false,
  }),
  useInvitations: () => invitations(),
  refreshUsers: vi.fn(),
  refreshInvitations,
}))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => ({ isAdmin: true, canManageUsers: true }) }))
vi.mock('@/store/authStore', () => {
  const state = { user: { id: 'me' }, fetchUser: vi.fn() }
  const useAuthStore = Object.assign((sel: (s: typeof state) => unknown) => sel(state), { getState: () => state })
  return { useAuthStore }
})
vi.mock('@/services/projectsService', () => ({ projectsService: { list: vi.fn().mockResolvedValue([]) } }))
vi.mock('@/services/userManagementService', () => ({ userManagementService: svc }))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))
vi.mock('./ProjectMembersTab', () => ({ ProjectMembersTab: () => null }))

import UserManagementPage from './UserManagementPage'

const ORIGIN = window.location.origin
const INV = {
  id: 'i1',
  email: 'new.tester@testlookup.dev',
  role: 'QA_ENGINEER',
  status: 'pending',
  expires_at: '2026-11-02T10:00:00Z',
  created_at: '2026-10-26T10:00:00Z',
  used_at: null,
  revoked_at: null,
  invited_by_name: 'Admin',
  project_memberships: [{ project_id: 'p1', project_name: 'Payment Service', role: 'QA_ENGINEER' }],
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <UserManagementPage />
    </MemoryRouter>,
  )
}

describe('Pending invitations tab', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    invitations.mockReturnValue({ data: [INV], isLoading: false, error: undefined })
  })

  it('lists the invitation with its project and inviter, and Resend shows the new link', async () => {
    svc.resendInvitation.mockResolvedValue({ ...INV, invitation_link: 'https://x/accept-invite#token=new-tok', email_sent: false })
    renderAt('/users?tab=invitations')
    const table = screen.getByTestId('pending-invitations')
    const row = within(table).getByText('new.tester@testlookup.dev').closest('tr') as HTMLElement
    expect(row).toHaveTextContent('Payment Service')
    expect(row).toHaveTextContent('Admin')
    fireEvent.click(within(row).getByRole('button', { name: 'Resend' }))
    expect(await screen.findByText(`${ORIGIN}/accept-invite#token=new-tok`)).toBeInTheDocument()
    expect(screen.getByText(/SMTP is not configured/)).toBeInTheDocument()
    expect(svc.resendInvitation).toHaveBeenCalledWith('i1')
    expect(refreshInvitations).toHaveBeenCalled()
  })

  it('Revoke calls the API and re-reads the list', async () => {
    svc.revokeInvitation.mockResolvedValue({ ...INV, status: 'revoked' })
    renderAt('/users?tab=invitations')
    fireEvent.click(screen.getByRole('button', { name: 'Revoke' }))
    await waitFor(() => expect(svc.revokeInvitation).toHaveBeenCalledWith('i1'))
    expect(refreshInvitations).toHaveBeenCalled()
  })

  it('an empty list says how to invite', () => {
    invitations.mockReturnValue({ data: [], isLoading: false, error: undefined })
    renderAt('/users?tab=invitations')
    expect(screen.getByText('No pending invitations. Invite someone from the Users tab.')).toBeInTheDocument()
  })
})

describe('Send password reset link (row action)', () => {
  beforeEach(() => vi.clearAllMocks())

  it('is offered for another user, not for yourself', () => {
    renderAt('/users')
    expect(screen.getByRole('button', { name: 'Send password reset link to viewer' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Send password reset link to admin' })).toBeNull()
  })

  it('creates the link and shows it once, emailed or to copy', async () => {
    svc.issuePasswordResetLink.mockResolvedValue({
      link: 'https://x/reset-password/confirm#token=rst-9', email_sent: true, expires_at: '2026-10-26T11:00:00Z',
    })
    renderAt('/users')
    fireEvent.click(screen.getByRole('button', { name: 'Send password reset link to viewer' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Create reset link' }))
    expect(await screen.findByText(`${ORIGIN}/reset-password/confirm#token=rst-9`)).toBeInTheDocument()
    expect(screen.getByTestId('one-time-link')).toHaveTextContent('Reset link emailed to viewer@testlookup.dev')
    expect(svc.issuePasswordResetLink).toHaveBeenCalledWith('v1')
  })
})
