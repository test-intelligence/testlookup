/**
 * Search — hero-first layout: a large search bar leads, searchable data follows.
 *
 * Layout (1320 px max-width, 14 px section gaps):
 *   Header  → the template's `PageHeader` (UX redesign P6: compact, **?** =
 *             the search topic); the old crumb (workspace · index counts ·
 *             freshness) is its one-line subtitle.
 *   Hero    → SearchCommandBar — large autofocused input + ⌘K kbd + Search
 *             button; after a search with filters, the chips row (W12 SRCH-04:
 *             the applied filters, what was not applied, the Syntax help);
 *             mode chips (Keyword · Hybrid · Semantic) and scope chips (All ·
 *             Tests · Runs · Suites · Defects · Flaky · Releases) each carrying
 *             an item-count badge. Hybrid and Semantic cover test results
 *             only: choosing one moves the scope to Tests (and another scope
 *             moves the mode back to Keyword), with one line that says so.
 *   Verdict → "Index ready" tag + headline "N items indexed across K entity
 *             types" + a lede naming exactly which fields are matched, and
 *             the index-freshness stat. Under it, for a QA lead or an admin,
 *             today's search usage (W12 SRCH-05).
 *   Body    → 1.65fr | 1fr, while the box is empty.
 *     Left  → Suggested (one project pinned) · Recent searches · Saved
 *             searches (⌘1-⌘3, only when the server holds some).
 *     Right → Searchable data (per entity, plus the semantic index line and
 *             Re-index now).
 *
 * Keyword search is a case-insensitive substring match (SQL ILIKE in
 * global_search_service.py) across six kinds, with structured filters
 * (`status:`, `suite:`, … parsed server-side). Hybrid and Semantic go through
 * `GET /api/v1/search`, test results only; when the semantic index cannot
 * answer, the server answers with keyword rows and says why (the banner).
 *
 * Data: deep-links via querystring (`?q=…&mode=…&scope=…`). Every submitted
 * search goes through ONE imperative path (`runSearch`); everything the page
 * loads with is an SWR hook. Recent and saved searches live on the server (a
 * browser's old saved searches are moved there once).
 */
