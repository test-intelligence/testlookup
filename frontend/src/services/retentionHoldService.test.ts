/**
 * W22a ACT-05: the retention-hold service.
 *
 * The request paths are the backend contract (the SPA wire-contract guard
 * reads them), so each one is pinned here in full, with its params / body.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('./api', () => ({ api: apiMock }))

import { listRetentionHolds, placeRetentionHold, releaseRetentionHold } from './retentionHoldService'
import { holdList, holdRow } from '@/test/retentionHoldMocks'

const PROJECT = '00000000-0000-4000-8000-0000000000a1'
const HOLD = '00000000-0000-4000-8000-0000000000c3'

describe('retentionHoldService', () => {
  beforeEach(() => {
    apiMock.get.mockReset()
    apiMock.post.mockReset()
  })

  it('lists one project with no params when none are given', async () => {
    const answer = holdList([holdRow()])
    apiMock.get.mockResolvedValue({ data: answer })
    await expect(listRetentionHolds(PROJECT)).resolves.toBe(answer)
    expect(apiMock.get).toHaveBeenCalledWith(`/api/v1/projects/${PROJECT}/retention-holds`, { params: {} })
  })

  it('sends state, entity_type, entity_id and limit by their wire names', async () => {
    apiMock.get.mockResolvedValue({ data: holdList([]) })
    await listRetentionHolds(PROJECT, {
      state: 'all',
      entityType: 'release',
      entityId: '00000000-0000-4000-8000-0000000000b2',
      limit: 200,
    })
    expect(apiMock.get).toHaveBeenCalledWith(`/api/v1/projects/${PROJECT}/retention-holds`, {
      params: {
        state: 'all',
        entity_type: 'release',
        entity_id: '00000000-0000-4000-8000-0000000000b2',
        limit: 200,
      },
    })
  })

  it('places a hold with the body as given', async () => {
    const created = holdRow({ entity_type: 'project', entity_id: null })
    apiMock.post.mockResolvedValue({ data: created })
    const body = { entity_type: 'project' as const, reason: 'Litigation sentinel', expires_at: null }
    await expect(placeRetentionHold(PROJECT, body)).resolves.toBe(created)
    expect(apiMock.post).toHaveBeenCalledWith(`/api/v1/projects/${PROJECT}/retention-holds`, body)
  })

  it('releases one hold with the reason as the whole body', async () => {
    const released = holdRow({ id: HOLD, state: 'released', release_reason: 'Audit closed' })
    apiMock.post.mockResolvedValue({ data: released })
    await expect(releaseRetentionHold(PROJECT, HOLD, 'Audit closed')).resolves.toBe(released)
    expect(apiMock.post).toHaveBeenCalledWith(
      `/api/v1/projects/${PROJECT}/retention-holds/${HOLD}/release`,
      { reason: 'Audit closed' },
    )
  })

  it('lets a refusal reach the caller (the inline form shows its sentence)', async () => {
    const refusal = { response: { status: 403, data: { detail: { code: 'interactive_login_required' } } } }
    apiMock.post.mockRejectedValue(refusal)
    await expect(releaseRetentionHold(PROJECT, HOLD, 'Audit closed')).rejects.toBe(refusal)
  })
})
