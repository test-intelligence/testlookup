/**
 * "Send test" showed every failure twice: the settings page toasts the outcome
 * itself, and the API client toasted the same refusal (homelab sweep,
 * 2026-10-10). The request opts out of the client's toast.
 */
import { describe, expect, it, vi } from 'vitest'

const postData = vi.hoisted(() => vi.fn(async () => ({ status: 'sent' })))
vi.mock('./http', () => ({ postData, getData: vi.fn(), putData: vi.fn(), deleteData: vi.fn(), patchData: vi.fn() }))

const { notificationService } = await import('./notificationService')

describe('notificationService.sendTest', () => {
  it('asks the client not to toast: the page reports the outcome', async () => {
    await notificationService.sendTest('slack', 'pref-1')
    expect(postData).toHaveBeenCalledWith(
      '/api/v1/notifications/test',
      { channel: 'slack', preference_id: 'pref-1' },
      { suppressToast: true },
    )
  })
})
