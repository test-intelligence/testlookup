import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from './api'
import { deleteData } from './http'

vi.mock('./api', () => ({
  api: { delete: vi.fn() },
}))

describe('deleteData', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    ;(api.delete as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { ok: true } })
  })

  it('adds a JSON body without discarding safe request config', async () => {
    await deleteData<{ ok: boolean }, { reason: string }>(
      '/resource/1',
      { params: { project_id: 'project-1' }, headers: { 'X-Test': 'yes' } },
      { reason: 'Wrong link' },
    )

    expect(api.delete).toHaveBeenCalledWith('/resource/1', {
      params: { project_id: 'project-1' },
      headers: { 'X-Test': 'yes' },
      data: { reason: 'Wrong link' },
    })
  })

  it('preserves the legacy reasonless call shape', async () => {
    await deleteData('/resource/1', { params: { force: false } })

    expect(api.delete).toHaveBeenCalledWith('/resource/1', {
      params: { force: false },
    })
  })
})
