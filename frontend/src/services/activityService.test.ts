/**
 * W21d ACT-02: `listMyActivity` reads the caller's activity through the shared
 * client at its literal path, sends exactly the query it was given (no
 * `project_id` unless pinned, no empty keys), and hands the server's answer
 * back untouched (`total`, `projects`, the cursor, each item's `involvement`).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from './api'
import { listMyActivity, type MyActivityPage } from './activityService'
import { makeMyActivityItem, makeMyActivityPage } from '@/test/activityLiveMocks'

vi.mock('./api', () => ({ api: { get: vi.fn() } }))

beforeEach(() => {
  vi.clearAllMocks()
})

describe('listMyActivity', () => {
  it('reads the literal path with no params when given none', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: makeMyActivityPage() })
    await listMyActivity()
    expect(api.get).toHaveBeenCalledTimes(1)
    expect(api.get).toHaveBeenCalledWith('/api/v1/activity/mine', { params: {} })
  })

  it('passes the view, the window, the cursor and the project pin through as given', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: makeMyActivityPage() })
    const since = new Date(Date.now() - 7 * 86_400_000).toISOString()
    const query = {
      view: 'involving' as const,
      since,
      limit: 25,
      cursor: 'opaque-cursor',
      project_id: '00000000-0000-4000-8000-000000000123',
    }
    await listMyActivity(query)
    expect(api.get).toHaveBeenCalledWith('/api/v1/activity/mine', { params: query })
  })

  it('returns the server answer verbatim, total and involvement included', async () => {
    const page: MyActivityPage = makeMyActivityPage({
      items: [makeMyActivityItem({ involvement: 'mentioned' }), makeMyActivityItem({ involvement: 'assigned' })],
      total: 37,
      next_cursor: 'next-page',
      projects: null,
      view: 'all',
    })
    vi.mocked(api.get).mockResolvedValue({ data: page })
    const answer = await listMyActivity({ view: 'all' })
    expect(answer).toBe(page)
    expect(answer.total).toBe(37)
    expect(answer.items.map(item => item.involvement)).toEqual(['mentioned', 'assigned'])
  })

  it('lets a failed read reject (the hook turns it into an error, never an empty list)', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Request failed with status code 403'))
    await expect(listMyActivity({ project_id: '00000000-0000-4000-8000-000000000999' })).rejects.toThrow('403')
  })
})
