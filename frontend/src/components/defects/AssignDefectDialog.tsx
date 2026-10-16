/**
 * W06 DEF-01: assign a defect to a person of its project and / or set its own
 * owner team. Only what changed is sent: the server records each change in the
 * activity log, and re-sending an unchanged value would record nothing anyway.
 */
import { useId, useMemo, useState } from 'react'
import toast from 'react-hot-toast'
import { updateDefect } from '@/services/defectService'
import type { DefectItem } from '@/types/analytics'
import type { DefectPatchBody } from '@/types/defectTriage'
import AssigneeSelect, { type AssigneeOption } from './AssigneeSelect'
import TriageDialog, { TriageConflict } from './TriageDialog'
import { describeTriageFailure } from './defectTriage'

const TEAM_MAX = 255

export interface AssignDefectDialogProps {
  /** Reads id, project_id, assignee_user_id, assignee_name, owner_team and owner_source. */
  defect: DefectItem
  onClose: () => void
  onUpdated: (row: DefectItem) => void
}

export default function AssignDefectDialog({ defect, onClose, onUpdated }: AssignDefectDialogProps) {
  const initialAssignee = defect.assignee_user_id ?? ''
  // Only the defect's OWN team is editable here; a team the project's
  // ownership rules give it is not the defect's, so the field starts empty.
  const initialTeam = defect.owner_source === 'defect' ? (defect.owner_team ?? '') : ''
  const [assignee, setAssignee] = useState(initialAssignee)
  const [assigneeName, setAssigneeName] = useState<string | null>(defect.assignee_name ?? null)
  const [team, setTeam] = useState(initialTeam)
  const [busy, setBusy] = useState(false)
  const [conflict, setConflict] = useState<string | null>(null)
  const id = useId()
  const teamId = `${id}-team`
  const assigneeId = `${id}-assignee`

  const currentAssignee = useMemo<AssigneeOption | null>(
    () => (defect.assignee_user_id
      ? { id: defect.assignee_user_id, name: defect.assignee_name ?? 'Current assignee' }
      : null),
    [defect.assignee_user_id, defect.assignee_name],
  )

  const assigneeChanged = assignee !== initialAssignee
  const teamChanged = team.trim() !== initialTeam.trim()
  const canSave = !busy && (assigneeChanged || teamChanged) && Boolean(defect.project_id)

  async function save() {
    if (!canSave || !defect.project_id) return
    setBusy(true)
    setConflict(null)
    const trimmedTeam = team.trim()
    const body: DefectPatchBody = {
      ...(assigneeChanged ? { assignee_user_id: assignee || null } : {}),
      ...(teamChanged ? { owner_team: trimmedTeam || null } : {}),
    }
    try {
      const response = await updateDefect(defect.project_id, defect.id, body, { suppressToast: true })
      onUpdated(response.defect)
      const parts: string[] = []
      if (assigneeChanged) {
        parts.push(assignee
          ? `Assigned to ${response.defect.assignee_name ?? assigneeName ?? 'the selected person'}`
          : 'Unassigned')
      }
      if (teamChanged) parts.push(trimmedTeam ? `Team set to ${trimmedTeam}` : 'Team cleared')
      toast.success(parts.join(' · '))
      onClose()
    } catch (err) {
      const failure = describeTriageFailure(err)
      if (failure.inline) setConflict(failure.message)
      else toast.error(failure.message)
      setBusy(false)
    }
  }

  return (
    <TriageDialog title="Assign defect" busy={busy} onClose={onClose} testId="assign-defect-dialog">
      <label htmlFor={assigneeId} className="mt-4 block text-xs font-medium text-[var(--color-text-secondary)]">
        Assignee
      </label>
      {defect.project_id && (
        <AssigneeSelect
          id={assigneeId}
          projectId={defect.project_id}
          value={assignee}
          currentAssignee={currentAssignee}
          disabled={busy}
          className="input mt-1 w-full"
          onChange={(next, name) => {
            setAssignee(next)
            setAssigneeName(name)
          }}
        />
      )}
      <label htmlFor={teamId} className="mt-4 block text-xs font-medium text-[var(--color-text-secondary)]">
        Team
      </label>
      <input
        id={teamId}
        type="text"
        maxLength={TEAM_MAX}
        value={team}
        onChange={(event) => setTeam(event.target.value)}
        disabled={busy}
        placeholder="e.g. payments-backend"
        className="input mt-1 w-full"
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
