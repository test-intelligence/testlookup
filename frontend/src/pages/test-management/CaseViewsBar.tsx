/**
 * W13 TCL-01 — the Cases card's second header row, **Views**: the four presets
 * (My drafts, My P0/P1, Unautomated, AI drafts), then the saved case views
 * (mine, then those shared in the project, by name), then **Save view** and,
 * with at least one view of mine, **Manage**.
 *
 * A chip is active exactly when the URL state equals its filters; a click
 * REPLACES every filter key (`onApply`). Save view and Manage open IN THE
 * PAGE, under the row (`InlineFormSection`, one at a time); Delete asks in the
 * row (`InlineConfirm`). A view another member shared is apply-only. Every
 * name is React TEXT.
 */
import { Fragment, useId, useState, type FormEvent, type ReactNode } from 'react'
import { Bookmark, Pencil, Share2, Trash2 } from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { InlineConfirm } from '@/components/ui/inPage/InlineConfirm'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import { useInlineSlot, type InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { useCaseViews } from '@/hooks/useTestLibrary'
import { createSavedView, deleteSavedView, updateSavedView, type SavedView } from '@/services/savedViewsService'
import { useAuthStore } from '@/store/authStore'
import { useProjectStore } from '@/store/projectStore'
import { CASE_PRESETS, presetFilters, type CaseFilterState, type CasePresetId } from './caseFilterState'
import {
  DELETE_FAILED,
  MANAGE_TEXT,
  MANAGE_TITLE,
  RENAME_TITLE,
  SAVE_FAILED,
  SAVE_TITLE,
  SAVE_TITLE_ID,
  SAVE_VIEW_TEXT,
  SHARED_TEXT,
  UPDATE_FAILED,
  VIEWS_LABEL,
  VIEW_NAME_MAX,
  isMine,
  nameProblem,
  orderCaseViews,
  savePayload,
  shareLabel,
  viewIsActive,
  viewState,
} from './caseViewsModel'
import { serverSentence } from './importCasesModel'

export interface CaseViewsBarProps {
  projectId: string | null
  current: CaseFilterState
  activePresetId: string | null
  presets: { id: string; label: string }[]
  onApply: (next: CaseFilterState) => void
  /** May save views here (one project, a signed-in member). */
  canSave: boolean
}

const CHIP =
  'inline-flex max-w-[16ch] items-center gap-1 rounded-full border border-[var(--color-border)] px-2.5 py-0.5 text-xs text-[var(--color-text-secondary)] ' +
  'hover:bg-[var(--color-bg-hover)] aria-pressed:border-[var(--color-accent)] aria-pressed:bg-[var(--color-accent-bg-soft)] aria-pressed:text-[var(--color-accent-ink)]'
const ACTION = 'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs text-[var(--color-accent-ink)] hover:bg-[var(--color-bg-hover)]'
const ICON_BUTTON =
  'inline-flex h-7 w-7 items-center justify-center rounded text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] disabled:opacity-40'

function NameForm({
  title, titleId, initialName, submitLabel, extra, onSubmit, onClose, slot,
}: {
  title: string
  titleId?: string
  initialName: string
  submitLabel: string
  extra?: (busy: boolean) => ReactNode
  /** The failure sentence, or null on success (the caller closes the form). */
  onSubmit: (name: string) => Promise<string | null>
  onClose: () => void
  slot?: InlineSlot
}) {
  const nameId = useId()
  const [name, setName] = useState(initialName)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const problem = nameProblem(name)
    if (problem) {
      setError(problem)
      return
    }
    setBusy(true)
    setError(null)
    const failure = await onSubmit(name.trim())
    if (failure) {
      setError(failure)
      setBusy(false)
    }
  }

  return (
    <InlineFormSection
      title={title}
      titleId={titleId}
      headingLevel={3}
      onClose={onClose}
      canClose={!busy}
      busy={busy}
      slot={slot}
      onSubmit={(event) => void submit(event)}
      footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="submit" className="btn-primary inline-flex items-center gap-2" disabled={busy}>
            {busy && <LoadingSpinner size="sm" />}
            {submitLabel}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <div>
          <label htmlFor={nameId} className="mb-1 block text-xs text-[var(--color-text-muted)]">Name</label>
          <input id={nameId} className="input w-full max-w-[44ch]" value={name} maxLength={VIEW_NAME_MAX}
            onChange={(event) => setName(event.target.value)} disabled={busy} />
        </div>
        {extra?.(busy)}
        {error && <p role="alert" data-case-view-error="" className="m-0 text-sm text-[var(--ui-danger-ink)]">{error}</p>}
      </div>
    </InlineFormSection>
  )
}

