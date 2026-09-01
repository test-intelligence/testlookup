import { describe, expect, it, vi } from 'vitest'

const { mockGetData } = vi.hoisted(() => ({ mockGetData: vi.fn() }))

vi.mock('./http', () => ({
  getData: mockGetData,
  postData: vi.fn(),
}))

describe('runsService rich test-case detail', () => {
  it('requests the versioned rich-detail endpoint', async () => {
    mockGetData.mockResolvedValue({ contract: 'test-case-detail', schema_version: 1 })
    const { runsService } = await import('./runsService')

    await runsService.getEnrichedTest('run-1', 'test-1')

    expect(mockGetData).toHaveBeenCalledWith('/api/v1/runs/run-1/tests/test-1/rich-detail')
  })
})
