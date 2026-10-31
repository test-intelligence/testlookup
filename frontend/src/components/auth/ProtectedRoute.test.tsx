import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ProtectedRoute from './ProtectedRoute'

type AuthState = {
  _hasHydrated: boolean
  isAuthenticated: boolean
  token: string | null
  fetchUser: () => Promise<void>
  lastVerifiedAt?: number | null
}

const mocked = vi.hoisted(() => {
  const state: AuthState = {
    _hasHydrated: true,
    isAuthenticated: false,
    token: null,
    fetchUser: vi.fn(async () => {}),
  }
  const useAuthStore = vi.fn((selector: (s: AuthState) => unknown) => selector(state))
  ;(useAuthStore as unknown as { getState: () => AuthState }).getState = () => state
  return { state, useAuthStore }
})

vi.mock('../../store/authStore', () => ({
  useAuthStore: mocked.useAuthStore,
}))

function renderWithRoutes(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/login" element={<div>Login Page</div>} />
        <Route element={<ProtectedRoute />}>
          <Route path="/overview" element={<div>Overview Page</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

describe('ProtectedRoute', () => {
  beforeEach(() => {
    mocked.state._hasHydrated = true
    mocked.state.isAuthenticated = false
    mocked.state.token = null
    mocked.state.fetchUser = vi.fn(async () => {})
    delete mocked.state.lastVerifiedAt
    mocked.useAuthStore.mockClear()
  })

  it('shows loader while store is hydrating', () => {
    mocked.state._hasHydrated = false
    renderWithRoutes('/overview')

    expect(screen.queryByText('Overview Page')).not.toBeInTheDocument()
    expect(screen.queryByText('Login Page')).not.toBeInTheDocument()
  })

  it('redirects to login when unauthenticated', async () => {
    renderWithRoutes('/overview')

    await waitFor(() => {
      expect(screen.getByText('Login Page')).toBeInTheDocument()
    })
  })

  it('renders protected content when authenticated', () => {
    mocked.state.isAuthenticated = true
    renderWithRoutes('/overview')

    expect(screen.getByText('Overview Page')).toBeInTheDocument()
  })

  it('validates token by calling fetchUser after hydration', async () => {
    mocked.state.token = 'token'
    mocked.state.isAuthenticated = false
    const fetchUser = vi.fn(async () => {})
    mocked.state.fetchUser = fetchUser

    renderWithRoutes('/overview')

    await waitFor(() => {
      expect(fetchUser).toHaveBeenCalledTimes(1)
    })
  })

  // W25a HYG-07: the sign-in's own `/auth/me` (stamped by `setAuth`) IS the
  // verification — the protected shell does not ask again within 30 s.
  it('does not re-verify a session the server confirmed under 30 s ago', async () => {
    mocked.state.token = 'token'
    mocked.state.isAuthenticated = true
    mocked.state.lastVerifiedAt = Date.now() - 1_000
    const fetchUser = vi.fn(async () => {})
    mocked.state.fetchUser = fetchUser

    renderWithRoutes('/overview')

    await waitFor(() => {
      expect(screen.getByText('Overview Page')).toBeInTheDocument()
    })
    expect(fetchUser).not.toHaveBeenCalled()
  })

  it('re-verifies once the confirmation is older than 30 s', async () => {
    mocked.state.token = 'token'
    mocked.state.isAuthenticated = true
    mocked.state.lastVerifiedAt = Date.now() - 31_000
    const fetchUser = vi.fn(async () => {})
    mocked.state.fetchUser = fetchUser

    renderWithRoutes('/overview')

    await waitFor(() => {
      expect(fetchUser).toHaveBeenCalledTimes(1)
    })
    expect(screen.getByText('Overview Page')).toBeInTheDocument()
  })
})
