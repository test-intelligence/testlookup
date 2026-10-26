/**
 * W17b ADM-02: "Forgot password?" and the reset-link page.
 *
 * Forgot: with SMTP the email form, then ONE confirmation whatever happened
 * (even an error — the page must not reveal whether an account exists); without
 * SMTP, "Ask an administrator for a reset link". Reset confirm: the fragment
 * token + a new password; a dead link says so; success goes to sign-in.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ForgotPasswordPage from './ForgotPasswordPage'
import ResetPasswordConfirmPage from './ResetPasswordConfirmPage'

const options = vi.hoisted(() => vi.fn())
const forgot = vi.hoisted(() => vi.fn())
const reset = vi.hoisted(() => vi.fn())
vi.mock('@/services/authService', () => ({
  getAuthOptions: () => options(),
  requestPasswordReset: (e: string) => forgot(e),
  resetPasswordWithToken: (t: string, p: string) => reset(t, p),
}))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

function Landed() {
  const { pathname, search } = useLocation()
  return <output data-testid="landed">{`${pathname}${search}`}</output>
}

function renderAt(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password/confirm" element={<ResetPasswordConfirmPage />} />
        <Route path="*" element={<Landed />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('ForgotPasswordPage', () => {
  beforeEach(() => vi.clearAllMocks())

  it('without SMTP it says to ask an administrator and offers no form', async () => {
    options.mockResolvedValue({ registration_enabled: false, password_reset_email: false })
    renderAt('/forgot-password')
    expect(await screen.findByTestId('forgot-no-smtp')).toHaveTextContent('Ask an administrator for a reset link.')
    expect(screen.queryByLabelText('Email address')).toBeNull()
  })

  it('with SMTP it takes an address and shows the one confirmation', async () => {
    options.mockResolvedValue({ registration_enabled: false, password_reset_email: true })
    forgot.mockResolvedValue({ detail: 'x' })
    renderAt('/forgot-password')
    fireEvent.change(await screen.findByLabelText('Email address'), { target: { value: 'viewer@testlookup.dev' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send reset link' }))
    expect(await screen.findByTestId('forgot-sent')).toHaveTextContent(
      'If an account exists for that address, a reset link is on its way.',
    )
    expect(forgot).toHaveBeenCalledWith('viewer@testlookup.dev')
  })

  it('an error reads exactly like a success (no oracle)', async () => {
    options.mockResolvedValue({ registration_enabled: false, password_reset_email: true })
    forgot.mockRejectedValue({ response: { status: 429 } })
    renderAt('/forgot-password')
    fireEvent.change(await screen.findByLabelText('Email address'), { target: { value: 'x@testlookup.dev' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send reset link' }))
    expect(await screen.findByTestId('forgot-sent')).toHaveTextContent('If an account exists')
  })

  it('an older server (no flag) reads as no SMTP', async () => {
    options.mockResolvedValue({ registration_enabled: false })
    renderAt('/forgot-password')
    expect(await screen.findByTestId('forgot-no-smtp')).toBeInTheDocument()
  })
})

describe('ResetPasswordConfirmPage', () => {
  beforeEach(() => vi.clearAllMocks())

  it('sets the new password with the fragment token and goes to sign in', async () => {
    reset.mockResolvedValue(undefined)
    renderAt('/reset-password/confirm#token=rst-1')
    fireEvent.change(screen.getByLabelText('New password'), { target: { value: 'New-pass-456' } })
    fireEvent.change(screen.getByLabelText('Confirm new password'), { target: { value: 'New-pass-456' } })
    fireEvent.click(screen.getByRole('button', { name: 'Change password' }))
    expect(await screen.findByTestId('landed')).toHaveTextContent('/login?reset=1')
    expect(reset).toHaveBeenCalledWith('rst-1', 'New-pass-456')
  })

  it('a used or expired link says so', async () => {
    reset.mockRejectedValue({ response: { status: 400 } })
    renderAt('/reset-password/confirm#token=old')
    fireEvent.change(screen.getByLabelText('New password'), { target: { value: 'New-pass-456' } })
    fireEvent.change(screen.getByLabelText('Confirm new password'), { target: { value: 'New-pass-456' } })
    fireEvent.click(screen.getByRole('button', { name: 'Change password' }))
    expect(await screen.findByTestId('reset-invalid')).toHaveTextContent(
      'This reset link is invalid, expired or already used.',
    )
  })

  it('no token: the dead-link state straight away', () => {
    renderAt('/reset-password/confirm')
    expect(screen.getByTestId('reset-invalid')).toBeInTheDocument()
  })
})
