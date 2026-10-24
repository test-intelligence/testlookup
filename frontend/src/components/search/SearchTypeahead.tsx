/**
 * W12 SRCH-03 — the top-bar typeahead: up to five DISTINCT matches under the
 * global search input while the reader types (2+ characters, 250 ms after the
 * last keystroke, one `GET /search/global?size=15`, never recorded, no global
 * toast). One test that ran in many runs is ONE row — its first in the
 * server's order, "· 5 runs" after its subtitle — not five copies.
 *
 * In the page, never over it: a `role="listbox"` anchored inside the input's
 * own `relative` wrapper (absolute, `top-full`), as wide as the input — not a
 * dialog, not a portal, not `fixed`. While closed it renders NOTHING, so the
 * top bar's markup is the same as before (the desktop shell snapshot).
 *
 * A match opens its entity (selecting its project first, as the /search
 * results do); the last row — and Enter in the input, as before — runs the
 * full search. An answer for an older query is never shown (the read is keyed
 * by the settled query). No keyboard navigation (owner: no keyboard items).
 * Titles and subtitles are React text.
 */
import { useEffect, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import {
  AlertTriangle, CornerDownLeft, Layers, Package, Search as SearchIcon, ShieldCheck, TestTube, Zap,
} from 'lucide-react'
import { useSearchTypeahead } from '@/hooks/useSearchTypeahead'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import type { GlobalSearchResult, SearchEntityType } from '@/types/search'
import { TYPEAHEAD_ROWS, distinctMatches, fullSearchHref, matchCountLabel } from './searchModel'

export const TYPEAHEAD_SEARCHING = 'Searching…'
export const TYPEAHEAD_EMPTY = 'No matches. Press Enter to search everything.'
export const TYPEAHEAD_ERROR = 'Could not load matches.'

const ENTITY_ICON: Record<SearchEntityType, typeof SearchIcon> = {
  test_case: TestTube,
  test_run: Zap,
  suite: Layers,
  defect: ShieldCheck,
  flaky_test: AlertTriangle,
  release: Package,
}

const ENTITY_LABEL: Record<SearchEntityType, string> = {
  test_case: 'Tests',
  test_run: 'Runs',
  suite: 'Suites',
  defect: 'Defects',
  flaky_test: 'Flaky',
  release: 'Releases',
}

const ROW = 'flex w-full cursor-pointer items-center gap-2.5 px-3 py-2 text-left hover:bg-[var(--color-bg-hover)]'

export default function SearchTypeahead({
  query, open, onClose, onDone,
}: {
  /** The input's text. */
  query: string
  /** The list is open (typed into; not closed by Escape, a blur or a navigation). */
  open: boolean
  /** Close and keep the text (a navigation elsewhere). */
  onClose: () => void
  /** A row was chosen and opened: close and clear the input (as Enter does). */
  onDone: () => void
}) {
  const navigate = useNavigate()
  const location = useLocation()
  const projects = useProjectStore((s) => s.projects)
  const setActiveProject = useProjectStore((s) => s.setActiveProject)
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const projectId = activeProjectId && activeProjectId !== ALL_PROJECTS_ID ? activeProjectId : null
  const { debounced, settling, data, error, isLoading } = useSearchTypeahead(query, projectId, open)

  // A navigation closes the list (and so drops the read).
  const where = `${location.pathname}${location.search}`
  const lastWhere = useRef(where)
  useEffect(() => {
    if (lastWhere.current === where) return
    lastWhere.current = where
    onClose()
  }, [where, onClose])

  const trimmed = query.trim()
  if (!open || trimmed.length < 2) return null

  const openMatch = (row: GlobalSearchResult) => {
    if (row.project_id) {
      const destination = projects.find((candidate) => candidate.id === row.project_id)
      if (destination) setActiveProject(destination)
    }
    onDone()
    navigate(row.navigation_url)
  }

  const searchEverything = () => {
    onDone()
    navigate(fullSearchHref(trimmed))
  }

  // Five DISTINCT entities out of the 15 candidates: a test that ran in many
  // runs is one row (its first, most relevant, in the server's order).
  const candidates = data?.items ?? []
  const matches = distinctMatches(candidates, TYPEAHEAD_ROWS)
  const capped = data ? data.counts_are_exact === false || (data.total ?? 0) > candidates.length : false
  let state: string | null = null
  if (settling || (debounced && isLoading && !data)) state = TYPEAHEAD_SEARCHING
  else if (error) state = TYPEAHEAD_ERROR
  else if (data && matches.length === 0) state = TYPEAHEAD_EMPTY

  return (
    <div
      data-search-typeahead=""
      className="card !p-0 absolute left-0 right-0 top-full z-50 mt-1 overflow-hidden shadow-lg"
    >
      <ul role="listbox" aria-label="Matches" className="m-0 list-none p-0">
        {matches.map((match) => {
          const { row } = match
          const Icon = ENTITY_ICON[row.entity_type] ?? SearchIcon
          const countLabel = matchCountLabel(match, capped)
          return (
            <li
              key={`${row.entity_type}-${row.entity_id}`}
              role="option"
              aria-selected={false}
              data-typeahead-row={row.entity_type}
              className={ROW}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => openMatch(row)}
            >
              <span className="inline-flex h-6 w-6 flex-none items-center justify-center rounded-md bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)]">
                <Icon aria-hidden="true" className="h-3 w-3" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm text-[var(--color-text)]" title={row.title}>{row.title}</span>
                {(row.subtitle || countLabel) && (
                  <span className="block truncate text-xs text-[var(--color-text-muted)]">
                    {row.subtitle && <span>{row.subtitle}</span>}
                    {countLabel && <span data-typeahead-count="">{row.subtitle ? ' · ' : ''}{countLabel}</span>}
                  </span>
                )}
              </span>
              <span className="max-w-[80px] flex-none truncate text-xs text-[var(--color-text-muted)]">
                {ENTITY_LABEL[row.entity_type] ?? row.entity_type}
              </span>
            </li>
          )
        })}
        <li
          role="option"
          aria-selected={false}
          data-typeahead-everything=""
          className={`${ROW} border-t border-[var(--color-border)]`}
          onMouseDown={(event) => event.preventDefault()}
          onClick={searchEverything}
        >
          <SearchIcon aria-hidden="true" className="h-3.5 w-3.5 flex-none text-[var(--color-text-muted)]" />
          <span className="min-w-0 flex-1 truncate text-sm text-[var(--color-text)]">Search everything for “{trimmed}”</span>
          <CornerDownLeft aria-hidden="true" className="h-3.5 w-3.5 flex-none text-[var(--color-text-muted)]" />
        </li>
      </ul>
      {state && (
        <p className="m-0 border-t border-[var(--color-border)] px-3 py-2 text-xs text-[var(--color-text-muted)]" data-typeahead-state="">
          {state}
        </p>
      )}
    </div>
  )
}
