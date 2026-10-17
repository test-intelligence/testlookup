/**
 * NTF-04: `parseDigestScope` is the guard over a digest's `scope` (the preview
 * renders without its scope block when it is null), and the two widened
 * calls: the preview asks for `saved_view_id` only when a view is chosen, and
 * a PATCH may set or clear the view.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn(), post: vi.fn(), delete: vi.fn() }))
vi.mock('./api', () => ({ api }))

import { parseDigestScope, previewDigest, updateSubscription } from './digestService'

const VALID = {
  source: 'saved_view',
  view_id: 'v1',
  view_name: 'Checkout regressions',
  release_ids: ['r1'],
  release_names: ['2026.10'],
  suites: ['checkout'],
  window_days: 7,
  dropped_release_count: 2,
}

describe('parseDigestScope', () => {
  it.each([
    ['undefined', undefined],
    ['null', null],
    ['an empty object', {}],
    ['a string', 'saved_view'],
    ['an array', [VALID]],
    ['source a number', { ...VALID, source: 3 }],
    ['source empty', { ...VALID, source: '' }],
  ])('%s → null', (_n, raw) => {
    expect(parseDigestScope(raw)).toBeNull()
  })

  it('a valid answer reads as itself', () => {
    expect(parseDigestScope(VALID)).toEqual(VALID)
  })

  it('wrong-typed fields read as absent, never crash', () => {
    expect(
      parseDigestScope({
        source: 'saved_view',
        view_id: 5,
        view_name: { x: 1 },
        release_ids: 'r1',
        release_names: [1, '2026.10', null],
        suites: null,
        window_days: '7',
        dropped_release_count: -1,
      }),
    ).toEqual({
      source: 'saved_view',
      view_id: null,
      view_name: null,
      release_ids: [],
      release_names: ['2026.10'],
      suites: [],
      window_days: null,
      dropped_release_count: 0,
    })
    expect(parseDigestScope({ source: 'project' })).toEqual({
      source: 'project', view_id: null, view_name: null, release_ids: [], release_names: [], suites: [], window_days: null, dropped_release_count: 0,
    })
    expect(parseDigestScope({ ...VALID, window_days: 2.5, dropped_release_count: 1.5 })).toMatchObject({ window_days: null, dropped_release_count: 0 })
  })
})

describe('the widened calls', () => {
  beforeEach(() => {
    api.get.mockReset().mockResolvedValue({ data: {} })
    api.patch.mockReset().mockResolvedValue({ data: {} })
  })

  it('previewDigest sends saved_view_id only when a view is given', async () => {
    await previewDigest('p1', 'daily')
    expect(api.get).toHaveBeenLastCalledWith('/api/v1/digests/preview', { params: { project_id: 'p1', period: 'daily' } })
    await previewDigest('p1', undefined, 'v1')
    expect(api.get).toHaveBeenLastCalledWith('/api/v1/digests/preview', {
      params: { project_id: 'p1', period: 'weekly', saved_view_id: 'v1' },
    })
  })

  it('updateSubscription carries saved_view_id, null included', async () => {
    await updateSubscription('s1', { saved_view_id: null })
    expect(api.patch).toHaveBeenLastCalledWith('/api/v1/digests/subscriptions/s1', { saved_view_id: null })
    await updateSubscription('s1', { scope_type: 'release', scope_value: 'r1' })
    expect(api.patch).toHaveBeenLastCalledWith('/api/v1/digests/subscriptions/s1', { scope_type: 'release', scope_value: 'r1' })
  })
})
