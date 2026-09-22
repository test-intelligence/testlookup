/**
 * VIZ-306 "Signed out": a filtered report link opened while signed out must,
 * after signing in, land on the SAME path with the FULL query string intact.
 *
 * Before this, `ProtectedRoute` saved the whole location but `LoginPage` read
 * back only `pathname`, so a shared `/trends?release=…&suites=…&window=14`
 * signed the reader in to an UNFILTERED /trends — a link that "worked" and
 * showed the wrong numbers.
 *
 * End to end through the real `ProtectedRoute` and `LoginPage` and a real
 * router: only the network edges (auth service, SSO) are stubbed.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { RouterProvider, createMemoryRouter, useLocation } from 'react-router-dom'
import { create } from 'zustand'
import { describe, expect, it, vi } from 'vitest'
import { returnPathFrom } from '@/utils/returnPath'

const auth = vi.hoisted(() => ({ store: null as unknown }))

vi.mock('@/store/authStore', () => {
  interface S {
    _hasHydrated: boolean
    isAuthenticated: boolean
    token: string | null
    user: unknown
    refreshError: string | null
    refreshRequiresReauth: boolean
    refreshRetryExhausted: boolean
    retryRefresh: () => void
    fetchUser: () => Promise<void>
    logout: () => void
    setAuth: (t: string, r: string, u: unknown) => void
  }
  const useAuthStore = create<S>()((set) => ({
    _hasHydrated: true,
    isAuthenticated: false,
    token: null,
    user: null,
    refreshError: null,
    refreshRequiresReauth: false,
    refreshRetryExhausted: false,
    retryRefresh: () => {},
    fetchUser: async () => {},
    logout: () => {},
    setAuth: (token, _r, user) => set({ token, user, isAuthenticated: true }),
  }))
  auth.store = useAuthStore
  return { useAuthStore }
})

vi.mock('@/services/authService', () => ({
  loginWithPassword: vi.fn(async () => ({
    access_token: 'acc',
    refresh_token: 'ref',
    token_type: 'bearer',
    expires_in: 3600,
    must_change_password: false,
  })),
  fetchCurrentUser: vi.fn(async () => ({ id: 'u1', username: 'alice', role: 'ADMIN', must_change_password: false })),
}))
vi.mock('@/hooks/useMfaStatus', () => ({
  startMfaEnrollment: vi.fn(),
  confirmMfaEnrollment: vi.fn(),
  verifyMfaChallenge: vi.fn(),
}))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))
vi.mock('@/services/ssoService', () => ({ getSSOStatus: () => Promise.reject(new Error('sso off')) }))
vi.mock('@/services/api', () => ({ api: { post: vi.fn(), get: vi.fn() } }))

import ProtectedRoute from '@/components/auth/ProtectedRoute'
import LoginPage from './LoginPage'

const R1 = '11111111-0000-4000-8000-000000000001'
const R2 = '22222222-0000-4000-8000-000000000002'

function Landed() {
  const loc = useLocation()
  return <div data-testid="landed">{`${loc.pathname}${loc.search}${loc.hash}`}</div>
}

describe('signed-out filtered link → sign in → same URL', () => {
  it('keeps the full query string (and hash) through the login redirect', async () => {
    const link = `/trends?release=${R1}&release=${R2}&suites=payments&suites=cart%20%26%20co&window=14&tab=kpis#chart`
    const router = createMemoryRouter(
      [
        { path: '/login', element: <LoginPage /> },
        { element: <ProtectedRoute />, children: [{ path: '/trends', element: <Landed /> }] },
      ],
      { initialEntries: [link] },
    )
    render(<RouterProvider router={router} />)

    // Signed out → sent to /login.
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))

    fireEvent.change(screen.getByLabelText(/email or username/i), { target: { value: 'alice' } })
    fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: 'pw' } })
    fireEvent.click(screen.getByRole('button', { name: /log in/i }))

    await waitFor(() => expect(screen.getByTestId('landed')).toHaveTextContent(link))
  })
})

describe('returnPathFrom', () => {
  it('keeps pathname + search + hash', () => {
    expect(returnPathFrom({ pathname: '/coverage', search: '?suites=a', hash: '#x' })).toBe('/coverage?suites=a#x')
  })
  it('falls back for reset-password, missing, and non-app paths', () => {
    expect(returnPathFrom(undefined)).toBe('/overview')
    expect(returnPathFrom({ pathname: '/reset-password', search: '?a=1' })).toBe('/overview')
    expect(returnPathFrom({ pathname: '//evil.example/x' })).toBe('/overview')
    expect(returnPathFrom({ pathname: 'https://evil.example' })).toBe('/overview')
    expect(returnPathFrom({ pathname: '/\\evil' })).toBe('/overview')
  })
  it('ignores a search/hash without its leading marker', () => {
    expect(returnPathFrom({ pathname: '/trends', search: 'x=1', hash: 'y' })).toBe('/trends')
  })
})
