/**
 * Settings › GitLab (W19 INT-05): what the GitLab integration now feeds besides MR notes and commit statuses.
 *
 * - Commit attribution: a run whose repo is this project's GitLab project gets its commit range (and changed
 *   files) from GitLab's compare, when the `gitlab` flag is on and the project has no matching GitHub target.
 * - Release sync: "Sync milestones now" pulls the project's milestones into Releases (keyed on the milestone, so a
 *   rename renames the release). A refusal shows the server's sentence (offline mode, flag off, no token).
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { RefreshCw } from 'lucide-react'

import { releasesService } from '@/services/releasesService'
import { extractErrorMessage } from '@/services/apiErrors'

export function GitLabSyncSection({ projectId, canEdit }: { projectId: string; canEdit: boolean }) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function sync() {
    setBusy(true)
    setResult(null)
    setError(null)
    try {
      const r = await releasesService.sync({ project_id: projectId, source: 'gitlab' })
      setResult(`${r.created} created, ${r.updated} updated${r.skipped ? `, ${r.skipped} skipped` : ''}.`)
    } catch (err) {
      setError(extractErrorMessage((err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail,
        'The milestone sync could not run'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card p-4 space-y-3" data-testid="gitlab-sync">
      <h3 className="text-sm font-semibold text-[var(--color-text)]">Release sync and commit attribution</h3>
      <p className="text-xs text-[var(--color-text-muted)]">
        Runs whose repository is this GitLab project get their commit range and changed files from GitLab (shown on
        the run&apos;s Changes tab and as failure suspects). Milestones can be pulled into{' '}
        <Link to="/releases" className="text-[var(--color-accent)] hover:underline">Releases</Link>; each keeps its GitLab link.
      </p>
      {canEdit && (
        <div className="flex items-center gap-3">
          <button type="button" className="btn-secondary btn-sm flex items-center gap-1.5" onClick={sync} disabled={busy}>
            <RefreshCw className="h-3.5 w-3.5" /> {busy ? 'Syncing…' : 'Sync milestones now'}
          </button>
          {result && <span className="text-xs text-[var(--color-text-secondary)]" data-testid="gitlab-sync-result">{result}</span>}
          {error && <span role="alert" className="text-xs text-[var(--ui-danger)]">{error}</span>}
        </div>
      )}
    </section>
  )
}
