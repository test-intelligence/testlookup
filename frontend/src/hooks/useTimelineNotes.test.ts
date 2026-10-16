/**
 * TRND-04: the notes hook asks one project's window (no request in All
 * Projects or without a window), hands back parsed notes (garbage = none),
 * and every write revalidates every `timeline-notes` key of the project.
 */
import { createElement, type ReactNode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const service = vi.hoisted(() => ({
  listTimelineNotes: vi.fn(),
  createTimelineNote: vi.fn(),
  updateTimelineNote: vi.fn(),
  deleteTimelineNote: vi.fn(),
}))
vi.mock('@/services/timelineNoteService', () => service)

const store = vi.hoisted(() => ({ projectId: 'p1' as string | null }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: (s: { activeProjectId: string | null }) => unknown) => selector({ activeProjectId: store.projectId }),
}))

import { useTimelineNoteActions, useTimelineNotes } from './useTimelineNotes'

const NOTE = {
  id: 'n1',
  project_id: 'p1',
  starts_on: '2026-09-10',
  ends_on: '2026-09-10',
  text: 'release freeze',
  suite_name: null,
  created_by_name: 'Ada',
  can_edit: true,
  created_at: null,
  updated_at: null,
}
const ANSWER = { items: [NOTE], total: 1, truncated: false, start: '2026-09-05', end: '2026-09-18' }
const RANGE = { from: '2026-09-05', to: '2026-09-18' }

function wrapper() {
  const cache = new Map()
  return ({ children }: { children: ReactNode }) =>
    createElement(SWRConfig, { value: { provider: () => cache, dedupingInterval: 0 } }, children)
}

describe('useTimelineNotes', () => {
  beforeEach(() => {
    store.projectId = 'p1'
    for (const fn of Object.values(service)) fn.mockReset()
    service.listTimelineNotes.mockResolvedValue(ANSWER)
  })

  it("asks the project's window (suite only when one is selected) and returns the notes", async () => {
    const { result } = renderHook(() => useTimelineNotes(RANGE, null), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.notes).toHaveLength(1))
    expect(service.listTimelineNotes).toHaveBeenCalledWith('p1', { from: '2026-09-05', to: '2026-09-18', suite: null })
    renderHook(() => useTimelineNotes(RANGE, 'Checkout'), { wrapper: wrapper() })
    await waitFor(() => expect(service.listTimelineNotes).toHaveBeenCalledWith('p1', { ...RANGE, suite: 'Checkout' }))
  })

  it('asks nothing in All Projects or without a window', () => {
    store.projectId = '__ALL__'
    const all = renderHook(() => useTimelineNotes(RANGE, null), { wrapper: wrapper() })
    store.projectId = 'p1'
    const none = renderHook(() => useTimelineNotes(null, null), { wrapper: wrapper() })
    expect(service.listTimelineNotes).not.toHaveBeenCalled()
    expect(all.result.current.notes).toEqual([])
    expect(none.result.current.notes).toEqual([])
  })

  it('a malformed answer is no notes and "unavailable", never a crash', async () => {
    service.listTimelineNotes.mockResolvedValue({ items: 'nope' })
    const { result } = renderHook(() => useTimelineNotes(RANGE, null), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.unavailable).toBe(true))
    expect(result.current.notes).toEqual([])
  })
})

describe('useTimelineNoteActions', () => {
  beforeEach(() => {
    store.projectId = 'p1'
    for (const fn of Object.values(service)) fn.mockReset()
    service.listTimelineNotes.mockResolvedValue(ANSWER)
    service.createTimelineNote.mockResolvedValue(NOTE)
    service.updateTimelineNote.mockResolvedValue(NOTE)
    service.deleteTimelineNote.mockResolvedValue(undefined)
  })

  it('a write revalidates every timeline-notes window of the project', async () => {
    const shared = wrapper()
    const { result } = renderHook(
      () => ({ hero: useTimelineNotes(RANGE, null), history: useTimelineNotes({ from: '2026-06-20', to: '2026-09-18' }, null), actions: useTimelineNoteActions() }),
      { wrapper: shared },
    )
    await waitFor(() => expect(service.listTimelineNotes).toHaveBeenCalledTimes(2))
    await act(async () => {
      await result.current.actions.create({ starts_on: '2026-09-10', text: 'release freeze' })
    })
    expect(service.createTimelineNote).toHaveBeenCalledWith('p1', { starts_on: '2026-09-10', text: 'release freeze' })
    await waitFor(() => expect(service.listTimelineNotes).toHaveBeenCalledTimes(4))
    await act(async () => {
      await result.current.actions.remove('n1')
    })
    expect(service.deleteTimelineNote).toHaveBeenCalledWith('p1', 'n1')
    await waitFor(() => expect(service.listTimelineNotes).toHaveBeenCalledTimes(6))
  })

  it('All Projects cannot write', async () => {
    store.projectId = '__ALL__'
    const { result } = renderHook(() => useTimelineNoteActions(), { wrapper: wrapper() })
    expect(result.current.projectId).toBeNull()
    await expect(result.current.create({ starts_on: '2026-09-10', text: 'x' })).rejects.toThrow(/Pick a project/)
    expect(service.createTimelineNote).not.toHaveBeenCalled()
  })
})
