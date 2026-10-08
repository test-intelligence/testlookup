/**
 * Search — hero-first redesign per design_handoff_search/README.md.
 *
 * Layout (1320 px max-width, 14 px section gaps):
 *   Header  → the template's `PageHeader` (UX redesign P6: compact, **?** =
 *             the search topic); the old crumb (workspace · index counts ·
 *             freshness) is its one-line subtitle.
 *   Hero    → SearchCommandBar — large autofocused input + ⌘K kbd + Search
 *             button, with mode chips (only Keyword is available) and
 *             scope chips (All · Tests · Runs · Suites · Defects · Flaky ·
 *             Releases) each carrying an item-count badge.
 *   Verdict → "Index ready" tag + headline "N items indexed across K entity
 *             types" + a lede naming exactly which fields are matched, and
 *             the index-freshness stat.
 *   Body    → 1.65fr | 1fr.
 *     Left  → Recent searches · Saved searches (⌘1-⌘3, only when some exist).
 *     Right → Index health (per entity).
 *
 * Global search is a case-insensitive substring match (SQL ILIKE in
 * global_search_service.py). There is no field:value syntax, no ranking
 * model and no query-volume metric, so the page claims none of them.
 *
 * Data: deep-links via querystring (`?q=…&mode=…&scope=…`). Every submit
 * goes through `searchService.globalSearch`, narrowed by `entity_types`
 * when a scope chip is picked. Recent + Saved searches persist to
 * localStorage.
 */
import {
  useCallback, useEffect, useMemo, useRef, useState,
} from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  AlertTriangle, Bookmark, ChevronRight, Database,
  History as HistoryIcon, Layers, Package,
  Search as SearchIcon, ShieldCheck, TestTube, X, Zap,
} from 'lucide-react'
import toast from 'react-hot-toast'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import AllReleasesBadge from '@/components/ui/AllReleasesBadge'
import { useNow } from '@/hooks/useNow'
import PageShell from '@/components/layout/PageShell'
import PageHeader from '@/components/ui/PageHeader'
import Pagination from '@/components/ui/Pagination'
import { helpTopicParam } from '@/components/help/helpTopics'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { searchService } from '@/services/searchService'
import type { SearchType } from '@/services/searchService'
import type {
  GlobalSearchResponse, GlobalSearchResult, IndexStatus, SearchEntityType,
} from '@/types/search'

/** The page's help topic (the header's **?**). */
const HELP_TOPIC = helpTopicParam('/search')

// ── Types ────────────────────────────────────────────────────────────────
type RetrievalMode = SearchType                                  // 'hybrid' | 'keyword' | 'semantic'
type EntityScope = 'all' | 'tests' | 'runs' | 'suites' | 'defects' | 'flaky' | 'releases'

// Global search runs ONE retrieval strategy: SQL ILIKE substring matching
// (see global_search_service.py -- no BM25, no embeddings, no re-ranker). The
// Hybrid and Semantic chips never reached the API, so selecting one changed
// the label and nothing else, while the provenance footer went on naming an
// embedding model and a ranker version that are not involved. Marked
// unavailable rather than deleted: /api/v1/search does implement all three,
// but only over test cases, so pointing this page at it would silently drop
// runs, suites, defects, flaky tests and releases from every result.
const MODES: { id: RetrievalMode; label: string; available: boolean; why?: string }[] = [
  { id: 'keyword',  label: 'Keyword',  available: true },
  { id: 'hybrid',   label: 'Hybrid',   available: false, why: 'Hybrid retrieval is not available for global search — it spans six entity types and only keyword matching covers all of them.' },
  { id: 'semantic', label: 'Semantic', available: false, why: 'Semantic retrieval is not available for global search — it spans six entity types and only keyword matching covers all of them.' },
]

function normalizeRetrievalMode(mode: RetrievalMode | null | undefined): RetrievalMode {
  const requested = MODES.find(candidate => candidate.id === mode)
  return requested?.available ? requested.id : 'keyword'
}

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

function normalizeEntityScope(scope: unknown): EntityScope {
  return typeof scope === 'string' && SCOPES.some(candidate => candidate.id === scope)
    ? scope as EntityScope
    : 'all'
}

interface RecentSearch {
  id: string
  query: string
  mode: RetrievalMode
  scope: EntityScope
  resultCount: number
  ts: number
}

