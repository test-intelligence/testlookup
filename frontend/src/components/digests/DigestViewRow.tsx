/**
 * NTF-04: one listed saved view on the digests page's Saved Views tab — its
 * name, Shared / Default, where it was saved (`Saved on Trends`) and what it
 * covers (`Releases: 2026.10 · Suites: checkout · Last 7 days`). Edit turns
 * the row into the in-page editor (the page swaps it); Delete is the page's
 * own handler (its existing confirm stays in `DigestsPage.tsx` until W27).
 * Names are React text.
 */
import type { SavedView } from '@/services/savedViewsService'
import type { Release } from '@/types/releases'
import { viewPageText, viewScopeText } from './digestScopeModel'

const CHIP = 'ml-2 px-1.5 py-0.5 text-[10px] rounded'

export default function DigestViewRow({
  view,
  releases,
  onEdit,
  onDelete,
  canEdit = true,
}: {
  view: SavedView
  releases: readonly Release[]
  onEdit: () => void
  onDelete: () => void
  /** False in All Projects: the editor's releases are one project's. */
  canEdit?: boolean
}) {
  const page = viewPageText(view)
  return (
    <div data-digest-view-row={view.id} className="bg-[var(--color-bg-secondary)] rounded-lg p-3 flex justify-between items-center gap-4">
      <div className="min-w-0">
        <div className="flex items-center">
          <span className="text-[var(--color-text)] text-sm font-medium truncate">{view.name}</span>
          {view.is_shared && <span className={`${CHIP} bg-[var(--color-bg-secondary)]/60 text-[var(--color-text)]`}>Shared</span>}
          {view.is_default && <span className={`${CHIP} bg-[var(--status-passed-bg)]/40 text-[var(--status-passed)]`}>Default</span>}
        </div>
        <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
          {page ? `${page} · ` : ''}
          {viewScopeText(view, releases)}
        </p>
      </div>
      <div className="flex shrink-0 gap-2">
        {canEdit && (
          <button type="button" onClick={onEdit} className="px-2 py-1 text-xs rounded bg-[var(--color-bg-card)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]">
            Edit
          </button>
        )}
        <button type="button" onClick={onDelete} className="px-2 py-1 text-xs bg-[var(--status-failed-bg)]/50 rounded text-[var(--status-failed)] hover:bg-[var(--status-failed-bg)]">
          Delete
        </button>
      </div>
    </div>
  )
}
