/**
 * NTF-04: a project's saved REPORT views as a select, `No view (whole
 * project)` first. Shared by the subscription form, a row's Change view and
 * the preview.
 *
 * The views are read only once someone asks (`requested`): the digests page
 * makes no new request on load (W09 contract §6.5), so on the Subscriptions
 * tab the picker starts as a "Choose a saved view" button and the read goes
 * out on its click. The Preview tab asks when it opens. View names are React
 * text.
 */
import type { SavedView } from '@/services/savedViewsService'
import { pickableViews } from './digestScopeModel'

export const NO_VIEW_LABEL = 'No view (whole project)'
export const CHOOSE_VIEW_LABEL = 'Choose a saved view'

export interface ViewSource {
  /** Whether the views were asked for yet. */
  requested: boolean
  views: readonly SavedView[]
  loading: boolean
  error: boolean
  /** Ask for them (or again, after a failure). */
  request: () => void
}

export default function DigestViewPicker({
  id,
  source,
  projectId,
  value,
  onChange,
}: {
  id?: string
  source: ViewSource
  /** The digest's project: only its report views are offered. */
  projectId: string | null | undefined
  value: string
  onChange: (viewId: string) => void
}) {
  if (!source.requested) {
    // No `id` here: the field's label names the select once it exists, and
    // the button keeps its own name.
    return (
      <button
        type="button"
        onClick={source.request}
        data-digest-view-request=""
        className="block rounded border border-[var(--color-border)] px-3 py-2 text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
      >
        {CHOOSE_VIEW_LABEL}
      </button>
    )
  }
  if (source.error) {
    return (
      <span className="block py-2 text-xs text-[var(--status-failed)]" data-digest-view-error="">
        Saved views could not be read.{' '}
        <button type="button" onClick={source.request} className="underline">
          Retry
        </button>
      </span>
    )
  }
  const reportViews = pickableViews(source.views, projectId)
  return (
    <select
      id={id}
      value={value}
      disabled={source.loading}
      onChange={e => onChange(e.target.value)}
      data-digest-view-select=""
      className="bg-[var(--color-bg-card)] text-[var(--color-text)] rounded px-3 py-2 text-sm max-w-[240px]"
    >
      <option value="">{source.loading ? 'Loading views…' : NO_VIEW_LABEL}</option>
      {reportViews.map(v => (
        <option key={v.id} value={v.id}>
          {v.name}
        </option>
      ))}
    </select>
  )
}