interface SavedSearch {
  id: string
  label: string
  query: string
  mode: RetrievalMode
  scope: EntityScope
  resultCount?: number
  slot: 1 | 2 | 3
}

// ── Local storage keys + helpers ────────────────────────────────────────
const RECENT_KEY = 'tl.search.recent'
const SAVED_KEY  = 'tl.search.saved'
const MAX_RECENT = 8

function isStorageRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function normalizeRecentSearch(value: unknown): RecentSearch | null {
  if (!isStorageRecord(value)
    || typeof value.id !== 'string'
    || typeof value.query !== 'string'
    || typeof value.resultCount !== 'number'
    || !Number.isFinite(value.resultCount)
    || typeof value.ts !== 'number'
    || !Number.isFinite(value.ts)) return null

  return {
    id: value.id,
    query: value.query,
    mode: normalizeRetrievalMode(value.mode as RetrievalMode | undefined),
    scope: normalizeEntityScope(value.scope),
    resultCount: value.resultCount,
    ts: value.ts,
  }
}

function normalizeSavedSearch(value: unknown): SavedSearch | null {
  if (!isStorageRecord(value)
    || typeof value.id !== 'string'
    || typeof value.query !== 'string'
    || ![1, 2, 3].includes(value.slot as number)) return null

  const resultCount = typeof value.resultCount === 'number' && Number.isFinite(value.resultCount)
    ? value.resultCount
    : undefined
  return {
    id: value.id,
    label: typeof value.label === 'string' ? value.label : value.query,
    query: value.query,
    mode: normalizeRetrievalMode(value.mode as RetrievalMode | undefined),
    scope: normalizeEntityScope(value.scope),
    resultCount,
    slot: value.slot as 1 | 2 | 3,
  }
}

function readRecents(): RecentSearch[] {
  try {
    const raw = localStorage.getItem(RECENT_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return []
    const normalized = parsed
      .map(normalizeRecentSearch)
      .filter((row): row is RecentSearch => row !== null)
      .slice(0, MAX_RECENT)
    writeRecents(normalized)
    return normalized
  } catch { return [] }
}

function writeRecents(rs: RecentSearch[]) {
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(rs.slice(0, MAX_RECENT))) } catch { /* noop */ }
}

function readSaved(): SavedSearch[] {
  try {
    const raw = localStorage.getItem(SAVED_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return []
    const normalized = parsed
      .map(normalizeSavedSearch)
      .filter((row): row is SavedSearch => row !== null)
      .slice(0, 3)
    writeSaved(normalized)
    return normalized
  } catch { return [] }
}

function writeSaved(s: SavedSearch[]) {
  try { localStorage.setItem(SAVED_KEY, JSON.stringify(s.slice(0, 3))) } catch { /* noop */ }
}

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
      className="inline-flex items-center gap-1.5 px-4 py-1.5 text-[13px] font-medium rounded-md transition-colors disabled:opacity-50"
      style={{ background: 'var(--color-btn-primary-bg)', color: 'white' }}
      onMouseEnter={(e) => !disabled && (e.currentTarget.style.background = 'var(--color-btn-primary-hover)')}
      onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--color-btn-primary-bg)')}
    >
      {children}
    </button>
  )
}

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd
      className="font-mono text-[10.5px] inline-flex items-center gap-0.5 px-1.5 py-px rounded-sm whitespace-nowrap"
      style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text-secondary)' }}
    >
      {children}
    </kbd>
  )
}

