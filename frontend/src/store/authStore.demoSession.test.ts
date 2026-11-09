/**
 * `demoSession`: the public read-only demo's sessions carry a flag set by its
 * one-click sign-in, so the app shell can show the demo banner without asking
 * the server on every page (and no other deployment pays that request).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../services/api', () => ({ api: { get: vi.fn(), post: vi.fn(async () => ({})) } }))

const { useAuthStore } = await import('./authStore')

const USER = {
  id: 'u1',
  email: 'lead@testlookup.dev',
  username: 'qa_lead',
  full_name: 'QA Lead',
  role: 'QA_LEAD',
  is_active: true,
  must_change_password: false,
  avatar_color: null,
}

const persisted = () => JSON.parse(localStorage.getItem('auth-storage') ?? '{}').state ?? {}

describe('authStore.demoSession', () => {
  beforeEach(() => {
    localStorage.clear()
    useAuthStore.getState().logout()
  })

  it('is off by default and for an ordinary sign-in', () => {
    expect(useAuthStore.getState().demoSession).toBe(false)
    useAuthStore.getState().setAuth('acc', 'ref', USER)
    expect(useAuthStore.getState().demoSession).toBe(false)
  })

  it('is on for a demo sign-in, and survives a reload (persisted)', () => {
    useAuthStore.getState().setAuth('acc', 'ref', USER, { demo: true })
    expect(useAuthStore.getState().demoSession).toBe(true)
    expect(persisted().demoSession).toBe(true)
  })

  it('is cleared by logout and by the next ordinary sign-in', () => {
    useAuthStore.getState().setAuth('acc', 'ref', USER, { demo: true })
    useAuthStore.getState().logout()
    expect(useAuthStore.getState().demoSession).toBe(false)

    useAuthStore.getState().setAuth('acc', 'ref', USER, { demo: true })
    useAuthStore.getState().setAuth('acc2', 'ref2', USER)
    expect(useAuthStore.getState().demoSession).toBe(false)
  })
})
