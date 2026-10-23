/**
 * TRND-04: the `Notes ({n})` chip a time chart's host puts in its frame's
 * toolbar (the Trends hero; the Coverage history chart). Drawn only when the
 * window has notes, so a chart without notes is exactly what it was.
 *
 * It opens a popover listing the window's notes — dates · text · author, all
 * React TEXT (note text and names are user input) — with Edit (when the host
 * passes `onEdit`) and Delete on the notes this reader may change
 * (`can_edit`: the author, or a QA lead and above; the server decides and
 * answers 403 otherwise). Delete asks once more before it calls the API.
 */
import { useRef, useState } from 'react'
import toast from 'react-hot-toast'
import { StickyNote } from 'lucide-react'
import { HeaderPopover } from '@/components/ui/HeaderPopover'
import { useTimelineNoteActions } from '@/hooks/useTimelineNotes'
import { refusalText } from '@/services/trendsReportService'
import type { TimelineNote } from '@/types/timelineNotes'
import { noteDateRange } from './timeSeriesModel'

export interface TimelineNotesChipProps {
  notes: readonly TimelineNote[]
  /** Offer Edit on an editable note (the host opens its dialog). Absent = no Edit. */
  onEdit?: (note: TimelineNote) => void
  /** Offer Delete on an editable note. Default true. */
  allowDelete?: boolean
  /** The server capped the list (`truncated`): say so under it. */
  truncated?: boolean
}

const CHIP =
  'inline-flex items-center gap-1 rounded border border-[var(--color-border-light)] bg-[var(--color-bg-card)] px-2 py-1 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)]'
const ACTION = 'rounded px-1.5 py-0.5 text-2xs font-medium hover:bg-[var(--color-bg-hover)] disabled:opacity-50'

export const notesChipLabel = (count: number) => `Notes (${count})`

export default function TimelineNotesChip({ notes, onEdit, allowDelete = true, truncated = false }: TimelineNotesChipProps) {
  const [open, setOpen] = useState(false)
  const [confirming, setConfirming] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const actions = useTimelineNoteActions()
  if (!Array.isArray(notes) || notes.length === 0) return null

  const remove = async (note: TimelineNote) => {
    setBusy(note.id)
    try {
      await actions.remove(note.id)
      toast.success('Note removed')
      setConfirming(null)
    } catch (error) {
      toast.error(await refusalText(error, 'The note could not be removed.'))
    } finally {
      setBusy(null)
    }
  }

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        data-timeline-notes-chip=""
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((was) => !was)}
        className={CHIP}
      >
        <StickyNote aria-hidden="true" className="h-3.5 w-3.5" />
        {notesChipLabel(notes.length)}
      </button>
      <HeaderPopover anchorRef={triggerRef} open={open} onClose={() => setOpen(false)} width={320} ariaLabel="Timeline notes" role="dialog">
        <ul data-timeline-notes-list="" className="flex max-h-80 flex-col gap-1 overflow-y-auto p-2">
          {notes.map((note) => {
            const dates = noteDateRange({ fromDay: note.starts_on, toDay: note.ends_on })
            return (
              <li key={note.id} data-timeline-note={note.id} className="rounded-md border border-[var(--color-border)] px-2.5 py-2 text-xs">
                <p className="m-0 text-[var(--color-text-secondary)]">
                  <span className="tabular-nums">{dates}</span>
                  {note.suite_name ? <span> · {note.suite_name}</span> : null}
                  <span> · {note.created_by_name ?? 'Unknown'}</span>
                </p>
                <p className="m-0 mt-0.5 break-words text-sm text-[var(--color-text)]">{note.text}</p>
                {note.can_edit && (onEdit || allowDelete) ? (
                  <div className="mt-1.5 flex items-center gap-1">
                    {onEdit ? (
                      <button
                        type="button"
                        data-timeline-note-edit={note.id}
                        className={`${ACTION} text-[var(--color-text)]`}
                        onClick={() => {
                          setOpen(false)
                          onEdit(note)
                        }}
                      >
                        Edit
                      </button>
                    ) : null}
                    {allowDelete ? (
                      confirming === note.id ? (
                        <>
                          <button
                            type="button"
                            data-timeline-note-confirm-delete={note.id}
                            disabled={busy === note.id}
                            className={`${ACTION} text-[var(--status-failed)]`}
                            onClick={() => void remove(note)}
                          >
                            Delete this note
                          </button>
                          <button type="button" className={`${ACTION} text-[var(--color-text-secondary)]`} onClick={() => setConfirming(null)}>
                            Keep
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          data-timeline-note-delete={note.id}
                          className={`${ACTION} text-[var(--status-failed)]`}
                          onClick={() => setConfirming(note.id)}
                        >
                          Delete
                        </button>
                      )
                    ) : null}
                  </div>
                ) : null}
              </li>
            )
          })}
        </ul>
        {truncated ? (
          <p className="m-0 border-t border-[var(--color-border)] px-3 py-1.5 text-2xs text-[var(--color-text-secondary)]">
            Showing the first {notes.length} notes of this window.
          </p>
        ) : null}
      </HeaderPopover>
    </>
  )
}