// ── Search command bar (hero) ───────────────────────────────────────────
function SearchCommandBar({
  value, onChange, onSubmit, mode, onModeChange, scope, onScopeChange, scopeCounts,
  inputRef,
}: {
  value: string
  onChange: (v: string) => void
  onSubmit: () => void
  mode: RetrievalMode
  onModeChange: (m: RetrievalMode) => void
  scope: EntityScope
  onScopeChange: (s: EntityScope) => void
  scopeCounts: Record<EntityScope, number>
  inputRef: React.RefObject<HTMLInputElement | null>
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
          ? '0 0 0 1px color-mix(in srgb, var(--color-accent) 16%, transparent), 0 8px 28px rgba(0,0,0,0.25)'
          : '0 0 0 1px color-mix(in srgb, var(--color-accent) 8%, transparent), 0 8px 28px rgba(0,0,0,0.25)',
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

      {/* Filters row */}
      <div
        className="flex items-center gap-3 flex-wrap mt-3 pt-3"
        style={{ borderTop: '1px solid var(--color-border)' }}
      >
        <span
          className="inline-flex items-center gap-1.5 text-[10.5px] uppercase font-medium text-[var(--color-text-muted)]"
          style={{ letterSpacing: 'var(--tracking-wider)' }}
        >
          Mode
        </span>
        <div role="radiogroup" aria-label="Search mode" className="flex items-center gap-1.5">
          {MODES.map(m => (
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

        <span
          className="inline-flex items-center gap-1.5 text-[10.5px] uppercase font-medium text-[var(--color-text-muted)]"
          style={{ letterSpacing: 'var(--tracking-wider)' }}
        >
          Scope
        </span>
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
                className="font-mono text-[10.5px] tabular-nums ml-1"
                style={{ color: scope === s.id ? 'rgba(255,255,255,0.75)' : 'var(--color-text-faint)' }}
              >
                {Intl.NumberFormat().format(scopeCounts[s.id] ?? 0)}
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
          {Intl.NumberFormat().format(totalItems)} item{totalItems === 1 ? '' : 's'} indexed across {typeCount} entity type{typeCount === 1 ? '' : 's'}
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

// ── Lists (recent / saved / suggested) ──────────────────────────────────
interface ListRowProps {
  Icon: typeof HistoryIcon
  iconBg: string
  iconFg: string
  query: string
  meta: React.ReactNode
  trailing: React.ReactNode
  onClick: () => void
  onSecondary?: () => void
  secondaryLabel?: string
}

function ListRow({
  Icon, iconBg, iconFg, query, meta, trailing, onClick, onSecondary, secondaryLabel,
}: ListRowProps) {
  return (
    <div
      className="grid items-center gap-2.5 transition-colors"
      style={{
        gridTemplateColumns: '22px 1fr auto auto',
        padding: '10px 16px',
        borderBottom: '1px solid var(--color-border)',
      }}
    >
      <span
        className="inline-flex items-center justify-center rounded-md flex-none"
        style={{ width: 22, height: 22, background: iconBg, color: iconFg }}
      >
        <Icon className="h-3 w-3" />
      </span>
      <button
        type="button"
        onClick={onClick}
        className="text-left min-w-0 hover:bg-[var(--color-bg-hover)] -mx-1 px-1 py-0.5 rounded-sm transition-colors"
      >
        <div className="text-[12.5px] text-[var(--color-text)] truncate" title={query}>
          {renderHighlightedQuery(query)}
        </div>
        <div className="text-[10.5px] text-[var(--color-text-muted)] truncate mt-0.5">{meta}</div>
      </button>
      <span className="text-[11px] text-[var(--color-text-muted)] tabular-nums">{trailing}</span>
      {onSecondary ? (
        <button
          type="button"
          onClick={onSecondary}
          title={secondaryLabel}
          className="text-[11px] text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)] px-1.5 py-0.5 rounded-sm"
          aria-label={secondaryLabel}
        >
          <X className="h-3 w-3" />
        </button>
      ) : <span aria-hidden style={{ width: 12 }} />}
    </div>
  )
}

/**
 * Render a query string with `field:value` tokens as inline <code>. This is
 * deliberately minimal — no parser, just a regex tokenizer over the design's
 * canonical syntax tokens.
 */
function renderHighlightedQuery(q: string): React.ReactNode {
  const parts: React.ReactNode[] = []
  // Match: word:value (no spaces), or "phrase" quoted, or word:~fuzzy.
  const re = /(\b[a-z_]+:[^\s"]+|"[^"]+"|\b[a-z_]+:~[^\s]+)/gi
  let last = 0
  let m: RegExpExecArray | null
  let key = 0
  while ((m = re.exec(q)) !== null) {
    if (m.index > last) parts.push(<span key={key++}>{q.slice(last, m.index)}</span>)
    parts.push(
      <code
        key={key++}
        className="font-mono text-[11px] px-1 py-px rounded-sm mx-0.5"
        style={{ background: 'color-mix(in srgb, var(--color-accent) 6%, transparent)', border: '1px solid color-mix(in srgb, var(--color-accent) 18%, transparent)', color: 'var(--color-accent)' }}
      >
        {m[0]}
      </code>,
    )
    last = m.index + m[0].length
  }
  if (last < q.length) parts.push(<span key={key}>{q.slice(last)}</span>)
  return parts
}

function RecentList({
  rows, onPick, onClear,
}: {
  rows: RecentSearch[]
  onPick: (r: RecentSearch) => void
  onClear: () => void
}) {
  return (
    <CardShell
      title={
        <span className="inline-flex items-center gap-2">
          <HistoryIcon className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
          Recent searches
        </span>
      }
      rightSlot={
        rows.length > 0 && (
          <button
            type="button"
            onClick={onClear}
            className="hover:underline"
            style={{ color: 'var(--color-accent)' }}
          >
            Clear history
          </button>
        )
      }
    >
      {rows.length === 0 ? (
        <div className="px-4 py-6 text-center">
          <p className="text-[13px] m-0 mb-2" style={{ color: 'var(--color-text-secondary)' }}>
            Your queries will appear here.
          </p>
          <p className="text-[12px] m-0 text-[var(--color-text-muted)]">
            Start typing a search above.
          </p>
        </div>
      ) : (
        <div>
          {rows.map(r => (
            <ListRow
              key={r.id}
              Icon={HistoryIcon}
              iconBg="color-mix(in srgb, var(--color-accent) 14%, transparent)"
              iconFg="var(--color-accent)"
              query={r.query}
              meta={
                <>
                  <strong className="text-[var(--color-text-secondary)] font-semibold">
                    {r.resultCount} result{r.resultCount === 1 ? '' : 's'}
                  </strong>
                  {' · '}
                  {relativeTime(r.ts)}
                  {' · '}
                  {r.mode}
                  {' · '}
                  {r.scope}
                </>
              }
              trailing={<Kbd>↵</Kbd>}
              onClick={() => onPick(r)}
            />
          ))}
        </div>
      )}
    </CardShell>
  )
}

// Nothing on this page creates a saved search any more (no bookmark control
// exists), so the card renders only rows a browser already holds — never an
// empty state pointing at a button that is not there.
function SavedList({
  rows, onPick, onUnsave,
}: {
  rows: SavedSearch[]
  onPick: (r: SavedSearch) => void
  onUnsave: (id: string) => void
}) {
  if (rows.length === 0) return null
  return (
    <CardShell
      title={
        <span className="inline-flex items-center gap-2">
          <Bookmark className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
          Saved searches
        </span>
      }
    >
      <div>
        {rows.map(r => (
          <ListRow
            key={r.id}
            Icon={Bookmark}
            iconBg="color-mix(in srgb, var(--status-broken) 14%, transparent)"
            iconFg="var(--status-broken)"
            query={r.label || r.query}
            meta={
              <>
                <strong className="text-[var(--color-text-secondary)] font-semibold">scope:</strong>{' '}{r.scope}
                {' · '}
                <strong className="text-[var(--color-text-secondary)] font-semibold">filter:</strong>{' '}
                <code className="font-mono text-[11px]" style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid var(--color-border)', borderRadius: 3, padding: '1px 5px' }}>{r.query}</code>
              </>
            }
            trailing={<Kbd>⌘{r.slot}</Kbd>}
            onClick={() => onPick(r)}
            onSecondary={() => onUnsave(r.id)}
            secondaryLabel={`Remove saved search "${r.label || r.query}"`}
          />
        ))}
      </div>
    </CardShell>
  )
}

// ── Right rail ──────────────────────────────────────────────────────────
interface EntityHealth {
  entity: SearchEntityType
  label: string
  count: number
  status: 'live' | 'embedding' | 'down'
  detail: string
  Icon: typeof TestTube
  iconBg: string
  iconFg: string
}

function buildEntityHealth(indexStatus: IndexStatus | null, entityCounts: Record<SearchEntityType, number>): EntityHealth[] {
  const lastSync = indexStatus?.last_indexed_at ? Date.now() - new Date(indexStatus.last_indexed_at).getTime() : null
  const isDown = indexStatus?.status === 'unavailable'
  const isStale = lastSync != null && lastSync > 5 * 60 * 1000
  const overall: EntityHealth['status'] = isDown || isStale ? 'down' : 'live'
  const lastSyncLabel = lastSync == null ? 'unknown'
    : lastSync < 60_000 ? `${Math.max(1, Math.round(lastSync / 1000))}s`
    : lastSync < 3_600_000 ? `${Math.round(lastSync / 60_000)}m`
    : `${Math.round(lastSync / 3_600_000)}h`

  return [
    { entity: 'test_case',  label: 'Tests',    count: entityCounts.test_case ?? 0,  status: overall, detail: lastSyncLabel, Icon: TestTube,    iconBg: 'color-mix(in srgb, var(--color-accent) 14%, transparent)', iconFg: 'var(--color-accent)' },
    { entity: 'test_run',   label: 'Runs',     count: entityCounts.test_run ?? 0,   status: overall, detail: lastSyncLabel, Icon: Zap,         iconBg: 'color-mix(in srgb, var(--status-passed) 14%, transparent)',  iconFg: 'var(--status-passed)' },
    { entity: 'suite',      label: 'Suites',   count: entityCounts.suite ?? 0,      status: overall, detail: lastSyncLabel, Icon: Layers,      iconBg: 'color-mix(in srgb, var(--status-flaky) 14%, transparent)', iconFg: 'var(--status-flaky)' },
    { entity: 'defect',     label: 'Defects',  count: entityCounts.defect ?? 0,     status: overall, detail: lastSyncLabel, Icon: ShieldCheck, iconBg: 'rgba(236,72,153,0.14)',  iconFg: '#f9a8d4' },
    { entity: 'flaky_test', label: 'Flaky',    count: entityCounts.flaky_test ?? 0, status: overall, detail: lastSyncLabel, Icon: AlertTriangle, iconBg: 'color-mix(in srgb, var(--status-broken) 14%, transparent)', iconFg: 'var(--status-broken)' },
    { entity: 'release',    label: 'Releases', count: entityCounts.release ?? 0,    status: overall, detail: lastSyncLabel, Icon: Package,     iconBg: 'var(--color-bg-secondary)', iconFg: 'var(--color-text-muted)' },
  ]
}

function IndexHealthCard({ rows }: { rows: EntityHealth[] }) {
  const live = rows.filter(r => r.status === 'live').length
  return (
    <CardShell
      title={
        <span className="inline-flex items-center gap-2">
          <Database className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
          Index health
        </span>
      }
      rightSlot={<span>{live} of {rows.length} live</span>}
    >
      <div className="px-4 py-3 flex flex-col gap-2">
        {rows.map(r => {
          const Icon = r.Icon
          const statusPalette = r.status === 'live'
            ? { bg: 'color-mix(in srgb, var(--status-passed) 14%, transparent)', bd: 'color-mix(in srgb, var(--status-passed) 30%, transparent)', fg: 'var(--status-passed)', label: 'Live' }
            : r.status === 'embedding'
              ? { bg: 'color-mix(in srgb, var(--status-broken) 14%, transparent)', bd: 'color-mix(in srgb, var(--status-broken) 30%, transparent)', fg: 'var(--status-broken)', label: 'Embedding' }
              : { bg: 'color-mix(in srgb, var(--status-failed) 14%, transparent)', bd: 'color-mix(in srgb, var(--status-failed) 30%, transparent)', fg: 'var(--status-failed)', label: 'Down' }
          return (
            <div
              key={r.entity}
              className="grid items-center gap-2.5 rounded-md border"
              style={{ gridTemplateColumns: '24px 1fr auto', padding: '8px 12px', background: 'var(--color-bg)', borderColor: 'var(--color-border)' }}
            >
              <span className="inline-flex items-center justify-center rounded-full" style={{ width: 24, height: 24, background: r.iconBg, color: r.iconFg }}>
                <Icon className="h-3 w-3" />
              </span>
              <div className="min-w-0">
                <div className="text-[12.5px] text-[var(--color-text)] font-medium">{r.label}</div>
                <div className="text-[10.5px] text-[var(--color-text-muted)] truncate">
                  {Intl.NumberFormat().format(r.count)} items · last sync {r.detail}
                </div>
              </div>
              <span
                className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10.5px] font-semibold"
                aria-label={`${r.label} index: ${statusPalette.label}`}
                style={{ background: statusPalette.bg, border: `1px solid ${statusPalette.bd}`, color: statusPalette.fg }}
              >
                {statusPalette.label}
              </span>
            </div>
          )
        })}
      </div>
    </CardShell>
  )
}

// ── Shared shell ────────────────────────────────────────────────────────
function CardShell({
  title, rightSlot, children,
}: { title: React.ReactNode; rightSlot?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div
      className="overflow-hidden rounded-xl"
      style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)' }}
    >
      <div
        className="flex items-center justify-between gap-2.5 px-4 py-3"
        style={{ borderBottom: '1px solid var(--color-border)' }}
      >
        <h3 className="text-[13px] font-semibold m-0 text-[var(--color-text)]">{title}</h3>
        {rightSlot && <div className="flex items-center gap-2.5 text-[12px] text-[var(--color-text-muted)]">{rightSlot}</div>}
      </div>
      {children}
    </div>
  )
}

// ── Helpers ─────────────────────────────────────────────────────────────
function relativeTime(ts: number): string {
  const ms = Date.now() - ts
  if (Number.isNaN(ms) || ms < 0) return '—'
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

// ── Page ────────────────────────────────────────────────────────────────
export default function SearchPage() {
  const navigate = useNavigate()
  const project = useProjectStore(s => s.activeProject)
  const projects = useProjectStore(s => s.projects)
  const setActiveProject = useProjectStore(s => s.setActiveProject)
  const [searchParams, setSearchParams] = useSearchParams()

  // URL-driven state (deep-linkable)
  const [query, setQuery] = useState(() => searchParams.get('q') ?? '')
  const [mode, setMode] = useState<RetrievalMode>(() =>
    normalizeRetrievalMode(searchParams.get('mode') as RetrievalMode | null),
  )
  const [scope, setScope] = useState<EntityScope>(() => normalizeEntityScope(searchParams.get('scope')))
  const [isSearching, setIsSearching] = useState(false)
  const [response, setResponse] = useState<GlobalSearchResponse | null>(null)
  // 2026-05-15: the Results card previously sliced response.items down
  // to 12 rows and the global_search adapters capped at 50 each, so a
  // user with 84 tests had no way to see anything beyond the first
  // batch. Pagination now flows through runSearch(...,page) and the
  // adapters bump their per-type cap when narrowed.
  const [indexStatus, setIndexStatus] = useState<IndexStatus | null>(null)
  // Project-scoped totals from /api/v1/search/entity-counts — the
  // fallback for the chip + Index Health counts when no query is
  // active. Without this the chips and rows showed 0 forever even
  // though the data was sitting in Postgres.
  const [totalCounts, setTotalCounts] = useState<Record<SearchEntityType, number> | null>(null)
  const [recents, setRecents] = useState<RecentSearch[]>(() => readRecents())
  const [saved, setSaved] = useState<SavedSearch[]>(() => readSaved())

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

  // ── Fetch index status on mount + on project change ──────────────────
  useEffect(() => {
    let alive = true
    searchService.getIndexStatus(scopedProjectId)
      .then(s => { if (alive) setIndexStatus(s) })
      .catch(() => { if (alive) setIndexStatus({ status: 'unknown', document_count: 0, last_indexed_at: null }) })
    return () => { alive = false }
  }, [scopedProjectId])

  // ── Fetch project-scoped entity totals on mount + on project change ──
  useEffect(() => {
    let alive = true
    searchService.getEntityCounts(scopedProjectId)
      .then(c => { if (alive) setTotalCounts(c) })
      .catch(() => { if (alive) setTotalCounts(null) })
    return () => { alive = false }
  }, [scopedProjectId])

  // ── Run a search (or browse the scope when the query is empty) ───────
  // ``page`` is optional so callers that change the query/mode/scope can
  // omit it (they want page 1); the pagination control passes the new
  // page explicitly so a user clicking "page 2" doesn't reset back to 1.
  const runSearch = useCallback(async (q: string, m: RetrievalMode, s: EntityScope, page: number = 1) => {
    const requestId = ++searchRequestIdRef.current
    const trimmed = q.trim()
    const effectiveMode = normalizeRetrievalMode(m)
    if (effectiveMode !== m) setMode(effectiveMode)
    setSearchParams(prev => {
      const np = new URLSearchParams(prev)
      if (trimmed) np.set('q', trimmed); else np.delete('q')
      np.set('mode', effectiveMode)
      np.set('scope', s)
      selfWrittenSearchParamsRef.current = np.toString()
      return np
    }, { replace: true })

    // Empty query: hit the API in browse mode regardless of scope. The
    // backend adapters fall back to "most-recent N" rows when ``q`` is
    // empty, so landing at /search?scope=all with no query shows real
    // data instead of an empty page. (Pre-2026-05-16 this short-circuited
    // for scope='all', producing the asymmetric behaviour the user
    // reported: Tests/Suites populated but All was blank.)

    setIsSearching(true)
    try {
      const entityKey = s === 'all' ? null : (SCOPES.find(x => x.id === s)?.entityKey ?? null)
      // `globalSearch` accepts an array of entity types and doesn't expose a
      // mode parameter: it spans all six entity types with keyword retrieval.
      // Disabled legacy modes are normalized above until the global endpoint
      // gains a real server-side mode switch.
      const data = await searchService.globalSearch({
        q: trimmed,
        project_id: scopedProjectId,
        entity_types: entityKey ? [entityKey] : undefined,
        page,
        size: RESULTS_PAGE_SIZE,
      })
      if (requestId !== searchRequestIdRef.current) return
      setResponse(data)
      // Only persist real queries to history — browse views (empty q)
      // shouldn't pollute Recent searches.
      if (trimmed) {
        setRecents(prev => {
          const next: RecentSearch[] = [
            { id: `${Date.now()}`, query: trimmed, mode: effectiveMode, scope: s, resultCount: data.total, ts: Date.now() },
            ...prev.filter(r => r.query !== trimmed || r.scope !== s),
          ].slice(0, MAX_RECENT)
          writeRecents(next)
          return next
        })
      }
    } catch {
      if (requestId !== searchRequestIdRef.current) return
      toast.error('Search failed')
      setResponse(null)
    } finally {
      if (requestId === searchRequestIdRef.current) setIsSearching(false)
    }
  }, [scopedProjectId, setSearchParams])

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
    const nextMode = normalizeRetrievalMode(params.get('mode') as RetrievalMode | null)
    const nextScope = normalizeEntityScope(params.get('scope'))
    setQuery(nextQuery)
    setMode(nextMode)
    setScope(nextScope)
    void runSearch(nextQuery, nextMode, nextScope)
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

  // ── ⌘K shortcut: focus input (unless user is already typing in another input) ──
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
      // ⌘1 / ⌘2 / ⌘3 — fire the saved search in that slot.
      if ((e.metaKey || e.ctrlKey) && /^[123]$/.test(e.key)) {
        const slot = Number(e.key) as 1 | 2 | 3
        const match = saved.find(s => s.slot === slot)
        if (match) {
          e.preventDefault()
          setQuery(match.query)
          setMode(match.mode)
          setScope(match.scope)
          void runSearch(match.query, match.mode, match.scope)
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [saved, runSearch])

  // ── Pick a row from a list → write into input + submit immediately ───
  const handlePick = (q: string, m: RetrievalMode, s: EntityScope) => {
    setQuery(q)
    setMode(m)
    setScope(s)
    void runSearch(q, m, s)
  }

  const handleClearHistory = () => {
    if (recents.length === 0) return
    if (window.confirm(`Clear all ${recents.length} recent search${recents.length === 1 ? '' : 'es'}? This can't be undone.`)) {
      setRecents([])
      writeRecents([])
      toast.success('Recent searches cleared')
    }
  }

  const handleOpenResult = (row: GlobalSearchResult) => {
    if (row.project_id) {
      const destinationProject = projects.find(candidate => candidate.id === row.project_id)
      if (destinationProject) setActiveProject(destinationProject)
    }
    navigate(row.navigation_url)
  }

  const handleUnsave = (id: string) => {
    setSaved(prev => {
      const next = prev.filter(s => s.id !== id)
      writeSaved(next)
      return next
    })
  }

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

  const indexHealthRows = useMemo(
    () => buildEntityHealth(indexStatus, entityCounts),
    [indexStatus, entityCounts],
  )

  const projectLabel = project?.name ?? 'All Projects'

  // The crumb the old header drew (workspace chip · counts · freshness), as
  // the compact header's one line.
  const subtitle = [
    `Find tests, runs, suites, defects, releases across ${projectLabel}`,
    `${Intl.NumberFormat().format(totalIndexed)} items indexed`,
    indexStatus?.last_indexed_at
      ? `fresh ${relativeTime(new Date(indexStatus.last_indexed_at).getTime())}`
      : null,
  ].filter(Boolean).join(' · ')

  return (
    <PageShell>
      <PageHeader compact title="Search" subtitle={subtitle} helpTopic={HELP_TOPIC} />

      <SearchCommandBar
        value={query}
        onChange={setQuery}
        onSubmit={() => runSearch(query, mode, scope)}
        mode={mode}
        onModeChange={(m) => { setMode(m); void runSearch(query, m, scope) }}
        scope={scope}
        onScopeChange={(s) => { setScope(s); void runSearch(query, mode, s) }}
        scopeCounts={scopeCounts}
        inputRef={inputRef}
      />

      <VerdictRibbon
        totalItems={totalIndexed}
        typeCount={6}
        indexStatus={indexStatus}
      />

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
          <CardShell
            title={
              <>
                Results <span className="text-[11.5px] font-normal text-[var(--color-text-muted)] ml-2">
                  <strong>{Intl.NumberFormat().format(response.total)}{response.counts_are_exact === false ? '+' : ''}</strong> across {scope === 'all' ? '6' : '1'} type{scope === 'all' ? 's' : ''}
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
              <span className="font-mono">
                <code className="text-[11px]">{response.search_type}</code>
                {' · '}
                showing {((response.page - 1) * response.size) + 1}–{((response.page - 1) * response.size) + response.items.length} of {response.counts_are_exact === false ? 'at least ' : ''}{response.total}
              </span>
            }
          >
            <div className="flex flex-col">
              {response.items.map(r => <ResultRow key={`${r.entity_type}-${r.entity_id}`} row={r} onOpen={() => handleOpenResult(r)} />)}
            </div>
          </CardShell>
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
        <CardShell title="No results" rightSlot={<span>0 matches</span>}>
          <div className="px-4 py-8 text-center text-[13px] text-[var(--color-text-secondary)]">
            {query.trim() ? (
              <>
                <p className="m-0 mb-2">Nothing matched <code className="font-mono text-[12px]">{query}</code>.</p>
                <p className="text-[12px] m-0 text-[var(--color-text-muted)]">
                  Broaden the scope or try a shorter part of the name.
                </p>
              </>
            ) : (
              <>
                <p className="m-0 mb-2">Nothing to browse in <code className="font-mono text-[12px]">{projectLabel}</code> yet.</p>
                <p className="text-[12px] m-0 text-[var(--color-text-muted)]">
                  Ingest a test run or pick a different project from the top bar to populate the index.
                </p>
              </>
            )}
          </div>
        </CardShell>
      )}

      {/* Browse-mode helper grid — recent/saved searches + index health.
          Visible whenever no query is active, so a user landing at
          /search with scope=all (browse mode) still sees Index Health
          alongside the auto-loaded results.
          Hidden once the user types a query so the screen focuses on
          their search results. */}
      {query.trim() === '' && (
        <div className="grid gap-3.5 mt-3.5" style={{ gridTemplateColumns: 'minmax(0, 1.65fr) minmax(0, 1fr)' }}>
          <div className="flex flex-col gap-3.5 min-w-0">
            <RecentList
              rows={recents}
              onPick={(r) => handlePick(r.query, r.mode, r.scope)}
              onClear={handleClearHistory}
            />
            <SavedList
              rows={saved}
              onPick={(r) => handlePick(r.query, r.mode, r.scope)}
              onUnsave={handleUnsave}
            />
          </div>
          <div className="flex flex-col gap-3.5 min-w-0">
            <IndexHealthCard rows={indexHealthRows} />
          </div>
        </div>
      )}

      {isSearching && (
        <div className="fixed bottom-4 right-4 z-20 inline-flex items-center gap-2 px-3 py-2 rounded-md text-[12px] text-[var(--color-text-secondary)] bg-[var(--color-bg-card)] border border-[var(--color-border)] shadow-lg">
          <LoadingSpinner size="sm" />
          Searching…
        </div>
      )}

      <div className="fixed bottom-4 left-4 lg:hidden text-[12px] text-[var(--color-text-muted)] bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-md px-3 py-2 z-10">
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
  defect:     { bg: 'rgba(236,72,153,0.14)',  fg: '#f9a8d4' },
  flaky_test: { bg: 'color-mix(in srgb, var(--status-broken) 14%, transparent)',  fg: 'var(--status-broken)' },
  release:    { bg: 'var(--color-bg-secondary)', fg: 'var(--color-text-muted)' },
}
