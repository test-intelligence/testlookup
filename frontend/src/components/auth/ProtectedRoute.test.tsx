import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ProtectedRoute from './ProtectedRoute'

type AuthState = {
  _hasHydrated: boolean
  isAuthenticated: boolean
  token: string | null
  fetchUser: () => Promise<void>
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
})
