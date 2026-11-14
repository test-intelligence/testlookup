/**
 * The three user forms render their own refusal (UserManagementPage `FormError`), so their requests opt out
 * of the shared interceptor's toast; without this the admin saw the reason twice, once as a generic sentence.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const http = vi.hoisted(() => ({
  post: vi.fn().mockResolvedValue({ data: {} }),
  patch: vi.fn().mockResolvedValue({ data: {} }),
}))
vi.mock('./api', () => ({ api: http }))

import { userManagementService } from './userManagementService'

describe('userManagementService: the user forms own their error message', () => {
  beforeEach(() => vi.clearAllMocks())

  it('createUser posts the user without the shared toast', async () => {
    await userManagementService.createUser('jdoe@example.com', 'jdoe', 'VIEWER', 'Jane Doe')
    expect(http.post).toHaveBeenCalledWith(
      '/api/v1/users',
      { email: 'jdoe@example.com', username: 'jdoe', role: 'VIEWER', full_name: 'Jane Doe' },
      { suppressToast: true },
    )
  })

  it('inviteUser posts the invitation without the shared toast', async () => {
    await userManagementService.inviteUser('new.hire@example.com', 'TESTER', [{ project_id: 'p-1', role: 'TESTER' }])
    expect(http.post).toHaveBeenCalledWith(
      '/api/v1/users/invite',
      { email: 'new.hire@example.com', role: 'TESTER', project_memberships: [{ project_id: 'p-1', role: 'TESTER' }] },
      { suppressToast: true },
    )
  })

  it('updateUserProfile patches the user without the shared toast', async () => {
    await userManagementService.updateUserProfile('u-1', { email: 'jdoe@example.com' })
    expect(http.patch).toHaveBeenCalledWith('/api/v1/users/u-1', { email: 'jdoe@example.com' }, { suppressToast: true })
  })
})
