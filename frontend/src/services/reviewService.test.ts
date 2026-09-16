import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./http', () => ({
  getData: vi.fn(),
  postData: vi.fn(),
}))

import { getData, postData } from './http'
import { reviewService } from './reviewService'

describe('reviewService', () => {
  beforeEach(() => vi.clearAllMocks())

  it('encodes project ids and defaults the queue to pending reviews', async () => {
    await reviewService.list('project/with spaces')
    expect(getData).toHaveBeenCalledWith(
      '/api/v1/projects/project%2Fwith%20spaces/reviews',
      { params: { limit: 200, state: 'pending_review' } },
    )
  })

  it('omits the state filter when requesting the complete queue', async () => {
    await reviewService.list('project-1', 'all', 25)
    expect(getData).toHaveBeenCalledWith(
      '/api/v1/projects/project-1/reviews',
      { params: { limit: 25 } },
    )
  })

  it('keeps accept and reject on their distinct JWT-only actions', async () => {
    await reviewService.accept('review/1')
    await reviewService.accept('review 2', 'Evidence checked')
    await reviewService.reject('review 3', 'contradiction', 'Contradicts the run')

    expect(postData).toHaveBeenNthCalledWith(1, '/api/v1/reviews/review%2F1/accept', undefined)
    expect(postData).toHaveBeenNthCalledWith(
      2, '/api/v1/reviews/review%202/accept', { notes: 'Evidence checked' },
    )
    expect(postData).toHaveBeenNthCalledWith(
      3,
      '/api/v1/reviews/review%203/reject',
      { reason_code: 'contradiction', notes: 'Contradicts the run' },
    )
  })
})
