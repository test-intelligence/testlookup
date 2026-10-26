/**
 * W17b ADM-01: the page an invitation link opens (`/accept-invite#token=…`).
 *
 * It reads the token from the fragment once (and clears it), previews "invited
 * by X as Role", creates the account and lands signed in on Overview. Every bad
 * token reads the same: "invalid, expired or already used" + ask for a new one.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AcceptInvitePage from './AcceptInvitePage'

const preview = vi.hoisted(() => vi.fn())
const accept = vi.hoisted(() => vi.fn())
const fetchUser = vi.hoisted(() => vi.fn())
vi.mock('@/services/authService', () => ({
  previewInvitation: (t: string) => preview(t),
  acceptInvitation: (b: unknown) => accept(b),
  fetchCurrentUser: (t?: string) => fetchUser(t),
}))
const setAuth = vi.hoisted(() => vi.fn())
vi.mock('@/store/authStore', () => ({
  useAuthStore: (selector: (s: { setAuth: unknown }) => unknown) => selector({ setAuth }),
}))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

const PREVIEW = {
  email: 'new.tester@testlookup.dev',
  role: 'QA_ENGINEER',
  invited_by_name: 'Admin',
  expires_at: '2026-11-02T00:00:00Z',
  project_names: ['Payment Service'],
  sso_required: false,
}

function Landed() {
  const { pathname, search } = useLocation()
  return <output data-testid="landed">{`${pathname}${search}`}</output>
}

function renderAt(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/accept-invite" element={<AcceptInvitePage />} />
        <Route path="*" element={<Landed />} />
      </Routes>
    </MemoryRouter>,
  )
}

function fill(username = 'newtester', password = 'Sup3r-secret!') {
  fireEvent.change(screen.getByLabelText('Username'), { target: { value: username } })
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: password } })
  fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: password } })
}

describe('AcceptInvitePage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    preview.mockResolvedValue(PREVIEW)
    fetchUser.mockResolvedValue({ id: 'u9', username: 'newtester', role: 'QA_ENGINEER' })
  })

  it('previews who invited whom, creates the account and lands signed in on Overview', async () => {
    accept.mockResolvedValue({ access_token: 'acc', refresh_token: 'ref', token_type: 'bearer', expires_in: 60, must_change_password: false })
    renderAt('/accept-invite#token=tok-123')

    expect(await screen.findByTestId('invite-summary')).toHaveTextContent('You were invited by Admin as QA Engineer.')
    expect(screen.getByText('Projects: Payment Service')).toBeInTheDocument()
    expect(preview).toHaveBeenCalledWith('tok-123')
    fill()
    fireEvent.click(screen.getByRole('button', { name: 'Create account and sign in' }))

    expect(await screen.findByTestId('landed')).toHaveTextContent('/overview')
    expect(accept).toHaveBeenCalledWith({ token: 'tok-123', username: 'newtester', full_name: undefined, password: 'Sup3r-secret!' })
    expect(setAuth).toHaveBeenCalledWith('acc', 'ref', expect.objectContaining({ username: 'newtester' }))
  })

  it('an invalid, expired or used link says so and offers no form', async () => {
    preview.mockRejectedValue({ response: { status: 400 } })
    renderAt('/accept-invite#token=used')
    expect(await screen.findByTestId('invite-invalid')).toHaveTextContent(
      'This invitation link is invalid, expired or already used.',
    )
    expect(screen.getByText('Ask your administrator for a new invitation.')).toBeInTheDocument()
    expect(screen.queryByLabelText('Username')).toBeNull()
  })

  it('no token at all is the same dead end, and nothing is asked of the server', async () => {
    renderAt('/accept-invite')
    expect(await screen.findByTestId('invite-invalid')).toBeInTheDocument()
    expect(preview).not.toHaveBeenCalled()
  })

  it('a taken username stays on the form with the reason; a mismatch blocks the submit', async () => {
    accept.mockRejectedValue({ response: { status: 409, data: { detail: 'That username is taken. Choose another.' } } })
    renderAt('/accept-invite#token=tok-1')
    await screen.findByTestId('invite-summary')
    fill('admin')
    fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: 'different' } })
    expect(screen.getByText('Passwords do not match.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create account and sign in' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: 'Sup3r-secret!' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create account and sign in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('That username is taken. Choose another.')
    expect(screen.getByLabelText('Username')).toBeInTheDocument()
  })

  it('MFA owed: the account is made and sign-in takes over', async () => {
    accept.mockResolvedValue({ mfa_enrollment_required: true, enrollment_token: 'e', expires_in: 300, required_for_role: null })
    renderAt('/accept-invite#token=tok-2')
    await screen.findByTestId('invite-summary')
    fill()
    fireEvent.click(screen.getByRole('button', { name: 'Create account and sign in' }))
    expect(await screen.findByTestId('landed')).toHaveTextContent('/login?invited=1')
    expect(setAuth).not.toHaveBeenCalled()
  })

  it('SSO-only workspace: no password form, points at SSO', async () => {
    preview.mockResolvedValue({ ...PREVIEW, sso_required: true })
    renderAt('/accept-invite#token=tok-3')
    expect(await screen.findByRole('alert')).toHaveTextContent('signs in with single sign-on')
    await waitFor(() => expect(screen.queryByLabelText('Password')).toBeNull())
  })
})
