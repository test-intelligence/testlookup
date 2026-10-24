/**
 * W12 SRCH-03 — Saved searches on /search, from the server (`saved_views` rows
 * with `page: 'search'`), and the **Save search** form.
 *
 * A row: its name, then `scope · mode · q` and a `Shared` badge when shared;
 * the first three carry the ⌘1 / ⌘2 / ⌘3 hints (the shortcut fires them, in
 * the server's order). The owner's row has Rename (a form under the row),
 * Share / Stop sharing (a project-bound row only: a search saved for all
 * projects cannot be shared) and Delete (a confirmation under the row); a row
 * another member shared runs and nothing else. Everything opens in the page.
 * Names and queries are React text.
 */
import { useId, useState, type FormEvent } from 'react'
import toast from 'react-hot-toast'
import { Bookmark } from 'lucide-react'
import InlineConfirm from '@/components/ui/inPage/InlineConfirm'
import InlineFormSection from '@/components/ui/inPage/InlineFormSection'
import { useInlineSlot, type InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { createSavedSearch, deleteSavedSearch, updateSavedSearch } from '@/hooks/useSavedSearches'
import type { SavedView } from '@/services/savedViewsService'
import { HighlightedQuery, Kbd, SearchCardShell, SearchListRow } from './RecentSearchesCard'
import { errorSentence } from './searchModel'
import {
  SAVED_SEARCH_PAGE, SAVE_NAME_MAX, SHORTCUT_SLOTS, defaultSaveName, readSavedSearch, savedSearchFilters,
  validSaveName, type SearchSpec,
} from './savedSearchesModel'

export const SAVE_FAILED = 'Could not save the search.'
export const RENAME_FAILED = 'Could not rename the saved search.'
export const SHARE_FAILED = 'Could not change the sharing of the saved search.'
export const DELETE_FAILED = 'Could not delete the saved search.'

const ICON_BG = 'color-mix(in srgb, var(--ui-warning) 14%, transparent)'
const ICON_FG = 'var(--ui-warning)'

// ── Save this search ────────────────────────────────────────────────────

export function SaveSearchForm({
  search, projectId, projectName, slot, onClose, onSaved,
}: {
  search: SearchSpec
  /** The pinned project (the row's project); null in All Projects: then it cannot be shared. */
  projectId: string | null
  projectName: string | null
  slot?: InlineSlot
  onClose: () => void
  onSaved: (view: SavedView) => void
}) {
  const nameId = useId()
  const [name, setName] = useState(() => defaultSaveName(search.q))
  const [shared, setShared] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const trimmed = name.trim()
    if (!validSaveName(trimmed)) return
    setBusy(true)
    setError(null)
    try {
      const view = await createSavedSearch({
        project_id: projectId,
        name: trimmed,
        page: SAVED_SEARCH_PAGE,
        filters: { ...savedSearchFilters(search.q, search.scope, search.mode) },
        is_shared: projectId !== null && shared,
      })
      toast.success(`Saved “${trimmed}”.`)
      onSaved(view)
    } catch (err) {
      setError(errorSentence(err, SAVE_FAILED))
    } finally {
      setBusy(false)
    }
  }

  return (
    <InlineFormSection
      title="Save this search"
      titleId="save-search-title"
      headingLevel={3}
      onClose={onClose}
      canClose={!busy}
      slot={slot}
      busy={busy}
      onSubmit={(event) => void submit(event)}
      dataAttributes={{ 'data-save-search': '' }}
      footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="submit" className="btn-primary" disabled={busy || !validSaveName(name)}>Save</button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <label htmlFor={nameId} className="text-sm font-medium text-[var(--color-text)]">Name</label>
          <input
            id={nameId}
            className="input w-full max-w-[44ch]"
            value={name}
            maxLength={SAVE_NAME_MAX}
            onChange={(event) => setName(event.target.value)}
          />
          <p className="m-0 text-xs text-[var(--color-text-muted)]">
            Saves <code className="font-mono">{search.q}</code> · {search.scope} · {search.mode}
          </p>
        </div>
        {projectId !== null && (
          <label className="inline-flex items-center gap-2 text-sm text-[var(--color-text)]">
            <input type="checkbox" checked={shared} onChange={(event) => setShared(event.target.checked)} />
            Share with {projectName ?? 'this project'}
          </label>
        )}
        {error && <p role="alert" className="m-0 text-xs text-[var(--ui-danger-ink)]">{error}</p>}
      </div>
    </InlineFormSection>
  )
}

// ── Rename (under the row) ──────────────────────────────────────────────

