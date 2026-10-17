/**
 * W06 DEF-01: link a defect to an EXISTING Jira issue by its key, change the
 * key, or remove the link. With Jira online the server checks the key against
 * Jira before saving it; offline (the default) it saves the key without a
 * check and builds the link from the configured Jira domain. The link the
 * server builds is the only one shown, and only through `isSafeExternalUrl`;
 * keys and summaries are rendered as text.
 */
import { useId, useState } from 'react'
import { ExternalLink } from 'lucide-react'
import toast from 'react-hot-toast'
import { useJiraDefectMetadata } from '@/hooks/useJiraDefects'
import { updateDefect } from '@/services/defectService'
import type { DefectItem } from '@/types/analytics'
import type { DefectPatchResponse } from '@/types/defectTriage'
import { isSafeExternalUrl } from '@/utils/safeUrl'
import type { InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import TriageDialog, { TriageConflict } from './TriageDialog'
import {
  JIRA_KEY_MAX_LENGTH,
  JIRA_KEY_PATTERN,
  JIRA_LOCAL_ONLY_REASONS,
  describeTriageFailure,
  jiraReasonLabel,
} from './defectTriage'

export interface LinkJiraIssueDialogProps {
  /** Reads id, project_id, jira_ticket_id and jira_ticket_url. */
  defect: DefectItem
  onClose: () => void
  onUpdated: (row: DefectItem) => void
  /** The defects table's slot (W27 PAN-04): one form per table; a typed form asks before it is replaced. */
  slot?: InlineSlot
}

function linkedToast(response: DefectPatchResponse, sentKey: string): string {
  const jira = response.jira
  const key = jira?.jira_key ?? sentKey
  if (jira?.status === 'validated') {
    return `Linked to ${key} — checked against Jira${jira.summary ? `: "${jira.summary}"` : ''}`
  }
  if (jira?.status === 'saved_locally') return `Linked to ${key} — saved locally (${jiraReasonLabel(jira.reason)})`
  return `Linked to ${key}`
}

export default function LinkJiraIssueDialog({ defect, onClose, onUpdated, slot }: LinkJiraIssueDialogProps) {
  // Mounted only while the dialog is open, so the metadata is read only then.
  const { metadata } = useJiraDefectMetadata(defect.project_id)
  const currentKey = defect.jira_ticket_id?.trim() || null
  const currentUrl = defect.jira_ticket_url
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState<'save' | 'remove' | null>(null)
  const [conflict, setConflict] = useState<string | null>(null)
  const id = useId()
  const keyId = `${id}-key`

  const trimmed = key.trim()
  const valid = trimmed.length <= JIRA_KEY_MAX_LENGTH && JIRA_KEY_PATTERN.test(trimmed)
  const unchanged = currentKey !== null && trimmed === currentKey.toUpperCase()
  const canSave = busy === null && valid && !unchanged && Boolean(defect.project_id)
  const localOnly = metadata ? !metadata.available && JIRA_LOCAL_ONLY_REASONS.has(metadata.reason ?? '') : null

  async function send(jiraKey: string | null) {
    if (!defect.project_id) return
    setBusy(jiraKey === null ? 'remove' : 'save')
    setConflict(null)
    try {
      const response = await updateDefect(
        defect.project_id, defect.id, { jira_key: jiraKey }, { suppressToast: true },
      )
      onUpdated(response.defect)
      toast.success(jiraKey === null ? 'Jira link removed' : linkedToast(response, jiraKey))
      onClose()
    } catch (err) {
      const failure = describeTriageFailure(err)
      if (failure.inline) setConflict(failure.message)
      else toast.error(failure.message)
      setBusy(null)
    }
  }

  return (
    <TriageDialog title="Link a Jira issue" busy={busy !== null} onClose={onClose} testId="link-jira-dialog" slot={slot}>
      {currentKey && (
        <p className="mt-2 text-sm text-[var(--color-text-muted)]" data-current-jira-link="">
          Linked to{' '}
          {isSafeExternalUrl(currentUrl) ? (
            <a
              href={currentUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 font-mono text-[var(--color-accent)] hover:underline"
            >
              {currentKey}
              <ExternalLink aria-hidden="true" className="h-3 w-3" />
            </a>
          ) : (
            <span className="font-mono text-[var(--color-text)]">{currentKey}</span>
          )}
        </p>
      )}
      <label htmlFor={keyId} className="mt-4 block text-xs font-medium text-[var(--color-text-secondary)]">
        Jira issue key
      </label>
      <input
        id={keyId}
        type="text"
        autoFocus
        maxLength={JIRA_KEY_MAX_LENGTH}
        placeholder="ABC-123"
        value={key}
        onChange={(event) => {
          setKey(event.target.value.toUpperCase())
          setConflict(null)
        }}
        disabled={busy !== null}
        className="input mt-1 w-full font-mono"
      />
      {/* Save is disabled for a malformed key; say why (the server's 422 wording). */}
      {trimmed !== '' && !valid && (
        <p className="mt-2 text-xs text-[var(--status-failed)]" data-jira-key-invalid="">
          Not a Jira issue key (expected something like ABC-123).
        </p>
      )}
      {localOnly === false && (
        <p className="mt-2 text-xs text-[var(--color-text-muted)]" data-jira-check="online">
          The key is checked against Jira before it is saved.
        </p>
      )}
      {localOnly === true && (
        <p className="mt-2 text-xs text-[var(--color-text-muted)]" data-jira-check="local">
          Jira is not reachable from here ({jiraReasonLabel(metadata?.reason)}), so the key is saved without a check.
        </p>
      )}
      <TriageConflict message={conflict} />
      <div className="mt-4 flex items-center justify-end gap-2">
        {currentKey && (
          <button
            type="button"
            className="btn-ghost mr-auto text-[var(--status-failed)]"
            onClick={() => void send(null)}
            disabled={busy !== null}
          >
            {busy === 'remove' ? 'Removing…' : 'Remove link'}
          </button>
        )}
        <button type="button" className="btn-secondary" onClick={onClose} disabled={busy !== null}>
          Cancel
        </button>
        <button type="button" className="btn-primary" onClick={() => void send(trimmed)} disabled={!canSave}>
          {busy === 'save' ? 'Saving…' : 'Save'}
        </button>
      </div>
    </TriageDialog>
  )
}
