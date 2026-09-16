import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { post, logout, navigate, success, error } = vi.hoisted(() => ({
  post: vi.fn(),
  logout: vi.fn(),
  navigate: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}))

vi.mock('../services/api', () => ({ api: { post } }))
vi.mock('react-hot-toast', () => ({ default: { success, error } }))
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useNavigate: () => navigate }
})
vi.mock('../store/authStore', () => ({
  useAuthStore: (selector: (state: unknown) => unknown) => selector({
    user: { username: 'exp-m01-user', full_name: 'M01 User' },
    logout,
  }),
}))

import ResetPasswordPage from './ResetPasswordPage'

describe('ResetPasswordPage revoked bootstrap session', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    post.mockResolvedValue({ status: 204 })
  })

  it('clears revoked tokens and requires sign-in with the permanent password', async () => {
    render(<ResetPasswordPage />)

    fireEvent.change(screen.getByLabelText('New Password'), {
      target: { value: 'Permanent-M01-9!' },
    })
    fireEvent.change(screen.getByLabelText('Confirm Password'), {
      target: { value: 'Permanent-M01-9!' },
    })
    fireEvent.click(screen.getByRole('button', { name: /set password/i }))

    await waitFor(() => expect(post).toHaveBeenCalledWith(
      '/api/v1/auth/first-time-reset',
      { new_password: 'Permanent-M01-9!', confirm_password: 'Permanent-M01-9!' },
    ))
    expect(logout).toHaveBeenCalledTimes(1)
    expect(success).toHaveBeenCalledWith('Password updated. Sign in with your new password.')
    expect(navigate).toHaveBeenCalledWith('/login', { replace: true })
    expect(error).not.toHaveBeenCalled()
  })
})
