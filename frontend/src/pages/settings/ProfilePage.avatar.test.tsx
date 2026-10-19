/**
 * W29a UIX-01 (L3): the profile page's avatar preview and colour picker read
 * the ONE avatar map (`components/ui/avatarColors`), the same as the top bar.
 * The initials are the family's ink on its tint; white initials (about 1.3:1 on
 * a light theme's 15 % tint) are gone, and so is the white check on the
 * selected swatch.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ProfilePage from './ProfilePage'
import type { User } from '../../store/authStore'
import { AVATAR_COLOR_KEYS, AVATAR_COLORS } from '@/components/ui/avatarColors'

type AuthSlice = {
  user: User | null
  setAuth: () => void
  token: string | null
  refreshToken: string | null
}

const mocked = vi.hoisted(() => {
  const state: AuthSlice = { user: null, setAuth: vi.fn(), token: 't', refreshToken: 'r' }
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

const preview = () => screen.getByText('AA')
const swatch = (key: string) => screen.getByTitle(key)

describe('ProfilePage avatar (W29a UIX-01)', () => {
  beforeEach(() => {
    mocked.state.user = makeUser({})
  })

  it('the preview initials are the stored family\'s ink on its tint, never white', () => {
    render(<ProfilePage />)
    const el = preview()
    expect(el.className).not.toMatch(/text-white/)
    expect(el.style.background).toBe(AVATAR_COLORS.blue.bg)
    expect(el.style.color).toBe(AVATAR_COLORS.blue.fg)
    expect(el.style.color).toBe('var(--ui-info-ink)')
  })

  it('a red avatar previews the failed tint with the failed ink', () => {
    mocked.state.user = makeUser({ avatar_color: 'red' })
    render(<ProfilePage />)
    expect(preview().style.background).toBe('var(--status-failed-bg)')
    expect(preview().style.color).toBe('var(--status-failed-ink)')
  })

  it('offers the twelve stored keys, each swatch painted from the shared map', () => {
    render(<ProfilePage />)
    for (const key of AVATAR_COLOR_KEYS) {
      const el = swatch(key)
      expect(el.tagName).toBe('BUTTON')
      expect(el.style.background).toBe(AVATAR_COLORS[key].bg)
      // No page-local palette class is left on a swatch.
      expect(el.className).not.toMatch(/bg-\[|text-white|ring-\[/)
    }
  })

  it('rings only the selected swatch, with its family ring, and checks it in the text colour', () => {
    render(<ProfilePage />)
    const selected = swatch('blue')
    expect(selected.style.boxShadow).toBe('0 0 0 2px var(--color-bg-secondary), 0 0 0 4px var(--color-accent)')
    const check = selected.querySelector('svg')
    expect(check).not.toBeNull()
    expect(check?.getAttribute('class')).toContain('text-[var(--color-text)]')
    expect(check?.getAttribute('class')).not.toMatch(/text-white/)
    for (const key of AVATAR_COLOR_KEYS.filter((k) => k !== 'blue')) {
      expect(swatch(key).style.boxShadow).toBe('')
      expect(swatch(key).querySelector('svg')).toBeNull()
    }
  })

  it('picking a colour re-paints the preview from the map', () => {
    render(<ProfilePage />)
    fireEvent.click(swatch('violet'))
    expect(preview().style.background).toBe(AVATAR_COLORS.violet.bg)
    expect(preview().style.color).toBe(AVATAR_COLORS.violet.fg)
    expect(swatch('violet').style.boxShadow).toBe(`0 0 0 2px var(--color-bg-secondary), 0 0 0 4px ${AVATAR_COLORS.violet.ring}`)
    expect(swatch('blue').style.boxShadow).toBe('')
  })
})
