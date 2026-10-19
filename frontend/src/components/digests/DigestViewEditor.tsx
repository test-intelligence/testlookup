/**
 * NTF-04: the in-page editor for a digest's saved view (a section at the top
 * of the Saved Views tab for New view, or in place of the row for Edit — never
 * a modal).
 *
 * It writes the REPORT-view shape through the existing saved-views API
 * (`reportViewFilters`): releases (the project's, from the top bar's cached
 * list), suites (comma-separated, at most 50, each trimmed), a window of 1, 7,
 * 14, 30 or 90 days, and whether the project shares it. A new view is saved
 * with `page: 'trends'` — the digest page has no report of its own, and
 * Trends' scope is the digest's; an edited report view keeps its own page, a
 * legacy row opens empty and saves the report-view shape.
 */
import { useId, useState } from 'react'
import toast from 'react-hot-toast'
import Field from '@/components/ui/Field'
import { createSavedView, updateSavedView, type SavedView } from '@/services/savedViewsService'
import { extractErrorMessage } from '@/services/apiErrors'
import { isReportView, type SavedViewPage } from '@/components/reports/savedViewsModel'
import {
  EMPTY_DRAFT,
  VIEW_WINDOWS,
  daysText,
  draftFromView,
  hasErrors,
  releaseOptions,
  validateDraft,
  viewPayload,
  type ViewDraft,
} from './digestScopeModel'

type RequestError = { message?: string; response?: { data?: { detail?: unknown } } }

const LABEL = 'block text-xs text-[var(--color-text-muted)] mb-1'
const INPUT = 'w-full bg-[var(--color-bg-card)] text-[var(--color-text)] rounded px-3 py-2 text-sm'
const ERROR = 'mt-1 text-xs text-[var(--status-failed)]'

export default function DigestViewEditor({
  view,
  projectId,
  releases,
  onSaved,
  onCancel,
}: {
  /** The view to edit; absent for a new one. */
  view?: SavedView
  projectId: string
  releases: readonly unknown[]
  onSaved: () => void
  onCancel: () => void
}) {
  const options = releaseOptions(releases)
  const [draft, setDraft] = useState<ViewDraft>(() => (view ? draftFromView(view, options) : EMPTY_DRAFT))
  const [attempted, setAttempted] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const releasesId = useId()

  const errors = validateDraft(draft)
  const showNameError = Boolean(errors.name) && (attempted || draft.name !== '')
  const set = (patch: Partial<ViewDraft>) => setDraft(d => ({ ...d, ...patch }))
  const toggleRelease = (id: string) =>
    set({ releaseIds: draft.releaseIds.includes(id) ? draft.releaseIds.filter(r => r !== id) : [...draft.releaseIds, id] })

  const handleSave = async () => {
    setAttempted(true)
    if (hasErrors(errors)) return
    setSaving(true)
    setSaveError(null)
    try {
      if (view) {
        const page: SavedViewPage = isReportView(view) && view.page ? view.page : 'trends'
        await updateSavedView(view.id, viewPayload(draft, page))
      } else {
        await createSavedView({ project_id: projectId, ...viewPayload(draft, 'trends') })
      }
      toast.success('View saved')
      onSaved()
    } catch (err: unknown) {
      const e = err as RequestError
      setSaveError(extractErrorMessage(e?.response?.data?.detail, 'The view could not be saved.'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <section
      aria-label={view ? `Edit view ${view.name}` : 'New view'}
      data-digest-view-editor={view ? view.id : 'new'}
      className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4 space-y-4"
    >
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <div>
          <Field label="Name" labelClassName={LABEL}>
            {id => (
              <input id={id} value={draft.name} onChange={e => set({ name: e.target.value })} placeholder="e.g., Checkout regressions" className={INPUT} />
            )}
          </Field>
          {showNameError && <p className={ERROR} data-view-error="name">{errors.name}</p>}
        </div>
        <div>
          <Field label="Suites (comma-separated)" labelClassName={LABEL}>
            {id => (
              <input id={id} value={draft.suitesText} onChange={e => set({ suitesText: e.target.value })} placeholder="e.g., checkout, payments" className={INPUT} />
            )}
          </Field>
          {errors.suites && <p className={ERROR} data-view-error="suites">{errors.suites}</p>}
        </div>
      </div>

      <fieldset>
        <legend className={LABEL} id={releasesId}>Releases</legend>
        {options.length === 0 ? (
          <p className="text-xs text-[var(--color-text-muted)]">No releases in this project: the view covers any release.</p>
        ) : (
          <div className="grid grid-cols-2 gap-1.5 max-h-40 overflow-y-auto pr-1" aria-labelledby={releasesId}>
            {options.map(r => (
              <label key={r.id} className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)] cursor-pointer min-w-0">
                <input type="checkbox" checked={draft.releaseIds.includes(r.id)} onChange={() => toggleRelease(r.id)} />
                <span className="truncate">{r.name}</span>
              </label>
            ))}
          </div>
        )}
      </fieldset>

      <div className="flex flex-wrap items-end gap-4">
        <div>
          <Field label="Window" labelClassName={LABEL}>
            {id => (
              <select
                id={id}
                value={draft.windowDays}
                onChange={e => set({ windowDays: Number(e.target.value) })}
                className="bg-[var(--color-bg-card)] text-[var(--color-text)] rounded px-3 py-2 text-sm"
              >
                {VIEW_WINDOWS.map(n => (
                  <option key={n} value={n}>{`Last ${daysText(n)}`}</option>
                ))}
              </select>
            )}
          </Field>
        </div>
        <label className="flex items-center gap-2 pb-2 text-sm text-[var(--color-text-secondary)] cursor-pointer">
          <input type="checkbox" checked={draft.isShared} onChange={e => set({ isShared: e.target.checked })} />
          Shared with the project
        </label>
      </div>

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => void handleSave()}
          disabled={saving}
          className="btn-primary"
        >
          Save
        </button>
        <button type="button" onClick={onCancel} className="px-4 py-2 rounded text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]">
          Cancel
        </button>
      </div>
      {saveError && (
        <p role="alert" className="text-sm text-[var(--status-failed)]" data-view-save-error="">
          {saveError}
        </p>
      )}
    </section>
  )
}
