/**
 * Timeline notes (TRND-04), nested under their project:
 *
 *   GET    /api/v1/projects/{project_id}/timeline-notes?from=&to=[&suite=]
 *   POST   /api/v1/projects/{project_id}/timeline-notes            QA engineer+
 *   PATCH  /api/v1/projects/{project_id}/timeline-notes/{note_id}  author or QA lead+
 *   DELETE /api/v1/projects/{project_id}/timeline-notes/{note_id}  author or QA lead+
 *
 * Every id is a path segment, percent-encoded. The writes suppress the global
 * toast: the dialog / popover shows the server's refusal itself.
 */
import { deleteData, getData, patchData, postData } from './http'
import type { TimelineNote, TimelineNoteBody } from '@/types/timelineNotes'

export const notesUrl = (projectId: string) => `/api/v1/projects/${encodeURIComponent(projectId)}/timeline-notes`
const noteUrl = (projectId: string, noteId: string) => `${notesUrl(projectId)}/${encodeURIComponent(noteId)}`

export interface NotesWindow {
  /** `YYYY-MM-DD`, the window's first UTC day. */
  from: string
  /** `YYYY-MM-DD`, the window's last UTC day. */
  to: string
  /** The page's suite, sent only when one is selected. */
  suite?: string | null
}

/** The raw answer: the hook parses it (`parseTimelineNotes`). */
export function listTimelineNotes(projectId: string, { from, to, suite }: NotesWindow): Promise<unknown> {
  return getData<unknown>(notesUrl(projectId), {
    params: { from, to, ...(suite ? { suite } : {}) },
  })
}

export function createTimelineNote(projectId: string, body: TimelineNoteBody): Promise<TimelineNote> {
  return postData<TimelineNote, TimelineNoteBody>(notesUrl(projectId), body, { suppressToast: true })
}

export function updateTimelineNote(projectId: string, noteId: string, body: TimelineNoteBody): Promise<TimelineNote> {
  return patchData<TimelineNote, TimelineNoteBody>(noteUrl(projectId, noteId), body, { suppressToast: true })
}

export function deleteTimelineNote(projectId: string, noteId: string): Promise<void> {
  return deleteData<void>(noteUrl(projectId, noteId), { suppressToast: true })
}
