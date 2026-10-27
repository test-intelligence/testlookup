/**
 * Pull releases in from GitHub milestones, GitLab milestones (W19 INT-05, offered when the project has a
 * GitLab integration) or Jira fix versions (REL-08).
 *
 * `POST /api/v1/releases/sync` writes identity only (name, external id, link);
 * phases, gate policy and run attribution stay TestLookup's. While the
 * deployment is offline (`AI_OFFLINE_MODE`) or the integration has no
 * credential, the server answers 503 with its own sentence; a network failure
 * reaching the source is a 502. The form shows that sentence verbatim (the
 * request opts out of the global toast) and, for a 503, the way to Settings.
 * Nothing changes on a failure.
 *
 * It renders IN THE PAGE (W27 PAN-02): an `InlineFormSection` under the
 * releases' health banner, not a modal over the page. Its name, heading id,
 * `data-sync-releases-dialog` and close label are the dialog's, kept; Cancel,
 * the X and Escape inside it close it (not mid-sync), as before.
 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import toast from 'react-hot-toast'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import type { InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { releasesService } from '@/services/releasesService'
import { gitlabIntegrationService } from '@/services/gitlabIntegrationService'
import { extractErrorMessage } from '@/services/apiErrors'

export interface SyncReleasesDialogProps {
  projectId: string
  onClose: () => void
  /** A sync succeeded: refresh the list (and close). */
  onDone: () => void
  /** The page's form slot: one open form at a time under the page header. */
  slot?: InlineSlot
}

type Source = 'github' | 'jira' | 'gitlab'

const SOURCE_NAME: Record<Source, string> = { github: 'GitHub', jira: 'Jira', gitlab: 'GitLab' }

interface SyncFailure {
  status: number | null
  detail: string
}

export default function SyncReleasesDialog({ projectId, onClose, onDone, slot }: SyncReleasesDialogProps) {
  const [source, setSource] = useState<Source>('github')
  const [jiraKey, setJiraKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<SyncFailure | null>(null)
  // W19 INT-05: GitLab is offered only when the project has a GitLab integration (a 404 = none).
  const [hasGitLab, setHasGitLab] = useState(false)
  useEffect(() => {
    let live = true
    gitlabIntegrationService.get(projectId).then(() => { if (live) setHasGitLab(true) }).catch(() => { if (live) setHasGitLab(false) })
    return () => { live = false }
  }, [projectId])

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
    <InlineFormSection
      title="Sync releases"
      titleId="sync-releases-title"
      closeLabel="Close sync releases dialog"
      onClose={onClose}
      canClose={!busy}
      slot={slot}
      dataAttributes={{ 'data-sync-releases-dialog': '' }}
      footer={(
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
            onClick={() => void sync()}
            disabled={busy}
            className="btn-primary flex items-center gap-2"
          >
            {busy && <LoadingSpinner size="sm" />}
            Sync
          </button>
        </>
      )}
    >
      <div className="max-w-2xl">
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
          {hasGitLab && (
            <label className="flex items-center gap-2 text-sm text-[var(--color-text)]">
              <input type="radio" name="sync-source" value="gitlab" checked={source === 'gitlab'} onChange={() => choose('gitlab')} />
              GitLab milestones
            </label>
          )}
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
      </div>
    </InlineFormSection>
  )
}
