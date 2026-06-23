import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ProfilePage from './ProfilePage'
import type { User } from '../../store/authStore'

// Minimal auth-store mock: a mutable state object read through a selector,
// mirroring how the real zustand store is consumed in ProfilePage.
type AuthSlice = {
  user: User | null
  setAuth: () => void
  token: string | null
  refreshToken: string | null
}

const mocked = vi.hoisted(() => {
  const state: AuthSlice = {
    user: null,
    setAuth: vi.fn(),
    token: 't',
    refreshToken: 'r',
  }
  const useAuthStore = vi.fn((selector: (s: AuthSlice) => unknown) => selector(state))
  return { state, useAuthStore }
})

vi.mock('@/store/authStore', () => ({ useAuthStore: mocked.useAuthStore }))
vi.mock('@/services/api', () => ({ api: { patch: vi.fn(), post: vi.fn() } }))

function makeUser(overrides: Partial<User>): User {
  return {
    id: '1',
    email: 'a@example.com',
    username: 'alice',
    full_name: 'Alice Adams',
    role: 'ADMIN',
    is_active: true,
    must_change_password: false,
    avatar_color: 'blue',
    ...overrides,
  }
}

function fullNameInput() {
  return screen.getByPlaceholderText('Your display name') as HTMLInputElement
}

describe('ProfilePage', () => {
  beforeEach(() => {
    mocked.state.user = makeUser({})
  })

  it('seeds the editable form from the current user', () => {
    render(<ProfilePage />)
    expect(fullNameInput().value).toBe('Alice Adams')
  })

  it('re-seeds the form when the user object changes (render-phase sync)', () => {
    const { rerender } = render(<ProfilePage />)
    expect(fullNameInput().value).toBe('Alice Adams')

    // Simulate the canonical user changing (e.g. saved elsewhere / re-login).
    mocked.state.user = makeUser({ full_name: 'Alice Brown', avatar_color: 'teal' })
    rerender(<ProfilePage />)

    expect(fullNameInput().value).toBe('Alice Brown')
  })

  it('falls back to empty name when the user has no full_name', () => {
    mocked.state.user = makeUser({ full_name: null })
    render(<ProfilePage />)
    expect(fullNameInput().value).toBe('')
  })
})
