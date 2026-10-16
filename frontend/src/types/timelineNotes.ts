/**
 * Timeline notes (TRND-04): a UTC day or day range of a project, up to 280
 * characters, optionally for one suite. Drawn on the Trends hero and on the
 * Coverage history chart (G-D reads these types).
 *
 * `parseTimelineNotes` is the defensive guard over
 * `GET /api/v1/projects/{id}/timeline-notes`: a malformed answer is `null`
 * (no notes drawn, never a crash); a single malformed item is dropped, the
 * rest kept.
 */

export interface TimelineNote {
  id: string
  project_id: string
  /** YYYY-MM-DD, the UTC day the note starts on. */
  starts_on: string
  /** YYYY-MM-DD, inclusive; equals `starts_on` for a one-day note. */
  ends_on: string
  text: string
  suite_name: string | null
  created_by_name: string | null
  /** For THIS reader: the author, or a QA lead and above. */
  can_edit: boolean
  created_at: string | null
  updated_at: string | null
}

export interface TimelineNoteList {
  items: TimelineNote[]
  total: number
  truncated: boolean
  start: string
  end: string
}

export interface TimelineNoteBody {
  starts_on?: string
  ends_on?: string
  text?: string
  suite_name?: string | null
}

/** The server's caps, mirrored for the dialog's counter (the server is the authority). */
export const NOTE_TEXT_MAX = 280
export const NOTE_RANGE_MAX_DAYS = 366

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function strOrNull(v: unknown): string | null | undefined {
  if (v === null || v === undefined) return null
  return typeof v === 'string' ? v : undefined
}

function parseNote(raw: unknown): TimelineNote | null {
  if (!isRecord(raw)) return null
  const { id, project_id, starts_on, ends_on, text, can_edit } = raw
  if (typeof id !== 'string' || id === '') return null
  if (typeof project_id !== 'string') return null
  if (typeof starts_on !== 'string' || !ISO_DAY.test(starts_on.slice(0, 10))) return null
  if (typeof ends_on !== 'string' || !ISO_DAY.test(ends_on.slice(0, 10))) return null
  if (typeof text !== 'string') return null
  const suite = strOrNull(raw.suite_name)
  const author = strOrNull(raw.created_by_name)
  const created = strOrNull(raw.created_at)
  const updated = strOrNull(raw.updated_at)
  if (suite === undefined || author === undefined || created === undefined || updated === undefined) {
    return null
  }
  const from = starts_on.slice(0, 10)
  const to = ends_on.slice(0, 10)
  return {
    id,
    project_id,
    starts_on: from,
    // A reversed range cannot be drawn; read it as the one day it starts on.
    ends_on: to < from ? from : to,
    text,
    suite_name: suite,
    created_by_name: author,
    can_edit: can_edit === true,
    created_at: created,
    updated_at: updated,
  }
}

export function parseTimelineNotes(raw: unknown): TimelineNoteList | null {
  if (!isRecord(raw)) return null
  if (!Array.isArray(raw.items)) return null
  const items: TimelineNote[] = []
  for (const it of raw.items) {
    const note = parseNote(it)
    if (note) items.push(note)
  }
  const total = typeof raw.total === 'number' && Number.isFinite(raw.total) ? raw.total : items.length
  return {
    items,
    total,
    truncated: raw.truncated === true,
    start: typeof raw.start === 'string' ? raw.start : '',
    end: typeof raw.end === 'string' ? raw.end : '',
  }
}

/**
 * The notes a chart draws, as the time-series model's `NoteInput`s
 * (`components/charts/timeSeriesModel.ts`; structurally typed so this module
 * imports nothing from the chart kit). Notes for another suite are dropped
 * here, so every host applies the same "a suite note shows only under that
 * suite" rule.
 */
export function noteInputsFor(
  notes: readonly TimelineNote[] | null | undefined,
  suite: string | null,
): { id: string; from: string; to: string; label: string; author: string | null }[] {
  if (!Array.isArray(notes)) return []
  return notes
    .filter((note) => noteShowsForSuite(note, suite))
    .map((note) => ({
      id: note.id,
      from: note.starts_on,
      to: note.ends_on,
      label: note.text,
      author: note.created_by_name,
    }))
}

/** Does a note show under the page's suite filter? A project-wide note always does. */
export function noteShowsForSuite(note: Pick<TimelineNote, 'suite_name'>, suite: string | null): boolean {
  if (note.suite_name == null || note.suite_name.trim() === '') return true
  if (suite == null) return false
  return note.suite_name.trim().toLowerCase() === suite.trim().toLowerCase()
}
