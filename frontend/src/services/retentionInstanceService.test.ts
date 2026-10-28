/**
 * W22b ACT-06: the instance retention defaults service — the four literal paths and their bodies.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from './api'
import { retentionInstanceService } from './retentionInstanceService'

vi.mock('./api', () => ({ api: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }))

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.get).mockResolvedValue({ data: { enabled: false } })
  vi.mocked(api.put).mockResolvedValue({ data: { enabled: false } })
  vi.mocked(api.post).mockResolvedValue({ data: { enabled: true } })
})

describe('retentionInstanceService', () => {
  it('reads the defaults from the settings path', async () => {
    await expect(retentionInstanceService.getInstancePolicy()).resolves.toEqual({ enabled: false })
    expect(api.get).toHaveBeenCalledWith('/api/v1/settings/retention-defaults', undefined)
  })

  it('saves the defaults with the given body (clear included)', async () => {
    const body = { runs_days: 180, clear: ['artifacts_days' as const] }
    await retentionInstanceService.updateInstancePolicy(body)
    expect(api.put).toHaveBeenCalledWith('/api/v1/settings/retention-defaults', body, undefined)
  })

  it('switches the default off through the same PUT', async () => {
    await retentionInstanceService.updateInstancePolicy({ enabled: false })
    expect(api.put).toHaveBeenCalledWith('/api/v1/settings/retention-defaults', { enabled: false }, undefined)
  })

  it('reads the enable preview from its own path', async () => {
    await retentionInstanceService.previewInstanceEnable()
    expect(api.get).toHaveBeenCalledWith('/api/v1/settings/retention-defaults/enable-preview', undefined)
  })

  it('posts the typed phrase and the preview hash to enable', async () => {
    const body = { confirmation: 'enable retention for 7 projects', preview_hash: 'f'.repeat(64) }
    await expect(retentionInstanceService.enableInstancePolicy(body)).resolves.toEqual({ enabled: true })
    expect(api.post).toHaveBeenCalledWith('/api/v1/settings/retention-defaults/enable', body, undefined)
  })
})