import {
  useCallback, useEffect, useMemo, useRef, useState, type ReactNode,
} from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  AlertTriangle, Bookmark, ChevronRight, Layers, Package,
  Search as SearchIcon, ShieldCheck, TestTube, X, Zap,
} from 'lucide-react'
import toast from 'react-hot-toast'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import AllReleasesBadge from '@/components/ui/AllReleasesBadge'
import { useInlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { useNow } from '@/hooks/useNow'
import { usePermissions } from '@/hooks/usePermissions'
import { useSearchIndexStatus } from '@/hooks/useSearchIndexStatus'
import { useRecentSearches, withRecent, withoutRecents } from '@/hooks/useRecentSearches'
import { createSavedSearch, useSavedSearches } from '@/hooks/useSavedSearches'
import { useSuggestedSearches } from '@/hooks/useSuggestedSearches'
import { useSearchUsage } from '@/hooks/useSearchUsage'
import PageShell from '@/components/layout/PageShell'
import PageHeader from '@/components/ui/PageHeader'
import Pagination from '@/components/ui/Pagination'
import { helpTopicParam } from '@/components/help/helpTopics'
import RecentSearchesCard, { Kbd, SearchCardShell } from '@/components/search/RecentSearchesCard'
import SavedSearchesCard, { SaveSearchForm } from '@/components/search/SavedSearchesCard'
import SearchableDataCard from '@/components/search/SearchableDataCard'
import SearchChipsRow from '@/components/search/SearchChipsRow'
import SearchFallbackBanner from '@/components/search/SearchFallbackBanner'
import SearchUsageStrip from '@/components/search/SearchUsageStrip'
import SuggestedSearchesCard from '@/components/search/SuggestedSearchesCard'
import { migrateLegacySavedSearches } from '@/components/search/savedSearchesMigration'
import { shortcutSearches, type SearchSpec } from '@/components/search/savedSearchesModel'
import { removeFilter } from '@/components/search/searchChips'
import {
  fromSemanticResponse, isSemanticMode, modeAvailability, modeForScope, normalizeMode, parseMode, parseScope,
  relativeTime, type EntityScope, type ModeOption, type PageSearchResponse, type RetrievalMode,
} from '@/components/search/searchModel'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { useAuthStore } from '@/store/authStore'
import { searchService } from '@/services/searchService'
import { formatNumber } from '@/utils/formatters'
import type {
  GlobalSearchResult, IndexStatus, RecentSearch, SearchEntityType, SearchParsedFilter, SearchSuggestion,
} from '@/types/search'

/** The page's help topic (the header's **?**). */
const HELP_TOPIC = helpTopicParam('/search')

const RESULTS_PAGE_SIZE = 25

const SCOPES: { id: EntityScope; label: string; entityKey: SearchEntityType | null }[] = [
  { id: 'all',      label: 'All',      entityKey: null },
  { id: 'tests',    label: 'Tests',    entityKey: 'test_case' },
  { id: 'runs',     label: 'Runs',     entityKey: 'test_run' },
  { id: 'suites',   label: 'Suites',   entityKey: 'suite' },
  { id: 'defects',  label: 'Defects',  entityKey: 'defect' },
  { id: 'flaky',    label: 'Flaky',    entityKey: 'flaky_test' },
  { id: 'releases', label: 'Releases', entityKey: 'release' },
]

// ── Atoms ────────────────────────────────────────────────────────────────
function PrimaryBtn({
  children, onClick, title, disabled, type,
}: { children: React.ReactNode; onClick?: () => void; title?: string; disabled?: boolean; type?: 'button' | 'submit' }) {
  return (
    <button
      type={type ?? 'button'}
      onClick={onClick}
      title={title}
      disabled={disabled}
      className="btn-primary inline-flex items-center gap-1.5"
    >
      {children}
    </button>
  )
}

// ── Search command bar (hero) ───────────────────────────────────────────
function SearchCommandBar({
  value, onChange, onSubmit, modes, mode, onModeChange, scope, onScopeChange, scopeCounts,
  inputRef, chips,
}: {
  value: string
  onChange: (v: string) => void
  onSubmit: () => void
  modes: ModeOption[]
  mode: RetrievalMode
  onModeChange: (m: RetrievalMode) => void
  scope: EntityScope
  onScopeChange: (s: EntityScope) => void
  scopeCounts: Record<EntityScope, number>
  inputRef: React.RefObject<HTMLInputElement | null>
  /** The applied filters of the last search (absent for a plain query). */
  chips?: ReactNode
}) {
  const [focused, setFocused] = useState(false)
  return (
    <section
      aria-label="Search"
      className="rounded-xl"
      style={{
        background: 'var(--color-bg-card)',
        border: '1px solid var(--color-border)',
        padding: '14px 16px',
        marginBottom: 14,
        boxShadow: focused
          ? '0 0 0 1px color-mix(in srgb, var(--color-accent) 16%, transparent), var(--shadow-md)'
          : '0 0 0 1px color-mix(in srgb, var(--color-accent) 8%, transparent), var(--shadow-md)',
        transition: 'box-shadow 200ms ease',
      }}
    >
      <form
        onSubmit={(e) => { e.preventDefault(); onSubmit() }}
        className="flex items-center gap-2.5"
      >
        <SearchIcon className="h-4 w-4 text-[var(--color-text-muted)] flex-none" />
        <input
          ref={inputRef}
          autoFocus
          aria-label="Search across the workspace"
          aria-keyshortcuts="Meta+K"
          placeholder="Search test names, suites, error messages, build numbers, branches, jobs, Jira IDs, releases…"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          className="flex-1 bg-transparent outline-none border-0 text-[18px] font-medium text-[var(--color-text)] placeholder:text-[var(--color-text-faint)] tabular-nums"
        />
        {value && (
          <button
            type="button"
            onClick={() => { onChange(''); inputRef.current?.focus() }}
            title="Clear"
            className="inline-flex items-center justify-center h-6 w-6 rounded-md text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-bg-hover)]"
            aria-label="Clear search"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
        <Kbd>⌘K</Kbd>
        <PrimaryBtn type="submit" title="Run search">
          Search
        </PrimaryBtn>
      </form>

      {chips}

      {/* Filters row */}
      <div
        className="flex items-center gap-3 flex-wrap mt-3 pt-3"
        style={{ borderTop: '1px solid var(--color-border)' }}
      >
        <span className="eyebrow inline-flex items-center gap-1.5">Mode</span>
        <div role="radiogroup" aria-label="Search mode" className="flex items-center gap-1.5">
          {modes.map(m => (
            <Chip
              key={m.id}
              role="radio"
              ariaChecked={mode === m.id}
              active={mode === m.id}
              disabled={!m.available}
              title={m.why}
              onClick={() => { if (m.available) onModeChange(m.id) }}
            >
              {m.label}
            </Chip>
          ))}
        </div>

        <span className="w-px h-4" style={{ background: 'var(--color-border)' }} aria-hidden />

        <span className="eyebrow inline-flex items-center gap-1.5">Scope</span>
        <div role="radiogroup" aria-label="Entity scope" className="flex items-center gap-1.5 flex-wrap">
          {SCOPES.map(s => (
            <Chip
              key={s.id}
              role="radio"
              ariaChecked={scope === s.id}
              active={scope === s.id}
              onClick={() => onScopeChange(s.id)}
            >
              {s.label}
              <span
                className="font-mono text-2xs tabular-nums ml-1"
                style={{ color: scope === s.id ? 'var(--ui-info-ink)' : 'var(--color-text-faint)' }}
              >
                {formatNumber(scopeCounts[s.id] ?? 0)}
              </span>
            </Chip>
          ))}
        </div>
      </div>
    </section>
  )
}

function Chip({
  children, active, onClick, role, ariaChecked, disabled, title,
}: {
  children: React.ReactNode
  active: boolean
  onClick: () => void
  role?: 'radio'
  ariaChecked?: boolean
  disabled?: boolean
  title?: string
}) {
  return (
    <button
      type="button"
      role={role}
      aria-checked={ariaChecked}
      disabled={disabled}
      title={title}
      onClick={onClick}
      className="inline-flex items-center gap-1 px-2.5 py-1 text-[12.5px] rounded-full border transition-colors"
      style={{
        background: active ? 'color-mix(in srgb, var(--color-accent) 14%, transparent)' : 'transparent',
        borderColor: active ? 'color-mix(in srgb, var(--color-accent) 30%, transparent)' : 'var(--color-border)',
        color: active ? 'var(--color-accent)' : 'var(--color-text-muted)',
        opacity: disabled ? 0.45 : 1,
        cursor: disabled ? 'not-allowed' : 'pointer',
      }}
      onMouseEnter={(e) => { if (!active && !disabled) e.currentTarget.style.borderColor = 'var(--color-border-light)' }}
      onMouseLeave={(e) => { if (!active && !disabled) e.currentTarget.style.borderColor = 'var(--color-border)' }}
    >
      {children}
    </button>
  )
}

// ── Verdict ribbon ──────────────────────────────────────────────────────
// The three tiles that used to sit beside freshness (Latency p95, Queries
// today, Zero-result rate) had no metric behind them and were always "—".
// W12 SRCH-05 measures them; they are the usage strip under this ribbon.
function VerdictRibbon({
  totalItems, typeCount, indexStatus,
}: {
  totalItems: number
  typeCount: number
  indexStatus: IndexStatus | null
}) {
  const now = useNow()  // captured at mount — avoids impure Date.now() in render
  const freshAge = indexStatus?.last_indexed_at
    ? Math.max(0, Math.round((now - new Date(indexStatus.last_indexed_at).getTime()) / 1000))
    : null
  const freshLabel = freshAge == null ? '—'
    : freshAge < 60 ? `${freshAge}s`
    : freshAge < 3600 ? `${Math.round(freshAge / 60)}m`
    : `${Math.round(freshAge / 3600)}h`
  const streaming = freshAge != null && freshAge < 30

  const tagPulse = indexStatus?.status === 'healthy'
  return (
    <section
      aria-label="Index verdict"
      className="relative rounded-xl border overflow-hidden grid gap-0 mb-3.5"
      style={{
        gridTemplateColumns: 'minmax(0, 1fr) auto',
        background: 'var(--color-bg-card)',
        borderColor: 'var(--color-border)',
      }}
    >
      <div className="min-w-0" style={{ padding: '14px 18px' }}>
        <span
          className="inline-flex items-center gap-1.5 text-[11px] font-semibold uppercase"
          style={{ color: 'var(--status-passed)', letterSpacing: 'var(--tracking-wider)' }}
        >
          <span
            className="h-1.5 w-1.5 rounded-full"
            style={{
              background: 'var(--status-passed)',
              animation: tagPulse ? 'testlookup-pulse 1.6s ease-out infinite' : undefined,
            }}
            aria-hidden
          />
          Index ready
        </span>
        <h2 className="font-bold m-0" style={{ fontSize: 'var(--text-display-sm)', lineHeight: 1.15, letterSpacing: '-0.02em', margin: '6px 0 6px' }}>
          {formatNumber(totalItems)} item{totalItems === 1 ? '' : 's'} indexed across {typeCount} entity type{typeCount === 1 ? '' : 's'}
        </h2>
        {/* Exactly the columns global_search_service.py matches with ILIKE. */}
        <p className="text-[13px] m-0" style={{ color: 'var(--color-text-secondary)', lineHeight: 1.5, maxWidth: '80ch' }} data-testid="search-matched-fields">
          Case-insensitive substring match on test names, suite names, error messages and tags;
          run build numbers, branches, jobs and tags; defect Jira IDs; flaky test names; and
          release names and versions.
        </p>
      </div>

      <div
        className="flex items-stretch"
        style={{ borderLeft: '1px solid var(--color-border)' }}
      >
        <VerdictStat label="Index freshness" value={freshLabel} sub={streaming ? 'streaming' : 'static'} />      </div>
    </section>
  )
}

function VerdictStat({
  label, value, sub,
}: {
  label: string
  value: React.ReactNode
  sub?: React.ReactNode
}) {
  return (
    <div className="flex flex-col justify-center gap-0.5" style={{ padding: '14px 18px' }}>
      <div className="text-[10.5px] uppercase font-medium text-[var(--color-text-muted)]" style={{ letterSpacing: 'var(--tracking-wider)' }}>
        {label}
      </div>
      <div className="font-bold tabular-nums leading-[1.1]" style={{ fontSize: 18, color: 'var(--color-text)', letterSpacing: '-0.01em' }}>
        {value}
        {sub && <span className="text-[11px] font-medium text-[var(--color-text-muted)] ml-1">{sub}</span>}
      </div>
    </div>
  )
}

// ── Page ────────────────────────────────────────────────────────────────
export default function SearchPage() {
  const navigate = useNavigate()
  const project = useProjectStore(s => s.activeProject)
  const projects = useProjectStore(s => s.projects)
  const setActiveProject = useProjectStore(s => s.setActiveProject)
  const userId = useAuthStore(s => s.user?.id ?? null)
  const { isQaLead } = usePermissions()
  const now = useNow()
  const [searchParams, setSearchParams] = useSearchParams()

  // URL-driven state (deep-linkable). A semantic mode with another scope is
  // read as Tests (the URL is rewritten by the first search, `replace`).
  const [initial] = useState(() => {
    const normalized = normalizeMode(parseScope(searchParams.get('scope')), parseMode(searchParams.get('mode')))
    return { q: searchParams.get('q') ?? '', ...normalized }
  })
  const [query, setQuery] = useState(initial.q)
  const [mode, setMode] = useState<RetrievalMode>(initial.mode)
  const [scope, setScope] = useState<EntityScope>(initial.scope)
  // The one line under the command bar after a mode / scope switch.
  const [switchNote, setSwitchNote] = useState<string | null>(initial.note)
  const [isSearching, setIsSearching] = useState(false)
  const [response, setResponse] = useState<PageSearchResponse | null>(null)
  // The search `response` answers (Save search stores it; a chip's × edits it).
  const [lastSearch, setLastSearch] = useState<SearchSpec | null>(null)
  // 2026-05-15: the Results card previously sliced response.items down
  // to 12 rows and the global_search adapters capped at 50 each, so a
  // user with 84 tests had no way to see anything beyond the first
  // batch. Pagination now flows through runSearch(...,page) and the
  // adapters bump their per-type cap when narrowed.
  // Project-scoped totals from /api/v1/search/entity-counts — the
  // fallback for the chip counts (and the Searchable data rows) when no query is
  // active. Without this the chips and rows showed 0 forever even
  // though the data was sitting in Postgres.
  const [totalCounts, setTotalCounts] = useState<Record<SearchEntityType, number> | null>(null)

  const inputRef = useRef<HTMLInputElement>(null)
  const searchRequestIdRef = useRef(0)
  const selfWrittenSearchParamsRef = useRef<string | null>(null)
  const lastObservedSearchParamsRef = useRef<string | null>(null)

  // ALL_PROJECTS_ID is a frontend sentinel — omit it from API requests so
  // the backend resolves the caller's accessible project set.
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const scopedProjectId = activeProjectId && activeProjectId !== ALL_PROJECTS_ID
    ? activeProjectId
    : undefined
  const pinnedProjectId = scopedProjectId ?? null
  const boxEmpty = query.trim() === ''

  // ── What the page loads with (SWR) ───────────────────────────────────
  const { indexStatus } = useSearchIndexStatus(pinnedProjectId)
  const recents = useRecentSearches(true)
  const saved = useSavedSearches(pinnedProjectId)
  const suggested = useSuggestedSearches(pinnedProjectId, boxEmpty)
  const usage = useSearchUsage(pinnedProjectId, isQaLead)
  const saveSlot = useInlineSlot<'save'>()

  // ── Fetch project-scoped entity totals on mount + on project change ──
  useEffect(() => {
    let alive = true
    searchService.getEntityCounts(scopedProjectId)
      .then(c => { if (alive) setTotalCounts(c) })
      .catch(() => { if (alive) setTotalCounts(null) })
    return () => { alive = false }
  }, [scopedProjectId])

  // The recents the page last read, for prepending a recorded search at once
  // (only onto a list that loaded: a mutation over a read still in flight
  // would discard that read).
  const recentsDataRef = useRef(recents.data)
  useEffect(() => { recentsDataRef.current = recents.data }, [recents.data])
  const mutateRecents = recents.mutate
  const closeSave = saveSlot.close

  // ── Run a search (or browse the scope when the query is empty) ───────
  // ``page`` is optional so callers that change the query/mode/scope can
  // omit it (they want page 1); the pagination control passes the new
  // page explicitly so a user clicking "page 2" doesn't reset back to 1.
  // A page-1 search with words is RECORDED (a recent entry and a usage row,
  // written by the server after its answer); pagination and browsing are not.
  const runSearch = useCallback(async (q: string, m: RetrievalMode, s: EntityScope, page: number = 1) => {
    const requestId = ++searchRequestIdRef.current
    const trimmed = q.trim()
    const { scope: effectiveScope, mode: effectiveMode } = normalizeMode(s, m)
    if (effectiveScope !== s) setScope(effectiveScope)
    setSearchParams(prev => {
      const np = new URLSearchParams(prev)
      if (trimmed) np.set('q', trimmed); else np.delete('q')
      np.set('mode', effectiveMode)
      np.set('scope', effectiveScope)
      selfWrittenSearchParamsRef.current = np.toString()
      return np
    }, { replace: true })
    if (page === 1) closeSave()

    // Empty query: hit the API in browse mode regardless of scope. The
    // backend adapters fall back to "most-recent N" rows when ``q`` is
    // empty, so landing at /search?scope=all with no query shows real
    // data instead of an empty page. (Pre-2026-05-16 this short-circuited
    // for scope='all', producing the asymmetric behaviour the user
    // reported: Tests/Suites populated but All was blank.)
    const recordable = trimmed !== '' && page === 1

    setIsSearching(true)
    try {
      let data: PageSearchResponse
      if (trimmed && isSemanticMode(effectiveMode)) {
        // Hybrid / Semantic: GET /search, test results only. An outage or an
        // empty semantic answer comes back as keyword rows with a reason.
        const answer = await searchService.search({
          q: trimmed,
          project_id: scopedProjectId,
          search_type: effectiveMode,
          page,
          size: RESULTS_PAGE_SIZE,
          ...(recordable ? { record: true } : {}),
        })
        data = fromSemanticResponse(answer)
      } else {
        const entityKey = effectiveScope === 'all' ? null : (SCOPES.find(x => x.id === effectiveScope)?.entityKey ?? null)
        data = await searchService.globalSearch({
          q: trimmed,
          project_id: scopedProjectId,
          entity_types: entityKey ? [entityKey] : undefined,
          page,
          size: RESULTS_PAGE_SIZE,
          ...(recordable ? { record: true } : {}),
        })
      }
      if (requestId !== searchRequestIdRef.current) return
      setResponse(data)
      setLastSearch({ q: trimmed, scope: effectiveScope, mode: effectiveMode })
      // The server records it after its answer; show it in Recent searches now.
      if (recordable && recentsDataRef.current) {
        const entry: RecentSearch = {
          q: trimmed,
          scope: effectiveScope,
          mode: parseMode(data.search_type),
          project_id: scopedProjectId ?? null,
          result_count: data.total,
          counts_exact: data.counts_are_exact !== false,
          ts: new Date().toISOString(),
        }
        void mutateRecents(withRecent(recentsDataRef.current, entry), { revalidate: false })
      }
    } catch {
      if (requestId !== searchRequestIdRef.current) return
      toast.error('Search failed')
      setResponse(null)
    } finally {
      if (requestId === searchRequestIdRef.current) setIsSearching(false)
    }
  }, [scopedProjectId, setSearchParams, closeSave, mutateRecents])

  // ── URL synchronization + initial search ─────────────────────────────
  // React Router can keep this component mounted when another control
  // navigates to /search or when the user moves through browser history.
  // Treat those URL changes as new searches. Updates written by runSearch
  // are marked so their resulting render does not feed back into a second
  // request. This effect also owns the initial browse/search request.
  const searchParamsKey = searchParams.toString()
  useEffect(() => {
    if (selfWrittenSearchParamsRef.current === searchParamsKey) {
      selfWrittenSearchParamsRef.current = null
      lastObservedSearchParamsRef.current = searchParamsKey
      return
    }
    if (lastObservedSearchParamsRef.current === searchParamsKey) return
    lastObservedSearchParamsRef.current = searchParamsKey

    const params = new URLSearchParams(searchParamsKey)
    const nextQuery = params.get('q') ?? ''
    const next = normalizeMode(parseScope(params.get('scope')), parseMode(params.get('mode')))
    setQuery(nextQuery)
    setMode(next.mode)
    setScope(next.scope)
    setSwitchNote(next.note)
    void runSearch(nextQuery, next.mode, next.scope)
  }, [searchParamsKey, runSearch])

  // A project switch must refresh the result rows as well as the counts and
  // index-health panels above. Keep the previous scope separately so the
  // initial mount still issues exactly one browse/search request.
  const previousProjectIdRef = useRef(scopedProjectId)
  useEffect(() => {
    if (previousProjectIdRef.current === scopedProjectId) return
    previousProjectIdRef.current = scopedProjectId
    void runSearch(query, mode, scope)
  }, [scopedProjectId, query, mode, scope, runSearch])

  // ── The one-time move of this browser's saved searches (SRCH-03) ─────
  // Once per load, when the server's list answered (or refused: then nothing
  // is posted and the entries wait). `tl.search.recent` is dropped unread.
  const migrationRanRef = useRef(false)
  const savedAnswered = saved.data !== undefined || saved.error !== undefined
  const savedViews = saved.views
  const savedData = saved.data
  const mutateSaved = saved.mutate
  useEffect(() => {
    if (migrationRanRef.current || !savedAnswered) return
    migrationRanRef.current = true
    void migrateLegacySavedSearches({
      serverViews: savedData !== undefined ? savedViews : null,
      projectId: pinnedProjectId,
      create: createSavedSearch,
    }).then((result) => {
      if (result.posted > 0) void mutateSaved()
    })
  }, [savedAnswered, savedData, savedViews, pinnedProjectId, mutateSaved])

  // ── ⌘K shortcut: focus input (unless user is already typing in another input) ──
  const shortcuts = useMemo(() => shortcutSearches(savedViews), [savedViews])
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        const target = e.target as HTMLElement | null
        // Don't steal focus from textareas, contenteditable nodes, or other
        // search inputs the user might be typing in.
        const tag = target?.tagName?.toLowerCase()
        if (tag === 'textarea' || target?.isContentEditable) return
        if (tag === 'input' && (target as HTMLInputElement | null)?.type !== 'text' && (target as HTMLInputElement | null)?.type !== 'search') return
        e.preventDefault()
        inputRef.current?.focus()
        inputRef.current?.select()
        return
      }
      // ⌘1 / ⌘2 / ⌘3 — run the first three saved searches, in the server's order.
      if ((e.metaKey || e.ctrlKey) && /^[123]$/.test(e.key)) {
        const match = shortcuts[Number(e.key) - 1]
        if (match) {
          e.preventDefault()
          setQuery(match.q)
          setMode(match.mode)
          setScope(match.scope)
          setSwitchNote(null)
          void runSearch(match.q, match.mode, match.scope)
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [shortcuts, runSearch])

  // ── Mode / scope chips ───────────────────────────────────────────────
  const handleModeChange = (m: RetrievalMode) => {
    const next = normalizeMode(scope, m)
    setMode(next.mode)
    setScope(next.scope)
    setSwitchNote(next.note)
    void runSearch(query, next.mode, next.scope)
  }

  const handleScopeChange = (s: EntityScope) => {
    const next = modeForScope(s, mode)
    setScope(s)
    setMode(next.mode)
    setSwitchNote(next.note)
    void runSearch(query, next.mode, s)
  }

  // ── Pick a row from a list → write into input + submit immediately ───
  // (in the CURRENT project).
  const handlePick = (q: string, m: RetrievalMode, s: EntityScope) => {
    const next = normalizeMode(s, m)
    setQuery(q)
    setMode(next.mode)
    setScope(next.scope)
    setSwitchNote(null)
    void runSearch(q, next.mode, next.scope)
  }

  const handleRemoveChip = (filter: SearchParsedFilter) => {
    const next = removeFilter(lastSearch?.q ?? query, filter)
    setQuery(next)
    void runSearch(next, mode, scope)
  }

  const handleClearHistory = async () => {
    await searchService.clearRecent()
    await mutateRecents(withoutRecents(recents.data), { revalidate: false })
    toast.success('Recent searches cleared')
  }

  const handleOpenResult = (row: GlobalSearchResult) => {
    if (row.project_id) {
      const destinationProject = projects.find(candidate => candidate.id === row.project_id)
      if (destinationProject) setActiveProject(destinationProject)
    }
    navigate(row.navigation_url)
  }

  const projectNames = useMemo(
    () => Object.fromEntries(projects.map(p => [p.id, p.name])) as Record<string, string>,
    [projects],
  )

  const modes = useMemo(() => modeAvailability(scope, indexStatus), [scope, indexStatus])

  // ── Scope counts ────────────────────────────────────────────────────
  // Chip-count contract (revised 2026-05-15 to fix the "counts jump
  // when I click around" report):
  //
  //   * When scope === 'all' AND there's an active query, chips show
  //     the per-type match count from the response. This is the only
  //     case where match-count chips help — they tell the user
  //     "narrow to Tests to see those 3 hits" vs "0 in Runs".
  //
  //   * Otherwise (scope is narrowed, OR no query), chips show
  //     project-scoped totals from /api/v1/search/entity-counts.
  //     This keeps the chip's meaning stable: it's a navigation cue
  //     ("you have 84 tests in this project"), not a result counter.
  //
  // The previous implementation mixed both semantics on one screen —
  // the scoped type's chip used the (capped) match count while the
  // others used totals — so the Tests chip jumped 50 ↔ 84 as the
  // user clicked between Tests and Runs. Confusing and the count
  // wouldn't match the table either since the browse adapters cap
  // their LIMIT below the real project total.
  const entityCounts: Record<SearchEntityType, number> = useMemo(() => {
    const responseCounts = (response?.entity_counts ?? {}) as Partial<Record<SearchEntityType, number>>
    const fallback = (totalCounts ?? {}) as Partial<Record<SearchEntityType, number>>
    const useResponseCounts = !!response && scope === 'all' && query.trim() !== ''
    const pick = (type: SearchEntityType): number => {
      if (useResponseCounts) return Number(responseCounts[type] ?? 0)
      return Number(fallback[type] ?? 0)
    }
    return {
      test_case:  pick('test_case'),
      test_run:   pick('test_run'),
      suite:      pick('suite'),
      defect:     pick('defect'),
      flaky_test: pick('flaky_test'),
      release:    pick('release'),
    }
  }, [response, totalCounts, scope, query])

  // ``indexStatus.document_count`` reflects what's actually in the
  // BM25/embedding index. On a fresh deploy (or before the reindex
  // task fires for the first time) this is 0 even though the database
  // is full of data. Falling back to the entity-counts sum keeps the
  // headline honest about what the user can search across, since the
  // database read paths still work even when the vector index is
  // cold. Once reindex catches up, the indexStatus value wins.
  const databaseTotal = useMemo(
    () => Object.values(totalCounts ?? {}).reduce((s, n) => s + (n as number), 0),
    [totalCounts],
  )
  const totalIndexed = indexStatus?.document_count
    ? indexStatus.document_count
    : databaseTotal
  const scopeCounts: Record<EntityScope, number> = useMemo(() => {
    const summed = Object.values(entityCounts).reduce((s, n) => s + n, 0)
    return {
      // "All" chip: sum of the entity counts. Same source as the
      // individual chips so the numbers tally.
      all:      summed || totalIndexed,
      tests:    entityCounts.test_case,
      runs:     entityCounts.test_run,
      suites:   entityCounts.suite,
      defects:  entityCounts.defect,
      flaky:    entityCounts.flaky_test,
      releases: entityCounts.release,
    }
  }, [entityCounts, totalIndexed])

  const projectLabel = project?.name ?? 'All Projects'

  // The crumb the old header drew (workspace chip · counts · freshness), as
  // the compact header's one line.
  const subtitle = [
    `Find tests, runs, suites, defects, releases across ${projectLabel}`,
    `${formatNumber(totalIndexed)} items indexed`,
    indexStatus?.last_indexed_at
      ? `fresh ${relativeTime(new Date(indexStatus.last_indexed_at).getTime(), now)}`
      : null,
  ].filter(Boolean).join(' · ')

  // Save search: after ANY search with words — one that found nothing too (a
  // "watch" query worth re-running later). The form opens at the top of the
  // card that answered (Results, or No results).
  const savable = lastSearch !== null && lastSearch.q !== ''
  const saveButton = savable ? (
    <button
      type="button"
      className="btn-secondary btn-sm inline-flex items-center gap-1 whitespace-nowrap"
      onClick={() => saveSlot.open('save')}
    >
      <Bookmark aria-hidden="true" className="h-3.5 w-3.5" />
      Save search
    </button>
  ) : null
  const saveForm = saveSlot.openKey === 'save' && lastSearch && savable ? (
    <div className="border-b border-[var(--color-border)] p-3">
      <SaveSearchForm
        search={lastSearch}
        projectId={pinnedProjectId}
        projectName={project?.name ?? null}
        slot={saveSlot}
        onClose={saveSlot.close}
        onSaved={() => { saveSlot.close(); void mutateSaved() }}
      />
    </div>
  ) : null

  return (
    <PageShell>
      <PageHeader compact title="Search" subtitle={subtitle} helpTopic={HELP_TOPIC} />

      <SearchCommandBar
        value={query}
        onChange={setQuery}
        onSubmit={() => { void runSearch(query, mode, scope) }}
        modes={modes}
        mode={mode}
        onModeChange={handleModeChange}
        scope={scope}
        onScopeChange={handleScopeChange}
        scopeCounts={scopeCounts}
        inputRef={inputRef}
        chips={<SearchChipsRow parsed={response?.parsed} onRemove={handleRemoveChip} />}
      />

      {switchNote && (
        <p role="status" data-search-switch-note="" className="-mt-1.5 mb-3 text-xs text-[var(--color-text-secondary)]">
          {switchNote}
        </p>
      )}

      <VerdictRibbon
        totalItems={totalIndexed}
        typeCount={6}
        indexStatus={indexStatus}
      />

      {isQaLead && (
        <SearchUsageStrip usage={usage.data} loading={usage.isLoading} failed={usage.error !== undefined} />
      )}

      <SearchFallbackBanner reason={response?.fallback_reason} message={response?.fallback_message} />

      {response?.result_status === 'partial' && (
        <div
          role="status"
          className="mb-3 rounded-md border border-[var(--status-broken-bd)] bg-[var(--status-broken-bg)] px-3 py-2 text-[12px] text-[var(--status-broken)]"
        >
          {response.failed_entity_types?.length
            ? `Some search sources were unavailable: ${response.failed_entity_types.join(', ')}. `
            : 'Search sampled the most relevant matches. '}
          Counts shown are lower bounds.
        </div>
      )}

      {/* Live results — only when a query has been run */}
      {response && response.items.length > 0 && (
        <>
          <SearchCardShell
            title={
              <>
                Results <span className="text-[11.5px] font-normal text-[var(--color-text-muted)] ml-2">
                  <strong>{formatNumber(response.total)}{response.counts_are_exact === false ? '+' : ''}</strong> across {scope === 'all' ? '6' : '1'} type{scope === 'all' ? 's' : ''}
                </span>
                {/* The header's release picker is visible on this page and does
                    NOT apply here. Without saying so, a reader with 2.4.0
                    selected takes these results as 2.4.0's — and the one result
                    they were hunting for, last run in 2.3.0, looks like proof
                    the test is gone. The reason comes from the API's own scope
                    block so there is one sentence, not two that drift. */}
                <span className="ml-2 align-middle">
                  <AllReleasesBadge reason={response.scope?.note} />
                </span>
              </>
            }
            rightSlot={
              <>
                {saveButton}
                <span className="font-mono">
                  <code className="text-2xs">{response.search_type}</code>
                  {' · '}
                  showing {((response.page - 1) * response.size) + 1}–{((response.page - 1) * response.size) + response.items.length} of {response.counts_are_exact === false ? 'at least ' : ''}{response.total}
                </span>
              </>
            }
          >
            {saveForm}
            <div className="flex flex-col">
              {response.items.map(r => <ResultRow key={`${r.entity_type}-${r.entity_id}`} row={r} onOpen={() => handleOpenResult(r)} />)}
            </div>
          </SearchCardShell>
          {response.pages > 1 && (
            <div className="mt-3">
              <Pagination
                page={response.page}
                pages={response.pages}
                total={response.total}
                onChange={(p) => {
                  // Rerun the same query with the new page. Scroll to top
                  // of the Results card so the user sees the first row
                  // of the new page rather than the last one of the old.
                  void runSearch(query, mode, scope, p)
                  if (typeof window !== 'undefined') window.scrollTo({ top: 0, behavior: 'smooth' })
                }}
              />
            </div>
          )}
        </>
      )}

      {response && response.items.length === 0 && (
        <SearchCardShell title="No results" rightSlot={<>{saveButton}<span>0 matches</span></>}>
          {saveForm}
          <div className="px-4 py-8 text-center text-sm text-[var(--color-text-secondary)]">
            {query.trim() ? (
              <>
                <p className="m-0 mb-2">Nothing matched <code className="font-mono text-xs">{query}</code>.</p>
                <p className="text-xs m-0 text-[var(--color-text-muted)]">
                  Broaden the scope or try a shorter part of the name.
                </p>
              </>
            ) : (
              <>
                <p className="m-0 mb-2">Nothing to browse in <code className="font-mono text-xs">{projectLabel}</code> yet.</p>
                <p className="text-xs m-0 text-[var(--color-text-muted)]">
                  Ingest a test run or pick a different project from the top bar to populate the index.
                </p>
              </>
            )}
          </div>
        </SearchCardShell>
      )}

      {/* Browse-mode helper grid — suggested / recent / saved searches +
          searchable data. Visible whenever no query is active, so a user
          landing at /search with scope=all (browse mode) still sees
          Searchable data alongside the auto-loaded results.
          Hidden once the user types a query so the screen focuses on
          their search results. */}
      {boxEmpty && (
        <div className="grid grid-cols-[minmax(0,1.65fr)_minmax(0,1fr)] gap-3.5 mt-3.5">
          <div className="flex flex-col gap-3.5 min-w-0">
            {pinnedProjectId && (
              <SuggestedSearchesCard
                suggestions={suggested.data}
                failed={suggested.error !== undefined}
                onPick={(row: SearchSuggestion) => handlePick(row.q, 'keyword', parseScope(row.scope))}
              />
            )}
            <RecentSearchesCard
              recents={recents.data}
              loading={recents.isLoading}
              failed={recents.error !== undefined}
              projectNames={projectNames}
              pinnedProjectId={pinnedProjectId}
              onPick={(r) => handlePick(r.q, parseMode(r.mode), parseScope(r.scope))}
              onClear={handleClearHistory}
            />
            <SavedSearchesCard
              views={savedViews}
              userId={userId}
              onRun={(s) => handlePick(s.q, s.mode, s.scope)}
              onChanged={() => { void mutateSaved() }}
            />
          </div>
          <div className="flex flex-col gap-3.5 min-w-0">
            <SearchableDataCard totals={totalCounts} indexStatus={indexStatus} projectId={pinnedProjectId} />
          </div>
        </div>
      )}

      {isSearching && (
        <div className="fixed bottom-4 right-4 z-20 inline-flex items-center gap-2 px-3 py-2 rounded-md text-xs text-[var(--color-text-secondary)] bg-[var(--color-bg-card)] border border-[var(--color-border)] shadow-lg">
          <LoadingSpinner size="sm" />
          Searching…
        </div>
      )}

      <div className="fixed bottom-4 left-4 lg:hidden text-xs text-[var(--color-text-muted)] bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-md px-3 py-2 z-10">
        Wider screen recommended for the full layout.
      </div>
    </PageShell>
  )
}

// ── Result row ──────────────────────────────────────────────────────────
function ResultRow({ row, onOpen }: { row: GlobalSearchResult; onOpen: () => void }) {
  const Icon = ENTITY_ICON[row.entity_type] ?? SearchIcon
  const palette = ENTITY_PALETTE[row.entity_type] ?? { bg: 'var(--color-bg-secondary)', fg: 'var(--color-text-muted)' }
  return (
    <button
      type="button"
      onClick={onOpen}
      className="grid items-center gap-2.5 text-left transition-colors hover:bg-[var(--color-bg-hover)]"
      style={{ gridTemplateColumns: '24px 1fr auto auto', padding: '10px 16px', borderBottom: '1px solid var(--color-border)' }}
    >
      <span className="inline-flex items-center justify-center rounded-md flex-none" style={{ width: 24, height: 24, background: palette.bg, color: palette.fg }}>
        <Icon className="h-3 w-3" />
      </span>
      <div className="min-w-0">
        <div className="text-[12.5px] text-[var(--color-text)] truncate font-medium">{row.title}</div>
        <div className="text-[10.5px] text-[var(--color-text-muted)] truncate">
          {row.entity_type.replace('_', ' ')}
          {row.subtitle ? <> · {row.subtitle}</> : null}
          {row.project_name ? <> · {row.project_name}</> : null}
        </div>
      </div>
      <span className="text-[10.5px] text-[var(--color-text-faint)] tabular-nums whitespace-nowrap">
        rel {row.relevance_score.toFixed(2)}
      </span>
      <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-faint)]" />
    </button>
  )
}

const ENTITY_ICON: Record<SearchEntityType, typeof SearchIcon> = {
  test_case:  TestTube,
  test_run:   Zap,
  suite:      Layers,
  defect:     ShieldCheck,
  flaky_test: AlertTriangle,
  release:    Package,
}

const ENTITY_PALETTE: Record<SearchEntityType, { bg: string; fg: string }> = {
  test_case:  { bg: 'color-mix(in srgb, var(--color-accent) 14%, transparent)',  fg: 'var(--color-accent)' },
  test_run:   { bg: 'color-mix(in srgb, var(--status-passed) 14%, transparent)',   fg: 'var(--status-passed)' },
  suite:      { bg: 'color-mix(in srgb, var(--status-flaky) 14%, transparent)', fg: 'var(--status-flaky)' },
  defect:     { bg: 'var(--color-cyan-soft)',  fg: 'var(--color-cyan)' },
  flaky_test: { bg: 'color-mix(in srgb, var(--status-broken) 14%, transparent)',  fg: 'var(--status-broken)' },
  release:    { bg: 'var(--color-bg-secondary)', fg: 'var(--color-text-muted)' },
}
