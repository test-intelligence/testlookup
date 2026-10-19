/**
 * TRND-04: pin a note on the timeline — a UTC day or day range of the
 * project, up to 280 characters, optionally for the page's one suite — or
 * edit one (the author, or a QA lead and above; the server decides).
 *
 * Opened from the cadence card's `Pin a note`, from a mouse range on the
 * cadence strip (prefilled with those days) and from the gap callout's
 * `Pin a note for these days` (prefilled with the gap). The checks here mirror
 * the server's (1-280 characters, the end on or after the start, at most 366
 * days); the server is the authority and its refusal is shown as TEXT.
 *
 * W27b PAN-05b: the form renders IN THE PAGE (an `InlineFormSection`, region
 * `Pin a note` / `Edit note`), right after the card whose control opened it
 * (TrendsPage places it); no backdrop, no trap. Escape with focus inside
 * closes it (not mid-save), focus returns to the opener.
 */
import { useId, useState } from 'react'
import toast from 'react-hot-toast'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import type { InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { useTimelineNoteActions } from '@/hooks/useTimelineNotes'
import { refusalText } from '@/services/trendsReportService'
import { NOTE_RANGE_MAX_DAYS, NOTE_TEXT_MAX, type TimelineNote, type TimelineNoteBody } from '@/types/timelineNotes'
import { daysBetweenDayIso } from '@/utils/calendarDay'

export interface PinNoteDialogProps {
  /** A new note's days (the strip's range, the gap's dates, or today). Ignored when editing. */
  range: { from: string; to: string } | null
  /** Edit this note instead of pinning a new one. */
  note?: TimelineNote | null
  /** The page's selected suite: offers `Only for suite {suite}`. */
  suite: string | null
  onClose: () => void
  onSaved?: (note: TimelineNote) => void
  /** The page's note slot (one note form at a time; asks before typed text is replaced). */
  slot?: InlineSlot
}

export const PIN_NOTE_TITLE = 'Pin a note'
export const EDIT_NOTE_TITLE = 'Edit note'

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/
const FIELD =
  'min-h-8 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg-input)] px-2 text-sm text-[var(--color-text)]'
const LABEL = 'text-xs font-semibold text-[var(--color-text-secondary)]'

/** What is wrong with the form, in the server's words; `null` when it can be sent. */
export function noteFormError(from: string, to: string, text: string): string | null {
  const length = Array.from(text.trim()).length
  if (length < 1 || length > NOTE_TEXT_MAX) return 'A note is 1 to 280 characters.'
  if (!ISO_DAY.test(from) || !ISO_DAY.test(to)) return 'Pick the days the note covers.'
  const span = daysBetweenDayIso(to, from)
  if (span === null || span < 0 || span > NOTE_RANGE_MAX_DAYS) {
    return 'A note ends on or after the day it starts, at most 366 days later.'
  }
  return null
}

export default function PinNoteDialog({ range, note = null, suite, onClose, onSaved, slot }: PinNoteDialogProps) {
  const id = useId()
  const editing = note !== null
  const [from, setFrom] = useState(note?.starts_on ?? range?.from ?? '')
  const [to, setTo] = useState(note?.ends_on ?? range?.to ?? range?.from ?? '')
  const [text, setText] = useState(note?.text ?? '')
  const noteSuite = note?.suite_name ?? null
  // The suite the note can be scoped to: the page's, or (editing) the note's own.
  const offeredSuite = suite?.trim() ? suite.trim() : noteSuite
  const [onlySuite, setOnlySuite] = useState(editing ? noteSuite !== null : false)
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  const actions = useTimelineNoteActions()

  const problem = noteFormError(from, to, text)
  const count = Array.from(text).length
  const suiteName = onlySuite && offeredSuite ? offeredSuite : null

  const save = async () => {
    if (problem || busy) return
    setBusy(true)
    setRefusal(null)
    try {
      let saved: TimelineNote | null = null
      if (editing && note) {
        const body: TimelineNoteBody = {}
        if (from !== note.starts_on) body.starts_on = from
        if (to !== note.ends_on) body.ends_on = to
        if (text.trim() !== note.text) body.text = text.trim()
        if (suiteName !== noteSuite) body.suite_name = suiteName
        // Nothing changed: nothing to send.
        if (Object.keys(body).length > 0) saved = await actions.update(note.id, body)
        toast.success('Note updated')
      } else {
        saved = await actions.create({ starts_on: from, ends_on: to, text: text.trim(), suite_name: suiteName })
        toast.success('Note pinned')
      }
      if (saved) onSaved?.(saved)
      onClose()
    } catch (error) {
      setRefusal(await refusalText(error, 'The note could not be saved.'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <InlineFormSection
      title={editing ? EDIT_NOTE_TITLE : PIN_NOTE_TITLE}
      titleId={`${id}-title`}
      onClose={onClose}
      canClose={!busy}
      busy={busy}
      closeLabel="Close note dialog"
      slot={slot}
      dataAttributes={{ 'data-pin-note-dialog': editing ? 'edit' : 'create' }}
      footer={
        <>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="px-4 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-40"
          >
            Cancel
          </button>
          <button
            type="button"
            data-pin-note="save"
            onClick={() => void save()}
            disabled={problem !== null || busy}
            className="btn-primary flex items-center gap-2"
          >
            {busy && <LoadingSpinner size="sm" />}
            Save
          </button>
        </>
      }
    >
      {/* The note's text keeps a readable width; the section spans its column. */}
      <div className="max-w-2xl">
        <div className="grid grid-cols-2 gap-3">
          <div className="flex flex-col gap-1">
            <label htmlFor={`${id}-from`} className={LABEL}>
              From (UTC)
            </label>
            <input id={`${id}-from`} data-pin-note="from" type="date" value={from} onChange={(e) => setFrom(e.target.value)} className={FIELD} />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor={`${id}-to`} className={LABEL}>
              To (UTC)
            </label>
            <input id={`${id}-to`} data-pin-note="to" type="date" value={to} onChange={(e) => setTo(e.target.value)} className={FIELD} />
          </div>
        </div>
        <div className="mt-3 flex flex-col gap-1">
          <label htmlFor={`${id}-text`} className={LABEL}>
            Note
          </label>
          <textarea
            id={`${id}-text`}
            data-pin-note="text"
            value={text}
            maxLength={NOTE_TEXT_MAX}
            rows={3}
            onChange={(e) => setText(e.target.value)}
            className={`${FIELD} resize-y py-1.5`}
            aria-describedby={`${id}-count`}
            autoFocus
          />
          <span id={`${id}-count`} data-pin-note="count" className="self-end text-[11px] tabular-nums text-[var(--color-text-secondary)]">
            {count} / {NOTE_TEXT_MAX}
          </span>
        </div>
        {offeredSuite ? (
          <label className="mt-2 flex items-center gap-2 text-[13px] text-[var(--color-text)]">
            <input type="checkbox" data-pin-note="suite" checked={onlySuite} onChange={(e) => setOnlySuite(e.target.checked)} />
            <span>
              Only for suite <span className="font-mono">{offeredSuite}</span>
            </span>
          </label>
        ) : null}
        {problem && text.trim() !== '' ? (
          <p data-pin-note-problem="" className="mt-2 text-xs text-[var(--status-broken)]">
            {problem}
          </p>
        ) : null}
        {refusal ? (
          <p role="alert" data-pin-note-refusal="" className="mt-2 text-xs text-[var(--status-failed)] break-words">
            {refusal}
          </p>
        ) : null}
      </div>
    </InlineFormSection>
  )
}
