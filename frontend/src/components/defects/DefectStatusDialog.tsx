/**
 * W06 DEF-01: change a defect's status (Open / In progress / Resolved /
 * Invalid), with an optional note for the activity log. The server answers
 * the row as it now reads, and whether the linked Jira issue was moved too
 * (only with Jira online); a 409 (the test already has a live defect) is
 * shown here and the dialog stays open.
 */
import { useId, useState } from 'react'
import toast from 'react-hot-toast'
import { updateDefect } from '@/services/defectService'
import type { DefectItem } from '@/types/analytics'
import type { DefectPatchBody } from '@/types/defectTriage'
import type { InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import TriageDialog, { TriageConflict } from './TriageDialog'
import {
  DEFECT_STATUS_OPTIONS,
  defectStatusLabel,
  describeTriageFailure,
  normalizeDefectStatus,
} from './defectTriage'

const NOTE_MAX = 1000

export interface DefectStatusDialogProps {
  /** Reads id, project_id and resolution_status. */
  defect: DefectItem
  onClose: () => void
  onUpdated: (row: DefectItem) => void
  /** The defects table's slot (W27 PAN-04): one form per table; a typed form asks before it is replaced. */
  slot?: InlineSlot
}

/** The toast's tail for the Jira write-back (`skipped` and none say nothing). */
function writeBackSuffix(status: string | undefined): string {
  if (status === 'sent') return ' · Jira updated'
  if (status === 'not_sent') return ' · Jira not updated (offline)'
  return ''
}

export default function DefectStatusDialog({ defect, onClose, onUpdated, slot }: DefectStatusDialogProps) {
  const current = normalizeDefectStatus(defect.resolution_status)
  const [target, setTarget] = useState<string>(() =>
    DEFECT_STATUS_OPTIONS.some((o) => o.id === current) ? current : '',
  )
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [conflict, setConflict] = useState<string | null>(null)
  const id = useId()
  const noteId = `${id}-note`
  const canSave = !busy && target !== '' && target !== current && Boolean(defect.project_id)

  async function save() {
    if (!canSave || !defect.project_id) return
    setBusy(true)
    setConflict(null)
    const trimmedNote = note.trim()
    const body: DefectPatchBody = {
      resolution_status: target,
      ...(trimmedNote ? { note: trimmedNote } : {}),
    }
    try {
      const response = await updateDefect(defect.project_id, defect.id, body, { suppressToast: true })
      onUpdated(response.defect)
      const writeBack = response.write_back
      toast.success(`Status changed to ${defectStatusLabel(target)}${writeBackSuffix(writeBack?.status)}`)
      if (writeBack?.status === 'failed') {
        toast.error(`Jira not updated: ${writeBack.reason ?? 'unknown error'}`)
      }
      onClose()
    } catch (err) {
      const failure = describeTriageFailure(err)
      if (failure.inline) setConflict(failure.message)
      else toast.error(failure.message)
      setBusy(false)
    }
  }

  return (
    <TriageDialog title="Change status" busy={busy} onClose={onClose} testId="defect-status-dialog" slot={slot}>
      <fieldset className="mt-4 space-y-1.5">
        <legend className="mb-1 block text-xs font-medium text-[var(--color-text-secondary)]">Status</legend>
        {DEFECT_STATUS_OPTIONS.map((option) => (
          <label key={option.id} className="flex items-center gap-2 text-sm text-[var(--color-text)]">
            <input
              type="radio"
              name={`${id}-status`}
              value={option.id}
              checked={target === option.id}
              onChange={() => {
                setTarget(option.id)
                setConflict(null)
              }}
              disabled={busy}
            />
            {option.label}
          </label>
        ))}
      </fieldset>
      <label htmlFor={noteId} className="mt-4 block text-xs font-medium text-[var(--color-text-secondary)]">
        Note (recorded in the activity log)
      </label>
      <textarea
        id={noteId}
        maxLength={NOTE_MAX}
        rows={3}
        value={note}
        onChange={(event) => setNote(event.target.value)}
        disabled={busy}
        className="input mt-1 w-full resize-none"
      />
      <TriageConflict message={conflict} />
      <div className="mt-4 flex justify-end gap-2">
        <button type="button" className="btn-secondary" onClick={onClose} disabled={busy}>
          Cancel
        </button>
        <button type="button" className="btn-primary" onClick={() => void save()} disabled={!canSave}>
          {busy ? 'Saving…' : 'Save'}
        </button>
      </div>
    </TriageDialog>
  )
}
