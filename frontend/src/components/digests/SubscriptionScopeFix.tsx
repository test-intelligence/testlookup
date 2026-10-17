/**
 * NTF-03 / NTF-04: a subscription row's scope, fixed in the row (no modal).
 *
 *  - A row the server says cannot deliver (`scope_problem` "unscoped" /
 *    "release_missing") shows a badge and the fix inline: the release select
 *    (Per Release) or the suite input (Per Suite), and Save — a PATCH of
 *    `{ scope_type, scope_value }`. The release list is the top bar
 *    project's, so the select is offered only when the top bar shows the
 *    row's own project (another project's release would be refused).
 *  - A Daily or Weekly row of a project gets **Change view**: the project's
 *    report views, `No view (whole project)` first, and Save — a PATCH of
 *    `saved_view_id` (or `null`). A view problem ("saved_view_deleted" /
 *    "saved_view_unavailable") is said beside it.
 *
 * A refused PATCH shows the server's 422 text in the row. Names are React text.
 */
import { useState } from 'react'
import toast from 'react-hot-toast'
import { updateSubscription, type DigestSubscription } from '@/services/digestService'
import { extractErrorMessage } from '@/services/apiErrors'
import DigestReleaseSelect from './DigestReleaseSelect'
import DigestViewPicker, { type ViewSource } from './DigestViewPicker'
import { parseSubscriptionScope, problemKind, scopeProblemText, viewAppliesTo } from './digestScopeModel'

type RequestError = { message?: string; response?: { data?: { detail?: unknown } } }

const BTN =
  'px-3 py-1 text-xs rounded bg-[var(--color-btn-primary-bg)] text-[var(--color-btn-primary-text)] hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed'
const LINK_BTN = 'text-xs text-[var(--color-text-secondary)] underline hover:text-[var(--color-text)]'

export default function SubscriptionScopeFix({
  sub,
  releases,
  topBarProjectId,
  views,
  onSaved,
}: {
  sub: DigestSubscription
  /** The top bar project's releases (the cached top-bar list). */
  releases: readonly unknown[]
  /** null in All Projects. */
  topBarProjectId: string | null
  views: ViewSource
  onSaved: () => void
}) {
  const scope = parseSubscriptionScope(sub)
  const kind = problemKind(scope.problem, sub.schedule)
  const problemText = scopeProblemText(scope.problem, sub.schedule)

  const [releaseId, setReleaseId] = useState('')
  const [suite, setSuite] = useState('')
  const [changingView, setChangingView] = useState(false)
  const [viewId, setViewId] = useState(sub.saved_view_id ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const patch = async (payload: Parameters<typeof updateSubscription>[1], done: string) => {
    setSaving(true)
    setError(null)
    try {
      await updateSubscription(sub.id, payload)
      toast.success(done)
      setChangingView(false)
      onSaved()
    } catch (err: unknown) {
      const e = err as RequestError
      setError(extractErrorMessage(e?.response?.data?.detail, 'The subscription could not be updated.'))
    } finally {
      setSaving(false)
    }
  }

  const sameProject = !!sub.project_id && sub.project_id === topBarProjectId
  // The views offered are the top bar project's: only a row of that project can change its view.
  const viewRow = viewAppliesTo(sub.schedule) && !!sub.project_id
  const canChangeView = viewRow && sameProject

  if (!kind && !canChangeView) return null

  return (
    <div className="mt-2 space-y-1.5" data-scope-fix={sub.id}>
      {problemText && (
        <span
          data-scope-problem={scope.problem ?? ''}
          className={`inline-block rounded px-2 py-0.5 text-xs font-medium border ${
            kind === 'delivery'
              ? 'border-[var(--status-broken-bd)] bg-[var(--status-broken-bg)] text-[var(--status-broken)]'
              : 'border-[var(--color-border)] bg-[var(--status-skipped-bg)]/40 text-[var(--status-skipped)]'
          }`}
        >
          {problemText}
        </span>
      )}

      {kind === 'delivery' && sub.schedule === 'PER_RELEASE' && (
        sameProject ? (
          <div className="flex flex-wrap items-center gap-2">
            <DigestReleaseSelect releases={releases} value={releaseId} onChange={setReleaseId} />
            <button
              type="button"
              className={BTN}
              disabled={saving || !releaseId}
              onClick={() => void patch({ scope_type: 'release', scope_value: releaseId }, 'Subscription updated')}
            >
              Save
            </button>
          </div>
        ) : (
          <p className="text-xs text-[var(--color-text-muted)]">Pick this digest&apos;s project in the top bar to choose a release.</p>
        )
      )}

      {kind === 'delivery' && sub.schedule === 'PER_SUITE' && (
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={suite}
            onChange={e => setSuite(e.target.value)}
            placeholder="Suite name"
            aria-label="Suite name"
            className="bg-[var(--color-bg-card)] text-[var(--color-text)] rounded px-3 py-1.5 text-sm w-[220px]"
          />
          <button
            type="button"
            className={BTN}
            disabled={saving || !suite.trim()}
            onClick={() => void patch({ scope_type: 'suite', scope_value: suite.trim() }, 'Subscription updated')}
          >
            Save
          </button>
        </div>
      )}

      {kind === 'view' && viewRow && !sameProject && (
        <p className="text-xs text-[var(--color-text-muted)]">Pick this digest&apos;s project in the top bar to change its view.</p>
      )}

      {canChangeView && (
        changingView ? (
          <div className="flex flex-wrap items-center gap-2" data-change-view="">
            <DigestViewPicker source={views} projectId={sub.project_id} value={viewId} onChange={setViewId} />
            <button
              type="button"
              className={BTN}
              disabled={saving || !views.requested || views.loading || views.error}
              onClick={() => void patch({ saved_view_id: viewId || null }, 'Saved view changed')}
            >
              Save
            </button>
            <button type="button" className={LINK_BTN} onClick={() => { setChangingView(false); setError(null) }}>
              Cancel
            </button>
          </div>
        ) : (
          <button
            type="button"
            className={LINK_BTN}
            onClick={() => {
              setChangingView(true)
              // The views are asked for now, not on page load.
              if (!views.requested) views.request()
            }}
          >
            Change view
          </button>
        )
      )}

      {error && (
        <p role="alert" className="text-xs text-[var(--status-failed)]" data-scope-fix-error="">
          {error}
        </p>
      )}
    </div>
  )
}