function RenameForm({
  view, slot, onClose, onRenamed,
}: {
  view: SavedView
  slot: InlineSlot
  onClose: () => void
  onRenamed: () => void
}) {
  const nameId = useId()
  const [name, setName] = useState(view.name)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const trimmed = name.trim()
    if (!validSaveName(trimmed)) return
    setBusy(true)
    setError(null)
    try {
      await updateSavedSearch(view.id, { name: trimmed })
      toast.success(`Renamed to “${trimmed}”.`)
      onRenamed()
    } catch (err) {
      setError(errorSentence(err, RENAME_FAILED))
    } finally {
      setBusy(false)
    }
  }

  return (
    <InlineFormSection
      title="Rename saved search"
      headingLevel={3}
      onClose={onClose}
      canClose={!busy}
      slot={slot}
      busy={busy}
      onSubmit={(event) => void submit(event)}
      dataAttributes={{ 'data-rename-saved-search': view.id }}
      footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="submit" className="btn-primary" disabled={busy || !validSaveName(name)}>Save</button>
        </>
      }
    >
      <div className="flex flex-col gap-1">
        <label htmlFor={nameId} className="text-sm font-medium text-[var(--color-text)]">Name</label>
        <input
          id={nameId}
          className="input w-full max-w-[44ch]"
          value={name}
          maxLength={SAVE_NAME_MAX}
          onChange={(event) => setName(event.target.value)}
        />
        {error && <p role="alert" className="m-0 text-xs text-[var(--ui-danger-ink)]">{error}</p>}
      </div>
    </InlineFormSection>
  )
}

// ── The card ────────────────────────────────────────────────────────────

export default function SavedSearchesCard({
  views, userId, onRun, onChanged,
}: {
  /** The page's saved searches, in the server's order. */
  views: readonly SavedView[]
  /** The signed-in user (owner of a row = may rename / share / delete it). */
  userId: string | null
  onRun: (search: SearchSpec) => void
  /** After a rename / share / delete: re-read the list. */
  onChanged: () => void
}) {
  const slot = useInlineSlot<string>()
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  const rows = views
    .map((view) => ({ view, search: readSavedSearch(view) }))
    .filter((row): row is { view: SavedView; search: SearchSpec } => row.search !== null)
  if (rows.length === 0) return null

  const toggleShare = async (view: SavedView) => {
    setBusyId(view.id)
    try {
      await updateSavedSearch(view.id, { is_shared: !view.is_shared })
      toast.success(view.is_shared ? `Stopped sharing “${view.name}”.` : `Shared “${view.name}” with the project.`)
      onChanged()
    } catch (err) {
      toast.error(errorSentence(err, SHARE_FAILED))
    } finally {
      setBusyId(null)
    }
  }

  const remove = async (view: SavedView) => {
    setConfirmingDelete(null)
    setBusyId(view.id)
    try {
      await deleteSavedSearch(view.id)
      toast.success(`Deleted “${view.name}”.`)
      onChanged()
    } catch (err) {
      toast.error(errorSentence(err, DELETE_FAILED))
    } finally {
      setBusyId(null)
    }
  }

  return (
    <SearchCardShell
      testId="saved-searches"
      title={
        <span className="inline-flex items-center gap-2">
          <Bookmark className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
          Saved searches
        </span>
      }
    >
      <div>
        {rows.map(({ view, search }, index) => {
          const mine = userId !== null && view.user_id === userId
          const renameKey = `rename:${view.id}`
          const busy = busyId === view.id
          return (
            <SearchListRow
              key={view.id}
              Icon={Bookmark}
              iconBg={ICON_BG}
              iconFg={ICON_FG}
              label={view.name}
              title={view.name}
              dataAttributes={{ 'data-saved-search': view.id }}
              // Beside the name and never truncated (it ended the truncating
              // query line and was clipped at 1280): the name and the query
              // truncate instead, each with its full text on hover.
              badge={view.is_shared ? (
                <span className="whitespace-nowrap rounded-full border border-[var(--color-border)] px-1.5 text-2xs text-[var(--color-text-secondary)]" data-saved-search-shared="">
                  Shared
                </span>
              ) : undefined}
              metaTitle={`${search.scope} · ${search.mode} · ${search.q}`}
              meta={
                <>
                  {search.scope}
                  {' · '}
                  {search.mode}
                  {' · '}
                  <code className="rounded-sm border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-1 font-mono text-2xs">
                    <HighlightedQuery q={search.q} />
                  </code>
                </>
              }
              trailing={index < SHORTCUT_SLOTS ? <Kbd>⌘{index + 1}</Kbd> : null}
              actions={mine ? (
                <>
                  <button type="button" className="btn-secondary btn-sm whitespace-nowrap" disabled={busy} onClick={() => { setConfirmingDelete(null); slot.open(renameKey) }}>
                    Rename
                  </button>
                  {view.project_id !== null && (
                    <button type="button" className="btn-secondary btn-sm whitespace-nowrap" disabled={busy} onClick={() => void toggleShare(view)}>
                      {view.is_shared ? 'Stop sharing' : 'Share'}
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn-secondary btn-sm whitespace-nowrap"
                    disabled={busy}
                    onClick={() => slot.guard(() => { slot.close(); setConfirmingDelete(view.id) })}
                  >
                    Delete
                  </button>
                </>
              ) : undefined}
              onClick={() => onRun(search)}
              below={
                slot.openKey === renameKey ? (
                  <RenameForm
                    view={view}
                    slot={slot}
                    onClose={slot.close}
                    onRenamed={() => { slot.close(); onChanged() }}
                  />
                ) : confirmingDelete === view.id ? (
                  <InlineConfirm
                    message={<>Delete the saved search “<strong>{view.name}</strong>”?</>}
                    confirmLabel="Delete"
                    tone="danger"
                    onConfirm={() => void remove(view)}
                    onCancel={() => setConfirmingDelete(null)}
                  />
                ) : undefined
              }
            />
          )
        })}
      </div>
    </SearchCardShell>
  )
}
