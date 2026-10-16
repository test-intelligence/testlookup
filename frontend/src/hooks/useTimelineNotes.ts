/**
 * TRND-04: the timeline notes of the active project over a UTC day window.
 *
 * Keyed `['timeline-notes', projectId, from, to, suite]`; no key (no request)
 * in All Projects or without a window. The answer goes through
 * `parseTimelineNotes`: a malformed one is "no notes", never a crash.
 *
 * `useTimelineNoteActions()` writes, then revalidates EVERY `timeline-notes`
 * key of the project, so the Trends hero and the Coverage history chart (two
 * windows, maybe two suites) both redraw.
 */
import { useCallback, useMemo } from 'react'
import useSWR, { useSWRConfig } from 'swr'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import {
  createTimelineNote,
  deleteTimelineNote,
  listTimelineNotes,
  updateTimelineNote,
} from '@/services/timelineNoteService'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { parseTimelineNotes, type TimelineNote, type TimelineNoteBody } from '@/types/timelineNotes'

export const TIMELINE_NOTES_KEY = 'timeline-notes'

export interface TimelineNotesState {
  notes: TimelineNote[]
  total: number
  truncated: boolean
  isLoading: boolean
  /** True when the read failed or its answer was malformed (nothing is drawn). */
  unavailable: boolean
  mutate: () => Promise<unknown>
}

const NO_NOTES: TimelineNote[] = []

function usePinnedProjectId(): string | null {
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  return activeProjectId && activeProjectId !== ALL_PROJECTS_ID ? activeProjectId : null
}

export function useTimelineNotes(range: { from: string; to: string } | null, suite: string | null): TimelineNotesState {
  const projectId = usePinnedProjectId()
  const suiteKey = suite?.trim() ?? ''
  const key = projectId && range ? ([TIMELINE_NOTES_KEY, projectId, range.from, range.to, suiteKey] as const) : null
  const { data, error, isLoading, mutate } = useSWR(
    key,
    ([, pid, from, to, s]: readonly [string, string, string, string, string]) =>
      listTimelineNotes(pid, { from, to, suite: s || null }),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND, revalidateOnFocus: false },
  )
  const parsed = useMemo(() => parseTimelineNotes(data), [data])
  return {
    notes: parsed?.items ?? NO_NOTES,
    total: parsed?.total ?? 0,
    truncated: parsed?.truncated ?? false,
    isLoading: Boolean(isLoading),
    unavailable: Boolean(error) || (data !== undefined && parsed === null),
    mutate: () => mutate(),
  }
}

export interface TimelineNoteActions {
  /** null in All Projects: nothing can be pinned there. */
  projectId: string | null
  create: (body: TimelineNoteBody) => Promise<TimelineNote>
  update: (noteId: string, body: TimelineNoteBody) => Promise<TimelineNote>
  remove: (noteId: string) => Promise<void>
}

export function useTimelineNoteActions(): TimelineNoteActions {
  const projectId = usePinnedProjectId()
  const { mutate } = useSWRConfig()
  const revalidate = useCallback(
    () =>
      mutate(
        (key) => Array.isArray(key) && key[0] === TIMELINE_NOTES_KEY && key[1] === projectId,
        undefined,
        { revalidate: true },
      ),
    [mutate, projectId],
  )
  const need = useCallback(() => {
    if (!projectId) throw new Error('Pick a project to pin a note.')
    return projectId
  }, [projectId])
  return useMemo(
    () => ({
      projectId,
      create: async (body) => {
        const note = await createTimelineNote(need(), body)
        await revalidate()
        return note
      },
      update: async (noteId, body) => {
        const note = await updateTimelineNote(need(), noteId, body)
        await revalidate()
        return note
      },
      remove: async (noteId) => {
        await deleteTimelineNote(need(), noteId)
        await revalidate()
      },
    }),
    [projectId, need, revalidate],
  )
}
