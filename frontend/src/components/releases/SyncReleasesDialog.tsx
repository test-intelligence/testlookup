/**
 * Pull releases in from GitHub milestones or Jira fix versions (REL-08).
 *
 * `POST /api/v1/releases/sync` writes identity only (name, external id, link);
 * phases, gate policy and run attribution stay TestLookup's. While the
 * deployment is offline (`AI_OFFLINE_MODE`) or the integration has no
 * credential, the server answers 503 with its own sentence; a network failure
 * reaching the source is a 502. The dialog shows that sentence verbatim (the
 * request opts out of the global toast) and, for a 503, the way to Settings.
 * Nothing changes on a failure.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { X } from 'lucide-react'
import toast from 'react-hot-toast'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useModalFocus } from '@/hooks/useModalFocus'
import { releasesService } from '@/services/releasesService'
import { extractErrorMessage } from '@/services/apiErrors'

export interface SyncReleasesDialogProps {
  projectId: string
  onClose: () => void
  /** A sync succeeded: refresh the list (and close). */
  onDone: () => void
}

type Source = 'github' | 'jira'

const SOURCE_NAME: Record<Source, string> = { github: 'GitHub', jira: 'Jira' }

interface SyncFailure {
  status: number | null
  detail: string
}

export default function SyncReleasesDialog({ projectId, onClose, onDone }: SyncReleasesDialogProps) {
  const [source, setSource] = useState<Source>('github')
  const [jiraKey, setJiraKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<SyncFailure | null>(null)
  const dialogRef = useModalFocus({ onClose, canClose: !busy })

  async function sync() {
    setBusy(true)
    setFailure(null)
    const key = jiraKey.trim()
    try {
      const result = await releasesService.sync({
        project_id: projectId,
        source,
        ...(source === 'jira' && key ? { jira_project_key: key } : {}),
      })
      const name = SOURCE_NAME[(result.source as Source)] ?? SOURCE_NAME[source]
      toast.success(`Synced from ${name}: ${result.created} created, ${result.updated} updated, ${result.skipped} skipped.`)
      onDone()
    } catch (err: unknown) {
      const e = err as { message?: string; response?: { status?: number; data?: { detail?: unknown } } }
      setFailure({
        status: e.response?.status ?? null,
        detail: extractErrorMessage(e.response?.data?.detail, e.message || 'The sync failed.'),
      })
    } finally {
      setBusy(false)
    }
  }

  const choose = (next: Source) => { setSource(next); setFailure(null) }

  return (
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-labelledby="sync-releases-title"
      data-sync-releases-dialog=""
      className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--color-bg)]/60"
      onClick={() => { if (!busy) onClose() }}
    >
      <div
        className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-6 w-full max-w-md shadow-2xl"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h2 id="sync-releases-title" className="text-base font-semibold text-[var(--color-text)]">Sync releases</h2>
          <button
            type="button"
            aria-label="Close sync releases dialog"
            onClick={onClose}
            disabled={busy}
            className="text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-40"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <p className="text-[12.5px] text-[var(--color-text-muted)] mb-4">
          Pulls release names and links from the source. Phases, gate policy and run attribution stay in TestLookup.
        </p>
        <fieldset className="space-y-2" disabled={busy}>
          <legend className="sr-only">Source</legend>
          <label className="flex items-center gap-2 text-sm text-[var(--color-text)]">
            <input type="radio" name="sync-source" value="github" checked={source === 'github'} onChange={() => choose('github')} />
            GitHub milestones
          </label>
          <label className="flex items-center gap-2 text-sm text-[var(--color-text)]">
            <input type="radio" name="sync-source" value="jira" checked={source === 'jira'} onChange={() => choose('jira')} />
            Jira fix versions
          </label>
        </fieldset>
        {source === 'jira' && (
          <div className="mt-3">
            <label htmlFor="sync-jira-key" className="block text-xs text-[var(--color-text-muted)] mb-1">Jira project key</label>
            <input
              id="sync-jira-key"
              value={jiraKey}
              onChange={e => setJiraKey(e.target.value)}
              placeholder="Uses the default key when empty"
              disabled={busy}
              className="input w-full"
            />
          </div>
        )}
        {failure && (
          <div
            role="alert"
            data-sync-error=""
            data-status={failure.status ?? ''}
            className="mt-4 rounded-md border border-[var(--status-failed-bd)] bg-[var(--status-failed-bg)]/10 px-3 py-2 text-[12.5px] text-[var(--status-failed)]"
          >
            <p className="whitespace-pre-wrap break-words">{failure.detail}</p>
            {failure.status === 503 && (
              <Link
                to="/settings/integrations"
                onClick={onClose}
                className="mt-1 inline-block font-medium text-[var(--color-accent)] hover:underline"
              >
                Open Settings › Integrations
              </Link>
            )}
          </div>
        )}
        <div className="flex gap-3 justify-end mt-5">
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
            onClick={() => void sync()}
            disabled={busy}
            className="px-4 py-2 text-sm bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] disabled:opacity-50 text-[var(--color-btn-primary-text)] rounded-lg font-medium flex items-center gap-2"
          >
            {busy && <LoadingSpinner size="sm" />}
            Sync
          </button>
        </div>
      </div>
    </div>
  )
}
