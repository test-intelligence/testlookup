/**
 * The browser's way back from the identity provider.
 *
 * The API's ACS answers the IdP's form POST with a 303 to
 * `/sso/callback#code=…&next=…`. This page spends the one-time code (once),
 * signs in, and goes where the sign-in started -- an app path only. Before
 * this page existed the ACS answered the browser with JSON tokens and the user
 * was never signed in.
 */
import { StrictMode } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SSOCallbackPage from './SSOCallbackPage'

const exchange = vi.hoisted(() => vi.fn())
vi.mock('@/services/ssoService', () => ({ exchangeSSOCode: (code: string) => exchange(code) }))

const USER = { id: 'u1', username: 'sso-user', role: 'VIEWER', must_change_password: false }
const fetchUser = vi.hoisted(() => vi.fn())
vi.mock('@/services/authService', () => ({ fetchCurrentUser: (t?: string) => fetchUser(t) }))

const setAuth = vi.hoisted(() => vi.fn())
vi.mock('@/store/authStore', () => ({
  useAuthStore: (selector: (s: { setAuth: unknown }) => unknown) => selector({ setAuth }),
}))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

const TOKENS = { access_token: 'acc', refresh_token: 'ref', token_type: 'bearer', expires_in: 3600, is_new_user: false }

function Landed() {
  const { pathname, search } = useLocation()
  return <output data-testid="landed">{`${pathname}${search}`}</output>
}

function renderAt(entry: string, { strict = false } = {}) {
  const tree = (
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/sso/callback" element={<SSOCallbackPage />} />
        <Route path="*" element={<Landed />} />
      </Routes>
    </MemoryRouter>
  )
  return render(strict ? <StrictMode>{tree}</StrictMode> : tree)
}

describe('SSOCallbackPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    exchange.mockResolvedValue(TOKENS)
    fetchUser.mockResolvedValue(USER)
  })

  it('spends the code, signs in, and lands where the sign-in started', async () => {
    renderAt(`/sso/callback#code=abc&next=${encodeURIComponent('/runs/r1?tab=analysis')}`)

    expect(await screen.findByTestId('landed')).toHaveTextContent('/runs/r1?tab=analysis')
    expect(exchange).toHaveBeenCalledWith('abc')
    expect(fetchUser).toHaveBeenCalledWith('acc')
    expect(setAuth).toHaveBeenCalledWith('acc', 'ref', USER)
  })

  it.each(['https://evil.example/x', '//evil.example/x', 'runs'])(
    'goes to the default page, never to %s',
    async (unsafe) => {
      renderAt(`/sso/callback#code=abc&next=${encodeURIComponent(unsafe)}`)
      expect(await screen.findByTestId('landed')).toHaveTextContent('/overview')
    },
  )

  it('spends the code once, even when React runs the effect twice', async () => {
    renderAt('/sso/callback#code=abc', { strict: true })
    await screen.findByTestId('landed')
    expect(exchange).toHaveBeenCalledTimes(1)
  })

  it('says so when the code has expired or was used, and offers sign-in', async () => {
    exchange.mockRejectedValue(new Error('400'))
    renderAt('/sso/callback#code=used')

    expect(await screen.findByRole('alert')).toHaveTextContent(/expired or was already used/i)
    expect(screen.getByRole('link', { name: /back to sign in/i })).toHaveAttribute('href', '/login')
    expect(setAuth).not.toHaveBeenCalled()
  })

  it('without a code it spends nothing', async () => {
    renderAt('/sso/callback')
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    await waitFor(() => expect(exchange).not.toHaveBeenCalled())
  })
})
