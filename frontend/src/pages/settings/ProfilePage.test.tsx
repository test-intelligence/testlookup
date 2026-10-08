import { fireEvent, render, screen } from '@testing-library/react'
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

// UX redesign P5: the template header, and item 4 — profile | password side
// by side at >= 1280 px instead of one max-w-2xl column.
describe('ProfilePage layout', () => {
  beforeEach(() => {
    mocked.state.user = makeUser({})
  })

  it('each password eye says what it does, and flips (browser E2E pass: they had no name)', () => {
    render(<ProfilePage />)
    const eyes = screen.getAllByRole('button', { name: 'Show password' })
    expect(eyes).toHaveLength(3)
    fireEvent.click(eyes[0])
    expect(eyes[0]).toHaveAccessibleName('Hide password')
    expect(eyes[0]).toHaveAttribute('title', 'Hide password')
  })

  it('renders the compact template header with a help topic', () => {
    render(<ProfilePage />)
    expect(document.querySelector('[data-page-header]')).toHaveAttribute('data-compact', 'true')
    expect(screen.getByRole('button', { name: 'Help: My Profile' })).toHaveAttribute('data-help-topic', 'administration')
  })

  it('puts the profile and password forms in a grid that goes two-up at xl, with no max-w column', () => {
    const { container } = render(<ProfilePage />)
    const grid = container.querySelector('[data-profile-forms]') as HTMLElement
    expect(grid).not.toBeNull()
    expect(grid.className).toContain('xl:grid-cols-2')
    expect(container.querySelector('[class*="max-w-2xl"]')).toBeNull()

    const sections = grid.querySelectorAll(':scope > section')
    expect(sections).toHaveLength(2)
    expect(sections[0].textContent).toMatch(/Profile Information/)
    expect(sections[1].textContent).toMatch(/Change Password/)
    // Two-factor stays below the pair, at full width.
    expect(grid.contains(screen.getByText(/Two-Factor|two-factor/, { selector: 'h2, h3' }))).toBe(false)
  })
})
