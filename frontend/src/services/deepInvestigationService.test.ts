import { beforeEach, describe, expect, it, vi } from 'vitest'

import { getData, postData } from './http'
import { deepInvestigationService } from './deepInvestigationService'

vi.mock('./http', () => ({
  getData: vi.fn(),
  postData: vi.fn(),
}))

describe('deepInvestigationService (W14 AGT-07)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getData).mockResolvedValue({})
    vi.mocked(postData).mockResolvedValue({})
  })

  it('triggerDeep posts exactly { mode } without a configuration (the body is unchanged)', async () => {
    await deepInvestigationService.triggerDeep('run-1')
    await deepInvestigationService.triggerDeep('run-1', 'offline')
    expect(vi.mocked(postData).mock.calls).toEqual([
      ['/api/v1/deep-investigate/run-1', { mode: 'deep' }],
      ['/api/v1/deep-investigate/run-1', { mode: 'offline' }],
    ])
  })

  it('triggerDeep carries a run configuration beside the mode', async () => {
    await deepInvestigationService.triggerDeep('run-1', 'deep', {
      failure_ids: ['f-1', 'f-2'], max_failures_analyzed: 2, model_tier: 'slm', evidence_sources: ['logs'],
    })
    expect(postData).toHaveBeenCalledWith('/api/v1/deep-investigate/run-1', {
      mode: 'deep', failure_ids: ['f-1', 'f-2'], max_failures_analyzed: 2, model_tier: 'slm', evidence_sources: ['logs'],
    })
  })

  it('the three reads call their full paths with their query values', async () => {
    await deepInvestigationService.getPreScan('run-1')
    await deepInvestigationService.getPreScan('run-1', 25)
    await deepInvestigationService.getHistory('p1')
    await deepInvestigationService.getHistory('p1', 3, 50)
    await deepInvestigationService.getEstimate('run-1')
    await deepInvestigationService.getEstimate('run-1', { selected_failures: 9, evidence_sources: ['logs', 'history'] })

    expect(vi.mocked(getData).mock.calls).toEqual([
      ['/api/v1/deep-investigate/run-1/pre-scan', { params: { limit: 10 } }],
      ['/api/v1/deep-investigate/run-1/pre-scan', { params: { limit: 25 } }],
      ['/api/v1/deep-investigate/history', { params: { project_id: 'p1', page: 1, size: 10 } }],
      ['/api/v1/deep-investigate/history', { params: { project_id: 'p1', page: 3, size: 50 } }],
      ['/api/v1/deep-investigate/run-1/estimate', { params: {} }],
      ['/api/v1/deep-investigate/run-1/estimate', { params: { selected_failures: 9, evidence_sources: ['logs', 'history'] } }],
    ])
  })
})
