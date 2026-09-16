import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./api', () => ({
  api: { post: vi.fn() },
}))
vi.mock('./http', () => ({
  getData: vi.fn(),
}))

import { api } from './api'
import { getData } from './http'
import { fetchCurrentUser, loginWithPassword } from './authService'

describe('authService', () => {
  beforeEach(() => vi.clearAllMocks())

  it('submits credentials using the OAuth form contract and returns the response body', async () => {
    const response = { access_token: 'access', refresh_token: 'refresh', token_type: 'bearer' }
    vi.mocked(api.post).mockResolvedValue({ data: response })

    await expect(loginWithPassword('qa+lead@example.com', 'p&a=ss')).resolves.toEqual(response)

    const [path, body, config] = vi.mocked(api.post).mock.calls[0]
    expect(path).toBe('/api/v1/auth/login')
    expect(body).toBeInstanceOf(URLSearchParams)
    expect((body as URLSearchParams).toString()).toBe(
      'username=qa%2Blead%40example.com&password=p%26a%3Dss',
    )
    expect(config).toEqual({
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    })
  })

  it('uses the explicit access token while completing sign-in', async () => {
    const user = { id: 'user-1', username: 'qa', role: 'QA_LEAD' }
    vi.mocked(getData).mockResolvedValue(user)

    await expect(fetchCurrentUser('access-token')).resolves.toBe(user)
    expect(getData).toHaveBeenCalledWith('/api/v1/auth/me', {
      headers: { Authorization: 'Bearer access-token' },
    })
  })

  it('uses the shared authenticated client after sign-in', async () => {
    await fetchCurrentUser()
    expect(getData).toHaveBeenCalledWith('/api/v1/auth/me', undefined)
  })
})
