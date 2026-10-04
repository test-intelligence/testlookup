/**
 * VIZ-609 — the saved-views manager: "Views" opens a panel listing this
 * page's named views (mine and the ones shared in this project), each with
 * Open, and for my own: Set default, Share / Unshare and Delete; below them
 * "Save current view" (name, shared, default).
 *
 * Each page mounts its own (P1, 2026-10-04: the report chrome that used to
 * host it is gone): the six keyed report pages through `useReportViewsMenu`,
 * the Explorer directly. Open hands the view's scope to the page's `onApply`,
 * which writes the page's own controls (the top-bar release, the window, the
 * page's suite filter), so the URL, the chips and every chart follow, and
 * every release id is re-checked against the project as any pick is. A view
 * saved by someone else never widens what the reader can see: the data
 * requests re-authorise every release for the reader.
 *
 * The rows come from the SWR entry the page's widget layout already reads
 * (`savedViewsKey`), so a page with both asks once.
 */
import { useEffect, useId, useRef, useState, type FormEvent, type MouseEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import useSWR from 'swr'
import toast from 'react-hot-toast'
import { Bookmark, Share2, Star, Trash2 } from 'lucide-react'
import { HeaderPopover } from '@/components/ui/HeaderPopover'
import {
  createSavedView,
  deleteSavedView,
  listSavedViews,
  updateSavedView,
  type SavedView,
} from '@/services/savedViewsService'
import { SCOPE_URL_KEYS } from '@/lib/scopeUrl'
import { useAuthStore } from '@/store/authStore'
import {
  defaultAppliedKey,
  isReportView,
  nameTaken,
  orderViews,
  readReportView,
  reportViewFilters,
  savedViewsKey,
  VIEW_NAME_MAX,
  VIEW_NAME_MIN,
  type ReportViewScope,
  type SavedViewPage,
} from './savedViewsModel'

export interface SavedViewsMenuProps {
  page: SavedViewPage
  projectId: string
  /** The scope as the filter bar shows it now: what "Save current view" stores. */
  current: ReportViewScope
  /**
   * Apply a view's scope (the report-scope store's setters). The view itself
   * comes second, for a page that stores more than the scope (`extraFilters`).
   */
  onApply: (scope: ReportViewScope, view: SavedView) => void
  /**
   * More keys saved beside the scope in `filters` (VIZ-505: the Explorer's
   * configuration under `explore`). They never replace a scope key.
   */
  extraFilters?: Record<string, unknown>
  /**
   * Whether the URL already describes the page, so my default view must not
   * open over it. Default: the URL names releases or suites.
   */
  linked?: boolean
  /**
   * `accent` (default): the Explorer's header button. `ghost`: the report
   * pages' header buttons (their `GhostBtn`, class for class).
   */
  variant?: 'accent' | 'ghost'
}

const BUTTON =
  'inline-flex min-h-8 shrink-0 items-center gap-1 rounded-md px-2 text-sm font-medium text-[var(--color-accent-ink)] hover:bg-[var(--color-accent-bg-soft)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-ring)]'
// The report pages' `GhostBtn`, verbatim (Trends, Coverage, Failures,
// Defects): no class here is new to the stylesheet.
const GHOST_BUTTON =
  'inline-flex items-center gap-1.5 px-2.5 py-1.5 text-[13px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] border rounded-md transition-colors disabled:opacity-50'
const ICON_BUTTON =
  'inline-flex h-7 w-7 items-center justify-center rounded text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-ring)]'

export const VIEWS_BUTTON_TEXT = 'Views'


function alreadyApplied(key: string): boolean {
  try {
    return window.sessionStorage.getItem(key) === '1'
  } catch {
    return false
  }
}

function markApplied(key: string) {
  try {
    window.sessionStorage.setItem(key, '1')
  } catch {
    // Storage blocked: the default may apply again on the next visit.
  }
}

export default function SavedViewsMenu({
  page,
  projectId,
  current,
  onApply,
  extraFilters,
  linked: linkedProp,
  variant = 'accent',
}: SavedViewsMenuProps) {
  const [open, setOpen] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const nameId = useId()
  const userId = useAuthStore((s) => s.user?.id ?? null)
  const [name, setName] = useState('')
  const [shared, setShared] = useState(false)
  const [makeDefault, setMakeDefault] = useState(false)
  const [busy, setBusy] = useState(false)

  // The layout hook's entry (`savedViewsKey`). Not revalidated when this menu
  // mounts over rows already read: the page's layout asked for them moments
  // ago, and a second GET would be the same answer. A save revalidates.
  const { data, mutate, isLoading } = useSWR(savedViewsKey(projectId, page), () => listSavedViews(projectId, page), {
    revalidateOnFocus: false,
    revalidateIfStale: false,
  })
  const views = orderViews((data ?? []).filter(isReportView), userId)

  // My default view for this page opens on the first visit in this tab,
  // unless the URL already names releases or suites (a shared link wins).
  const [params] = useSearchParams()
  const linked = linkedProp ?? (params.has(SCOPE_URL_KEYS.release) || params.has(SCOPE_URL_KEYS.suites))
  const myDefault = views.find((view) => view.is_default && view.user_id === userId) ?? null
  useEffect(() => {
    const key = defaultAppliedKey(projectId, page)
    if (!myDefault || linked || alreadyApplied(key)) return
    markApplied(key)
    const scope = readReportView(myDefault, current.windowDays)
    onApply(scope, myDefault)
    toast.success(`Opened your default view "${myDefault.name}"`)
    // Once per project and page: the view's identity is what matters, not every render's scope.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [myDefault?.id, projectId, page])
  const trimmed = name.trim()
  const duplicate = nameTaken(views, trimmed)

  async function run(action: () => Promise<unknown>, done: string) {
    setBusy(true)
    try {
      await action()
      await mutate()
      toast.success(done)
    } catch {
      toast.error('The view could not be saved. Try again.')
    } finally {
      setBusy(false)
    }
  }

  function save(event: FormEvent) {
    event.preventDefault()
    if (trimmed.length < VIEW_NAME_MIN) return
    void run(
      () =>
        createSavedView({
          project_id: projectId,
          name: trimmed,
          page,
          filters: { ...extraFilters, ...reportViewFilters(page, current) },
          is_shared: shared,
          is_default: makeDefault,
        }),
      `Saved "${trimmed}"`,
    ).then(() => {
      setName('')
      setShared(false)
      setMakeDefault(false)
    })
  }

  function openView(view: SavedView) {
    const scope = readReportView(view, current.windowDays)
    onApply(scope, view)
    setOpen(false)
    toast.success(scope.releaseNote ? `Opened "${view.name}". ${scope.releaseNote}` : `Opened "${view.name}"`)
  }

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        data-saved-views-trigger=""
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        {...(variant === 'ghost'
          ? {
              className: GHOST_BUTTON,
              style: { borderColor: 'var(--color-border)' },
              onMouseEnter: (event: MouseEvent<HTMLButtonElement>) => (event.currentTarget.style.borderColor = 'var(--color-border-light)'),
              onMouseLeave: (event: MouseEvent<HTMLButtonElement>) => (event.currentTarget.style.borderColor = 'var(--color-border)'),
            }
          : { className: BUTTON })}
      >
        <Bookmark aria-hidden="true" className={variant === 'ghost' ? 'h-3.5 w-3.5' : 'h-4 w-4'} />
        {VIEWS_BUTTON_TEXT}
      </button>
      <HeaderPopover anchorRef={triggerRef} open={open} onClose={() => setOpen(false)} width={360} role="dialog" ariaLabel="Saved views">
        <div data-saved-views-panel="" className="flex flex-col gap-3 p-3 text-sm text-[var(--color-text)]">
          <section aria-label="Saved views for this page">
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">Saved views</h3>
            {isLoading ? (
              <p className="text-[var(--color-text-secondary)]">Loading…</p>
            ) : views.length === 0 ? (
              <p data-saved-views-empty="" className="text-[var(--color-text-secondary)]">
                No saved views for this page yet. Save the current filters below.
              </p>
            ) : (
              <ul className="flex max-h-64 flex-col gap-1 overflow-y-auto">
                {views.map((view) => {
                  const mine = view.user_id === userId
                  return (
                    <li key={view.id} data-saved-view={view.id} className="flex items-center gap-1 rounded px-1 hover:bg-[var(--color-bg-hover)]">
                      <button
                        type="button"
                        data-saved-view-open=""
                        onClick={() => openView(view)}
                        className="min-w-0 flex-1 truncate py-1 text-left font-medium"
                        title={view.description ?? view.name}
                      >
                        {view.is_default && mine ? <Star aria-label="Default" className="mr-1 inline h-3.5 w-3.5 fill-current text-[var(--status-broken)]" /> : null}
                        {view.name}
                        {!mine ? <span className="ml-1 text-xs font-normal text-[var(--color-text-secondary)]">(shared)</span> : null}
                      </button>
                      {mine ? (
                        <>
                          <button
                            type="button"
                            data-saved-view-default=""
                            aria-pressed={view.is_default}
                            title={view.is_default ? 'Default view for this page' : 'Open this view by default on this page'}
                            disabled={busy || view.is_default}
                            onClick={() => void run(() => updateSavedView(view.id, { is_default: true }), `"${view.name}" is your default view`)}
                            className={ICON_BUTTON}
                          >
                            <Star aria-hidden="true" className={`h-4 w-4 ${view.is_default ? 'fill-current' : ''}`} />
                          </button>
                          <button
                            type="button"
                            data-saved-view-share=""
                            aria-pressed={view.is_shared}
                            title={view.is_shared ? 'Shared with this project: click to make it private' : 'Share with everyone in this project'}
                            disabled={busy}
                            onClick={() =>
                              void run(
                                () => updateSavedView(view.id, { is_shared: !view.is_shared }),
                                view.is_shared ? `"${view.name}" is private` : `"${view.name}" is shared with the project`,
                              )
                            }
                            className={`${ICON_BUTTON} ${view.is_shared ? 'text-[var(--color-accent-ink)]' : ''}`}
                          >
                            <Share2 aria-hidden="true" className="h-4 w-4" />
                          </button>
                          <button
                            type="button"
                            data-saved-view-delete=""
                            title="Delete this view"
                            disabled={busy}
                            onClick={() => {
                              if (window.confirm(`Delete the view "${view.name}"?`)) {
                                void run(() => deleteSavedView(view.id), `Deleted "${view.name}"`)
                              }
                            }}
                            className={ICON_BUTTON}
                          >
                            <Trash2 aria-hidden="true" className="h-4 w-4" />
                          </button>
                        </>
                      ) : null}
                    </li>
                  )
                })}
              </ul>
            )}
          </section>
          <form onSubmit={save} aria-label="Save current view" className="flex flex-col gap-2 border-t border-[var(--color-border)] pt-3">
            <label htmlFor={nameId} className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">
              Save current view
            </label>
            <input
              id={nameId}
              data-saved-view-name=""
              value={name}
              maxLength={VIEW_NAME_MAX}
              placeholder="e.g. Payments release watch"
              onChange={(event) => setName(event.target.value)}
              className="min-h-8 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-input)] px-2 text-sm"
            />
            {duplicate ? (
              <p data-saved-view-duplicate="" className="text-xs text-[var(--status-broken)]">
                A view with this name already exists; saving adds a second one.
              </p>
            ) : null}
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <label className="inline-flex items-center gap-1">
                <input type="checkbox" data-saved-view-shared="" checked={shared} onChange={(event) => setShared(event.target.checked)} />
                Share with the project
              </label>
              <label className="inline-flex items-center gap-1">
                <input type="checkbox" data-saved-view-make-default="" checked={makeDefault} onChange={(event) => setMakeDefault(event.target.checked)} />
                My default for this page
              </label>
            </div>
            <button
              type="submit"
              data-saved-view-save=""
              disabled={busy || trimmed.length < VIEW_NAME_MIN}
              className="self-start rounded-md bg-[var(--color-btn-primary-bg)] px-3 py-1.5 text-sm font-semibold text-[var(--color-btn-primary-text)] disabled:opacity-50"
            >
              Save view
            </button>
          </form>
        </div>
      </HeaderPopover>
    </>
  )
}