function ManageViews({ views, onChanged, onClose }: { views: SavedView[]; onChanged: () => Promise<unknown>; onClose: () => void }) {
  const [renaming, setRenaming] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [rowError, setRowError] = useState<{ id: string; text: string } | null>(null)

  async function run(view: SavedView, action: () => Promise<unknown>, done: string, fallback: string) {
    setBusy(true)
    setRowError(null)
    try {
      await action()
      await onChanged()
      toast.success(done)
      setConfirming(null)
    } catch (err) {
      setRowError({ id: view.id, text: serverSentence(err, fallback) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <InlineFormSection title={MANAGE_TITLE} headingLevel={3} onClose={onClose} canClose={!busy} dataAttributes={{ 'data-case-views-manage': '' }}>
      <ul className="m-0 list-none space-y-1 p-0">
        {views.map((view) => (
          <Fragment key={view.id}>
            {renaming === view.id ? (
              <li>
                <NameForm
                  title={RENAME_TITLE}
                  initialName={view.name}
                  submitLabel="Rename"
                  onClose={() => setRenaming(null)}
                  onSubmit={async (name) => {
                    try {
                      await updateSavedView(view.id, { name }, { suppressToast: true })
                      await onChanged()
                      toast.success(`Renamed the view to “${name}”`)
                      setRenaming(null)
                      return null
                    } catch (err) {
                      return serverSentence(err, UPDATE_FAILED)
                    }
                  }}
                />
              </li>
            ) : (
              <li data-case-view-manage={view.id} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 rounded-md px-1 py-1 hover:bg-[var(--color-bg-hover)]">
                <span className="min-w-0 truncate text-sm text-[var(--color-text)]" title={view.name}>
                  {view.name}
                  {view.is_shared && <span className="ml-1.5 text-xs text-[var(--color-text-muted)]">· {SHARED_TEXT}</span>}
                </span>
                <span className="flex items-center gap-0.5">
                  <button type="button" className={ICON_BUTTON} aria-label={`Rename ${view.name}`} title="Rename" disabled={busy}
                    onClick={() => { setConfirming(null); setRenaming(view.id) }}>
                    <Pencil aria-hidden="true" className="h-4 w-4" />
                  </button>
                  <button
                    type="button"
                    className={clsx(ICON_BUTTON, view.is_shared && 'text-[var(--color-accent-ink)]')}
                    aria-label={`${view.is_shared ? 'Stop sharing' : 'Share'} ${view.name}`}
                    aria-pressed={view.is_shared}
                    title={view.is_shared ? 'Stop sharing' : 'Share'}
                    disabled={busy}
                    onClick={() => void run(
                      view,
                      () => updateSavedView(view.id, { is_shared: !view.is_shared }),
                      view.is_shared ? `“${view.name}” is private` : `“${view.name}” is shared with the project`,
                      UPDATE_FAILED,
                    )}
                  >
                    <Share2 aria-hidden="true" className="h-4 w-4" />
                  </button>
                  <button type="button" className={ICON_BUTTON} aria-label={`Delete ${view.name}`} title="Delete" disabled={busy}
                    onClick={() => { setRenaming(null); setConfirming(view.id) }}>
                    <Trash2 aria-hidden="true" className="h-4 w-4" />
                  </button>
                </span>
              </li>
            )}
            {confirming === view.id && (
              <li data-in-page-row="">
                <InlineConfirm
                  message={<>Delete the view “<strong className="break-words">{view.name}</strong>”?</>}
                  confirmLabel="Delete"
                  tone="danger"
                  busy={busy}
                  onConfirm={() => void run(view, () => deleteSavedView(view.id), `Deleted the view “${view.name}”`, DELETE_FAILED)}
                  onCancel={() => setConfirming(null)}
                />
              </li>
            )}
            {rowError?.id === view.id && <li role="alert" className="text-sm text-[var(--ui-danger-ink)]">{rowError.text}</li>}
          </Fragment>
        ))}
      </ul>
    </InlineFormSection>
  )
}

function isPresetId(id: string): id is CasePresetId {
  return Object.prototype.hasOwnProperty.call(CASE_PRESETS, id)
}

export default function CaseViewsBar({ projectId, current, activePresetId, presets, onApply, canSave }: CaseViewsBarProps) {
  const userId = useAuthStore((s) => s.user?.id ?? null)
  // A store with no `projects` yet (an early render, a test's partial store) reads as "not found", never a throw.
  const projectName = useProjectStore((s) => {
    if (!projectId) return null
    if (s.activeProject?.id === projectId) return s.activeProject.name ?? null
    return s.projects?.find((p) => p.id === projectId)?.name ?? null
  })
  const { views, mutate } = useCaseViews(projectId)
  const slot = useInlineSlot<'save' | 'manage'>()
  const [shared, setShared] = useState(false)
  const ordered = orderCaseViews(views, userId)
  const mine = ordered.filter((view) => isMine(view, userId))
  const writable = canSave && Boolean(projectId)

  return (
    <div data-case-views="" className="space-y-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="eyebrow mr-1">{VIEWS_LABEL}</span>
        {presets.map((preset) => (
          <button
            key={preset.id}
            type="button"
            data-case-preset={preset.id}
            aria-pressed={activePresetId === preset.id}
            className={CHIP}
            onClick={() => { if (isPresetId(preset.id)) onApply(presetFilters(preset.id)) }}
          >
            <span className="truncate">{preset.label}</span>
          </button>
        ))}
        {ordered.map((view) => {
          const own = isMine(view, userId)
          return (
            <button
              key={view.id}
              type="button"
              data-case-view={view.id}
              aria-pressed={viewIsActive(view, current)}
              className={CHIP}
              title={own ? view.name : `${view.name} (shared with the project)`}
              onClick={() => onApply(viewState(view))}
            >
              <span className="truncate">{view.name}</span>
              {view.is_shared && (
                <>
                  <span aria-hidden="true" data-shared-dot="" className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-accent)]" />
                  <span className="sr-only">{SHARED_TEXT}</span>
                </>
              )}
            </button>
          )
        })}
        {writable && (
          <button type="button" data-case-view-save-open="" className={ACTION} onClick={() => { setShared(false); slot.open('save') }}>
            <Bookmark aria-hidden="true" className="h-3.5 w-3.5" />
            {SAVE_VIEW_TEXT}
          </button>
        )}
        {writable && mine.length > 0 && (
          <button type="button" data-case-view-manage-open="" className={ACTION} onClick={() => slot.open('manage')}>
            {MANAGE_TEXT}
          </button>
        )}
      </div>
      {writable && slot.openKey === 'save' && projectId && (
        <NameForm
          title={SAVE_TITLE}
          titleId={SAVE_TITLE_ID}
          initialName=""
          submitLabel="Save"
          slot={slot}
          onClose={slot.close}
          extra={(busy) => (
            <label className="inline-flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
              <input type="checkbox" checked={shared} disabled={busy} onChange={(event) => setShared(event.target.checked)} />
              {shareLabel(projectName)}
            </label>
          )}
          onSubmit={async (name) => {
            try {
              await createSavedView(savePayload(projectId, name, current, shared), { suppressToast: true })
              await mutate()
              toast.success(`Saved the view “${name}”`)
              slot.close()
              return null
            } catch (err) {
              return serverSentence(err, SAVE_FAILED)
            }
          }}
        />
      )}
      {writable && slot.openKey === 'manage' && mine.length > 0 && (
        <ManageViews views={mine} onChanged={() => mutate()} onClose={slot.close} />
      )}
    </div>
  )
}
