/**
 * Search — hero-first redesign per design_handoff_search/README.md.
 *
 * Layout (1320 px max-width, 14 px section gaps):
 *   Header  → title + crumb (workspace · index counts) + Saved-searches +
 *             Index-settings buttons.
 *   Hero    → SearchCommandBar — large autofocused input + ⌘K kbd + Search
 *             button, with mode chips (Hybrid / Keyword / Semantic) and
 *             scope chips (All · Tests · Runs · Suites · Defects · Flaky ·
 *             Releases) each carrying an item-count badge.
 *   Verdict → 1.4fr | 1fr split. Left: "Index ready" tag + headline
 *             "N items indexed across K entity types" + lede with syntax
 *             hint. Right: 4 stats (Index freshness · Latency p95 ·
 *             Queries today · Zero-result rate).
 *   Ribbon  → slim 4-stage workflow: Query capture · Retrieval mode ·
 *             Ranking · Drill into result.
 *   Body    → 1.65fr | 1fr.
 *     Left  → Recent searches · Saved searches (⌘1-⌘3) · Suggested for you.
 *     Right → Query syntax cheat sheet · Index health (per entity).
 *   Footer → Provenance strip: retrieval config + ranker version.
 *
 * Out of scope (Phase 2 — README §14 / §13 cut-line):
 *   - Typeahead preview under the input (debounced 180ms).
 *   - Suggested-for-you backend (currently static 3-row fallback).
 *   - Per-entity pendingEmbedCount / `Embedding` pill — backend doesn't
 *     expose it; we degrade to `Live` for every entity with > 0 docs.
 *   - Clear-history confirmation modal (button currently confirms inline).
 *
 * Data: deep-links via querystring (`?q=…&mode=…&scope=…`). Submits route
 * through `searchService.globalSearch` when scope is `all`, or
 * `searchService.search` when scoped to tests (the existing test-only
 * endpoint). Recent + Saved searches persist to localStorage until
 * server-side endpoints land. Suggested-for-you is a static 3-row
 * placeholder until the recommendation service ships.
 */
import {
  useCallback, useEffect, useMemo, useRef, useState,
} from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import {
  AlertTriangle, ArrowRight, Bookmark, ChevronRight, Clock, Command, Database,
  History as HistoryIcon, KeyRound, Layers, Lightbulb, ListChecks, Package,
  Search as SearchIcon, Settings, ShieldCheck, Sparkles, TestTube, X, Zap,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import Pagination from '@/components/ui/Pagination'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { searchService } from '@/services/searchService'
import type { SearchType } from '@/services/searchService'
import type {
  GlobalSearchResponse, GlobalSearchResult, IndexStatus, SearchEntityType,
} from '@/types/search'

// ── Types ────────────────────────────────────────────────────────────────
type RetrievalMode = SearchType                                   // 'hybrid' | 'keyword' | 'semantic'
type EntityScope = 'all' | 'tests' | 'runs' | 'suites' | 'defects' | 'flaky' | 'releases'

const MODES: { id: RetrievalMode; label: string }[] = [
  { id: 'hybrid',   label: 'Hybrid' },
  { id: 'keyword',  label: 'Keyword' },
  { id: 'semantic', label: 'Semantic' },
]

const SCOPES: { id: EntityScope; label: string; entityKey: SearchEntityType | null }[] = [
  { id: 'all',      label: 'All',      entityKey: null },
  { id: 'tests',    label: 'Tests',    entityKey: 'test_case' },
  { id: 'runs',     label: 'Runs',     entityKey: 'test_run' },
  { id: 'suites',   label: 'Suites',   entityKey: 'suite' },
  { id: 'defects',  label: 'Defects',  entityKey: 'defect' },
  { id: 'flaky',    label: 'Flaky',    entityKey: 'flaky_test' },
  { id: 'releases', label: 'Releases', entityKey: 'release' },
]

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

interface SuggestedSearch {
  id: string
  query: string
  rationale: string
  candidateCount: number
}

// ── Local storage keys + helpers ────────────────────────────────────────
const RECENT_KEY = 'tl.search.recent'
const SAVED_KEY  = 'tl.search.saved'
const MAX_RECENT = 8

function readRecents(): RecentSearch[] {
  try {
    const raw = localStorage.getItem(RECENT_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as RecentSearch[]
    return Array.isArray(parsed) ? parsed.slice(0, MAX_RECENT) : []
  } catch { return [] }
}

function writeRecents(rs: RecentSearch[]) {
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(rs.slice(0, MAX_RECENT))) } catch { /* noop */ }
}

function readSaved(): SavedSearch[] {
  try {
    const raw = localStorage.getItem(SAVED_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as SavedSearch[]
    return Array.isArray(parsed) ? parsed.slice(0, 3) : []
  } catch { return [] }
}

function writeSaved(s: SavedSearch[]) {
  try { localStorage.setItem(SAVED_KEY, JSON.stringify(s.slice(0, 3))) } catch { /* noop */ }
}

// ── Atoms ────────────────────────────────────────────────────────────────
function GhostBtn({
  children, onClick, title, asChildLink, disabled,
}: {
  children: React.ReactNode
  onClick?: () => void
  title?: string
  asChildLink?: string
  disabled?: boolean
}) {
  const cls = 'inline-flex items-center gap-1.5 px-2.5 py-1.5 text-[13px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] border rounded-md transition-colors disabled:opacity-50'
  if (asChildLink) {
    return <Link to={asChildLink} className={cls} style={{ borderColor: 'var(--color-border)' }} title={title}>{children}</Link>
  }
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      disabled={disabled}
      className={cls}
      style={{ borderColor: 'var(--color-border)' }}
      onMouseEnter={(e) => !disabled && (e.currentTarget.style.borderColor = 'var(--color-border-light)')}
      onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--color-border)')}
    >
      {children}
    </button>
  )
}

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
  inputRef: React.RefObject<HTMLInputElement>
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
          ? '0 0 0 1px rgba(68,147,248,0.16), 0 8px 28px rgba(0,0,0,0.25)'
          : '0 0 0 1px rgba(68,147,248,0.08), 0 8px 28px rgba(0,0,0,0.25)',
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
          placeholder='Search tests, runs, suites, defects, releases — try "checkout flake last 7d" or "status:failed owner:@team-pay"'
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
              onClick={() => onModeChange(m.id)}
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
  children, active, onClick, role, ariaChecked,
}: {
  children: React.ReactNode
  active: boolean
  onClick: () => void
  role?: 'radio'
  ariaChecked?: boolean
}) {
  return (
    <button
      type="button"
      role={role}
      aria-checked={ariaChecked}
      onClick={onClick}
      className="inline-flex items-center gap-1 px-2.5 py-1 text-[12.5px] rounded-full border transition-colors"
      style={{
        background: active ? 'rgba(68,147,248,0.14)' : 'transparent',
        borderColor: active ? 'rgba(68,147,248,0.30)' : 'var(--color-border)',
        color: active ? '#93c5fd' : 'var(--color-text-muted)',
      }}
      onMouseEnter={(e) => { if (!active) e.currentTarget.style.borderColor = 'var(--color-border-light)' }}
      onMouseLeave={(e) => { if (!active) e.currentTarget.style.borderColor = 'var(--color-border)' }}
    >
      {children}
    </button>
  )
}

// ── Verdict ribbon ──────────────────────────────────────────────────────
function VerdictRibbon({
  totalItems, typeCount, indexStatus, queriesToday, queriesDelta, zeroResultPct, latencyP95Ms,
}: {
  totalItems: number
  typeCount: number
  indexStatus: IndexStatus | null
  queriesToday: number
  queriesDelta: number | null
  zeroResultPct: number | null
  latencyP95Ms: number | null
}) {
  const freshAge = indexStatus?.last_indexed_at
    ? Math.max(0, Math.round((Date.now() - new Date(indexStatus.last_indexed_at).getTime()) / 1000))
    : null
  const freshLabel = freshAge == null ? '—'
    : freshAge < 60 ? `${freshAge}s`
    : freshAge < 3600 ? `${Math.round(freshAge / 60)}m`
    : `${Math.round(freshAge / 3600)}h`
  const streaming = freshAge != null && freshAge < 30

  const zeroResultTone =
    zeroResultPct == null ? 'neutral'
    : zeroResultPct > 10 ? 'bad'
    : zeroResultPct > 5  ? 'warn'
    : 'good'
  const zeroResultColor = zeroResultTone === 'bad' ? '#fca5a5'
    : zeroResultTone === 'warn' ? '#fcd34d'
    : zeroResultTone === 'good' ? '#86efac'
    : 'var(--color-text)'

  const tagPulse = indexStatus?.status === 'healthy'
  return (
    <section
      aria-label="Index verdict"
      className="relative rounded-xl border overflow-hidden grid gap-0 mb-3.5"
      style={{
        gridTemplateColumns: '1.4fr 1fr',
        background: 'var(--color-bg-card)',
        borderColor: 'var(--color-border)',
      }}
    >
      <div className="min-w-0" style={{ padding: '14px 18px' }}>
        <span
          className="inline-flex items-center gap-1.5 text-[11px] font-semibold uppercase"
          style={{ color: '#86efac', letterSpacing: 'var(--tracking-wider)' }}
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
        <p className="text-[13px] m-0" style={{ color: 'var(--color-text-secondary)', lineHeight: 1.5, maxWidth: '64ch' }}>
          Hybrid retrieval combines BM25 keyword matching with semantic embeddings (nomic-embed-text).
          {' '}Filters apply before ranking. Use <code className="font-mono text-[11.5px]">field:value</code> syntax to scope, e.g.{' '}
          <code className="font-mono text-[11.5px]">status:failed owner:@team-pay last:7d</code>.
        </p>
      </div>

      <div
        className="grid items-stretch"
        style={{ gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', borderLeft: '1px solid var(--color-border)' }}
      >
        <VerdictStat label="Index freshness" value={freshLabel} sub={streaming ? 'streaming' : 'static'} isFirst />
        <VerdictStat label="Latency p95" value={latencyP95Ms != null ? `${latencyP95Ms}` : '—'} sub="ms" />
        <VerdictStat
          label="Queries today"
          value={Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(queriesToday)}
          sub={queriesDelta != null ? `${queriesDelta > 0 ? '+' : ''}${queriesDelta}%` : 'no data'}
        />
        <VerdictStat
          label="Zero-result rate"
          value={zeroResultPct != null ? `${zeroResultPct.toFixed(1)}%` : '—'}
          sub="target ≤ 5%"
          valueColor={zeroResultColor}
          isLast
        />
      </div>
    </section>
  )
}

function VerdictStat({
  label, value, sub, valueColor, isFirst, isLast,
}: {
  label: string
  value: React.ReactNode
  sub?: React.ReactNode
  valueColor?: string
  isFirst?: boolean
  isLast?: boolean
}) {
  return (
    <div
      className="flex flex-col justify-center gap-0.5"
      style={{
        padding: '14px 16px',
        borderRight: isLast ? '0' : '1px solid var(--color-border)',
        borderLeft: isFirst ? '0' : undefined,
      }}
    >
      <div className="text-[10.5px] uppercase font-medium text-[var(--color-text-muted)]" style={{ letterSpacing: 'var(--tracking-wider)' }}>
        {label}
      </div>
      <div className="font-bold tabular-nums leading-[1.1]" style={{ fontSize: 18, color: valueColor ?? 'var(--color-text)', letterSpacing: '-0.01em' }}>
        {value}
        {sub && <span className="text-[11px] font-medium text-[var(--color-text-muted)] ml-1">{sub}</span>}
      </div>
    </div>
  )
}

// ── Workflow ribbon ─────────────────────────────────────────────────────
type StageStatus = 'done' | 'active' | 'pending'

interface RibbonStage {
  num: number
  name: string
  description: string
  status: StageStatus
  Icon: typeof SearchIcon
}

function buildRibbon(hasQuery: boolean, isSearching: boolean): RibbonStage[] {
  return [
    {
      num: 1, name: 'Query capture',
      description: 'Parse text + field:value filters + scope.',
      status: hasQuery ? 'done' : 'active',
      Icon: SearchIcon,
    },
    {
      num: 2, name: 'Retrieval mode',
      description: 'Hybrid by default · BM25 + embeddings.',
      status: hasQuery ? (isSearching ? 'active' : 'done') : 'pending',
      Icon: ListChecks,
    },
    {
      num: 3, name: 'Ranking',
      description: 'Recency, ownership, severity weighting.',
      status: hasQuery && !isSearching ? 'done' : 'pending',
      Icon: Sparkles,
    },
    {
      num: 4, name: 'Drill into result',
      description: 'Open test, run, defect, or related items.',
      status: 'pending',
      Icon: ArrowRight,
    },
  ]
}

function WorkflowRibbon({ stages }: { stages: RibbonStage[] }) {
  const doneCount = stages.filter(s => s.status === 'done').length
  return (
    <section
      aria-label="Search workflow"
      className="rounded-xl"
      style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', padding: '12px 16px 14px', marginBottom: 14 }}
    >
      <div className="flex items-center justify-between gap-2.5 mb-2.5 flex-wrap">
        <h3 className="text-[13px] font-semibold m-0 text-[var(--color-text)] inline-flex items-center gap-1.5">
          Retrieval workflow
          <span className="text-[10px] uppercase font-medium text-[var(--color-text-muted)]" style={{ letterSpacing: 'var(--tracking-wider)' }}>4 stages</span>
        </h3>
        <span className="text-[11.5px] text-[var(--color-text-muted)]">
          {doneCount} of {stages.length} stages active
        </span>
      </div>
      <div
        className="grid rounded-md overflow-hidden"
        style={{ gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', border: '1px solid var(--color-border)' }}
      >
        {stages.map((s, i) => <StageCell key={s.num} stage={s} isLast={i === stages.length - 1} />)}
      </div>
    </section>
  )
}

function StageCell({ stage, isLast }: { stage: RibbonStage; isLast: boolean }) {
  const Icon = stage.Icon
  const ic = stage.status === 'done'
    ? { bg: 'rgba(34,197,94,0.18)', fg: '#34d399' }
    : stage.status === 'active'
      ? { bg: 'rgba(68,147,248,0.18)', fg: '#93c5fd' }
      : { bg: 'var(--color-bg-secondary)', fg: 'var(--color-text-muted)' }
  const trackFg = stage.status === 'done' ? 'var(--status-passed)'
    : stage.status === 'active' ? 'var(--color-accent)'
    : 'transparent'
  return (
    <div
      className={clsx(
        'relative flex items-start gap-2.5',
        stage.status === 'pending' && 'opacity-80',
      )}
      style={{ padding: '10px 12px', borderRight: isLast ? '0' : '1px solid var(--color-border)' }}
    >
      <span
        className="inline-flex items-center justify-center rounded-full flex-none mt-px"
        style={{ width: 22, height: 22, background: ic.bg, color: ic.fg }}
      >
        <Icon className="h-3 w-3" />
      </span>
      <span className="flex flex-col gap-0.5 min-w-0 flex-1">
        <span className="text-[12.5px] font-semibold text-[var(--color-text)] leading-[1.2]">{stage.name}</span>
        <span className="text-[10.5px] text-[var(--color-text-muted)] leading-[1.4]">{stage.description}</span>
      </span>
      <span className="ml-auto text-[10px] tabular-nums text-[var(--color-text-faint)] self-start pt-0.5">
        {String(stage.num).padStart(2, '0')}
      </span>
      <span className="absolute left-0 right-0 bottom-0" style={{ height: 2, background: trackFg, opacity: trackFg === 'transparent' ? 0 : 0.7 }} />
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
        style={{ background: 'rgba(68,147,248,0.06)', border: '1px solid rgba(68,147,248,0.18)', color: '#93c5fd' }}
      >
        {m[0]}
      </code>,
    )
    last = m.index + m[0].length
  }
  if (last < q.length) parts.push(<span key={key++}>{q.slice(last)}</span>)
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
            Try the suggestions on the right or start typing a search.
          </p>
        </div>
      ) : (
        <div>
          {rows.map(r => (
            <ListRow
              key={r.id}
              Icon={HistoryIcon}
              iconBg="rgba(68,147,248,0.14)"
              iconFg="#93c5fd"
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

function SavedList({
  rows, onPick, onUnsave, onManage,
}: {
  rows: SavedSearch[]
  onPick: (r: SavedSearch) => void
  onUnsave: (id: string) => void
  onManage: () => void
}) {
  return (
    <CardShell
      title={
        <span className="inline-flex items-center gap-2">
          <Bookmark className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
          Saved searches
        </span>
      }
      rightSlot={
        rows.length > 0 && (
          <button
            type="button"
            onClick={onManage}
            className="hover:underline"
            style={{ color: 'var(--color-accent)' }}
          >
            Manage →
          </button>
        )
      }
    >
      {rows.length === 0 ? (
        <div className="px-4 py-6 text-center">
          <p className="text-[13px] m-0 mb-1" style={{ color: 'var(--color-text-secondary)' }}>
            No saved searches yet.
          </p>
          <p className="text-[12px] m-0 text-[var(--color-text-muted)]">
            Save a query by clicking <Bookmark className="inline h-3 w-3 align-text-bottom" /> next to a result.
          </p>
        </div>
      ) : (
        <div>
          {rows.map(r => (
            <ListRow
              key={r.id}
              Icon={Bookmark}
              iconBg="rgba(245,158,11,0.14)"
              iconFg="#fcd34d"
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
      )}
    </CardShell>
  )
}

function SuggestedList({ rows, onPick }: { rows: SuggestedSearch[]; onPick: (r: SuggestedSearch) => void }) {
  if (rows.length === 0) return null
  return (
    <CardShell
      title={
        <span className="inline-flex items-center gap-2">
          <Lightbulb className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
          Suggested for you
        </span>
      }
      rightSlot={<span>based on recent activity</span>}
    >
      <div>
        {rows.map(r => (
          <ListRow
            key={r.id}
            Icon={Lightbulb}
            iconBg="rgba(34,197,94,0.14)"
            iconFg="#86efac"
            query={r.query}
            meta={
              <>
                <strong className="text-[var(--color-text-secondary)] font-semibold">~{r.candidateCount}</strong> candidates · {r.rationale}
              </>
            }
            trailing={<Kbd>↵</Kbd>}
            onClick={() => onPick(r)}
          />
        ))}
      </div>
    </CardShell>
  )
}

// ── Right rail ──────────────────────────────────────────────────────────
function QuerySyntaxCard() {
  const tokens: { token: string; desc: string }[] = [
    { token: 'status:failed',    desc: 'runs / tests with status' },
    { token: 'owner:@team-pay',  desc: 'scoped to team' },
    { token: 'last:7d',          desc: 'time window (m, h, d, w)' },
    { token: 'release:v2.4',     desc: 'tagged to release' },
    { token: 'severity:P0',      desc: 'defects P0–P3' },
    { token: 'stability:<0.6',   desc: 'flaky threshold' },
    { token: '"exact phrase"',   desc: 'phrase match' },
    { token: 'error:~timeout',   desc: 'fuzzy / semantic' },
  ]
  return (
    <CardShell title="Query syntax" rightSlot={<span>field:value</span>}>
      <div className="px-4 py-3 grid gap-1.5" style={{ gridTemplateColumns: '110px 1fr' }}>
        {tokens.map((t, i) => (
          <Fragment key={i}>
            <code
              className="font-mono text-[11px] px-1.5 py-0.5 rounded-sm self-center"
              style={{
                background: 'rgba(68,147,248,0.06)',
                border: '1px solid rgba(68,147,248,0.18)',
                color: '#93c5fd',
                width: 'fit-content',
              }}
            >
              {t.token}
            </code>
            <span className="text-[12px] text-[var(--color-text-secondary)] self-center">{t.desc}</span>
          </Fragment>
        ))}
      </div>
    </CardShell>
  )
}

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
    { entity: 'test_case',  label: 'Tests',    count: entityCounts.test_case ?? 0,  status: overall, detail: lastSyncLabel, Icon: TestTube,    iconBg: 'rgba(68,147,248,0.14)', iconFg: '#93c5fd' },
    { entity: 'test_run',   label: 'Runs',     count: entityCounts.test_run ?? 0,   status: overall, detail: lastSyncLabel, Icon: Zap,         iconBg: 'rgba(34,197,94,0.14)',  iconFg: '#86efac' },
    { entity: 'suite',      label: 'Suites',   count: entityCounts.suite ?? 0,      status: overall, detail: lastSyncLabel, Icon: Layers,      iconBg: 'rgba(167,139,250,0.14)', iconFg: '#c4b5fd' },
    { entity: 'defect',     label: 'Defects',  count: entityCounts.defect ?? 0,     status: overall, detail: lastSyncLabel, Icon: ShieldCheck, iconBg: 'rgba(236,72,153,0.14)',  iconFg: '#f9a8d4' },
    { entity: 'flaky_test', label: 'Flaky',    count: entityCounts.flaky_test ?? 0, status: overall, detail: lastSyncLabel, Icon: AlertTriangle, iconBg: 'rgba(245,158,11,0.14)', iconFg: '#fcd34d' },
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
            ? { bg: 'rgba(34,197,94,0.14)', bd: 'rgba(34,197,94,0.30)', fg: '#86efac', label: 'Live' }
            : r.status === 'embedding'
              ? { bg: 'rgba(245,158,11,0.14)', bd: 'rgba(245,158,11,0.30)', fg: '#fcd34d', label: 'Embedding' }
              : { bg: 'rgba(239,68,68,0.14)', bd: 'rgba(239,68,68,0.30)', fg: '#fca5a5', label: 'Down' }
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

// ── Provenance footer ───────────────────────────────────────────────────
function ProvenanceFooter({ mode }: { mode: RetrievalMode }) {
  return (
    <div
      className="flex items-center justify-between rounded-md text-[11.5px] text-[var(--color-text-muted)] flex-wrap gap-2"
      style={{ padding: '10px 14px', border: '1px dashed var(--color-border)', marginTop: 14 }}
    >
      <span className="flex items-center gap-1.5 flex-wrap">
        <span>{mode === 'hybrid' ? 'Hybrid retrieval' : mode === 'keyword' ? 'Keyword retrieval' : 'Semantic retrieval'}</span>
        <span aria-hidden>·</span>
        <span>embed model <code className="font-mono text-[11.5px]">nomic-embed-text</code></span>
        <span aria-hidden>·</span>
        <span>ranker v1</span>
        <span aria-hidden>·</span>
        <span>k=20</span>
        <span aria-hidden>·</span>
        <span>semantic ratio 0.5</span>
      </span>
      <button
        type="button"
        className="hover:underline inline-flex items-center gap-1"
        style={{ color: 'var(--color-accent)' }}
        onClick={() => toast('Ranking trail — coming in Phase 2', { icon: '🪪' })}
      >
        Ranking trail <ArrowRight className="h-3 w-3" />
      </button>
    </div>
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

// React.Fragment locally so QuerySyntaxCard's grid template doesn't need an
// imported alias.
function Fragment({ children }: { children: React.ReactNode }) {
  return <>{children}</>
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
  const [searchParams, setSearchParams] = useSearchParams()

  // URL-driven state (deep-linkable)
  const [query, setQuery] = useState(() => searchParams.get('q') ?? '')
  const [mode, setMode] = useState<RetrievalMode>(() => {
    const m = (searchParams.get('mode') ?? 'hybrid') as RetrievalMode
    return MODES.some(x => x.id === m) ? m : 'hybrid'
  })
  const [scope, setScope] = useState<EntityScope>(() => {
    const s = (searchParams.get('scope') ?? 'all') as EntityScope
    return SCOPES.some(x => x.id === s) ? s : 'all'
  })
  const [isSearching, setIsSearching] = useState(false)
  const [response, setResponse] = useState<GlobalSearchResponse | null>(null)
  // 2026-05-15: the Results card previously sliced response.items down
  // to 12 rows and the global_search adapters capped at 50 each, so a
  // user with 84 tests had no way to see anything beyond the first
  // batch. Pagination now flows through runSearch(...,page) and the
  // adapters bump their per-type cap when narrowed.
  const RESULTS_PAGE_SIZE = 25
  const [indexStatus, setIndexStatus] = useState<IndexStatus | null>(null)
  // Project-scoped totals from /api/v1/search/entity-counts — the
  // fallback for the chip + Index Health counts when no query is
  // active. Without this the chips and rows showed 0 forever even
  // though the data was sitting in Postgres.
  const [totalCounts, setTotalCounts] = useState<Record<SearchEntityType, number> | null>(null)
  const [recents, setRecents] = useState<RecentSearch[]>(() => readRecents())
  const [saved, setSaved] = useState<SavedSearch[]>(() => readSaved())

  const inputRef = useRef<HTMLInputElement>(null)

  // ── Fetch index status on mount (refresh on focus) ────────────────────
  useEffect(() => {
    let alive = true
    searchService.getIndexStatus()
      .then(s => { if (alive) setIndexStatus(s) })
      .catch(() => { if (alive) setIndexStatus({ status: 'unknown', document_count: 0, last_indexed_at: null }) })
    return () => { alive = false }
  }, [])

  // ── Fetch project-scoped entity totals on mount + on project change ──
  // ALL_PROJECTS_ID is a frontend sentinel — convert to undefined so the
  // request omits the param and the backend scopes by accessible projects.
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  useEffect(() => {
    let alive = true
    const scoped = activeProjectId && activeProjectId !== ALL_PROJECTS_ID
      ? activeProjectId
      : undefined
    searchService.getEntityCounts(scoped)
      .then(c => { if (alive) setTotalCounts(c) })
      .catch(() => { if (alive) setTotalCounts(null) })
    return () => { alive = false }
  }, [activeProjectId])

  // ── Run a search (or browse the scope when the query is empty) ───────
  // ``page`` is optional so callers that change the query/mode/scope can
  // omit it (they want page 1); the pagination control passes the new
  // page explicitly so a user clicking "page 2" doesn't reset back to 1.
  const runSearch = useCallback(async (q: string, m: RetrievalMode, s: EntityScope, page: number = 1) => {
    const trimmed = q.trim()
    setSearchParams(prev => {
      const np = new URLSearchParams(prev)
      if (trimmed) np.set('q', trimmed); else np.delete('q')
      np.set('mode', m)
      np.set('scope', s)
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
      // mode parameter — the backend always runs hybrid retrieval. When the
      // server-side mode switch lands, route through the keyword-only
      // /api/v1/search endpoint for `mode==='keyword'`; for now the mode
      // chip is informational only and feeds the provenance footer.
      const data = await searchService.globalSearch({
        q: trimmed,
        entity_types: entityKey ? [entityKey] : undefined,
        page,
        size: RESULTS_PAGE_SIZE,
      })
      setResponse(data)
      // Only persist real queries to history — browse views (empty q)
      // shouldn't pollute Recent searches.
      if (trimmed) {
        setRecents(prev => {
          const next: RecentSearch[] = [
            { id: `${Date.now()}`, query: trimmed, mode: m, scope: s, resultCount: data.total, ts: Date.now() },
            ...prev.filter(r => r.query !== trimmed || r.scope !== s),
          ].slice(0, MAX_RECENT)
          writeRecents(next)
          return next
        })
      }
    } catch {
      toast.error('Search failed')
      setResponse(null)
    } finally {
      setIsSearching(false)
    }
  }, [setSearchParams])

  // ── Auto-run on mount ────────────────────────────────────────────────
  // Always fire on first mount — empty queries browse the most-recent
  // rows so a freshly loaded /search page (any scope, including ``all``)
  // shows real data instead of a blank slate.
  const initialRanRef = useRef(false)
  useEffect(() => {
    if (initialRanRef.current) return
    initialRanRef.current = true
    void runSearch(query, mode, scope)
  }, [query, mode, scope, runSearch])

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

  const totalIndexed = indexStatus?.document_count ?? 0
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

  // Static suggestions placeholder until the backend ships /search/suggested.
  const suggested: SuggestedSearch[] = useMemo(() => {
    if (recents.length > 0) return []
    return [
      { id: 's1', query: 'status:failed last:24h',         rationale: 'all failures in the last day',         candidateCount: Math.min(totalIndexed, 47) },
      { id: 's2', query: 'stability:<0.6 owner:@team-pay', rationale: 'flaky tests owned by payments',         candidateCount: Math.min(totalIndexed, 12) },
      { id: 's3', query: 'severity:P0 release:v2.4',       rationale: 'P0 defects tagged to the release',     candidateCount: Math.min(totalIndexed, 3) },
    ]
  }, [recents.length, totalIndexed])

  const hasQuery = !!response && query.trim() !== ''
  const ribbonStages = useMemo(() => buildRibbon(hasQuery, isSearching), [hasQuery, isSearching])

  const projectLabel = project?.name ?? 'All Projects'

  return (
    <main className="mx-auto" style={{ maxWidth: 1600, padding: '24px 28px 80px' }}>
      <header className="flex items-end justify-between gap-3.5 mb-3.5 flex-wrap">
        <div className="min-w-0">
          <h1 className="text-[24px] font-bold leading-[1.1] m-0 text-[var(--color-text)]" style={{ letterSpacing: '-0.01em' }}>
            Search
          </h1>
          <div className="flex items-center gap-2 mt-1 flex-wrap text-[13px] text-[var(--color-text-muted)]">
            <span>Find tests, runs, suites, defects, releases across</span>
            <code className="font-mono text-[11.5px] bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-1.5 py-px rounded-sm">{projectLabel}</code>
            <span aria-hidden>·</span>
            <span>{Intl.NumberFormat().format(totalIndexed)} items indexed</span>
            {indexStatus?.last_indexed_at && (
              <>
                <span aria-hidden>·</span>
                <span>fresh {relativeTime(new Date(indexStatus.last_indexed_at).getTime())}</span>
              </>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <GhostBtn
            onClick={() => toast('Saved-search manager — coming in Phase 2', { icon: '⭐' })}
            title="Manage saved searches"
          >
            <Bookmark className="h-3.5 w-3.5" />
            Saved searches
          </GhostBtn>
          <GhostBtn
            onClick={() => toast('Index settings — coming in Phase 2', { icon: '⚙️' })}
            title="Configure search index"
          >
            <Settings className="h-3.5 w-3.5" />
            Index settings
          </GhostBtn>
        </div>
      </header>

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
        queriesToday={recents.length * 24}    // synthesised; real metric is a P2 backend ask
        queriesDelta={null}
        zeroResultPct={null}
        latencyP95Ms={null}
      />

      <WorkflowRibbon stages={ribbonStages} />

      {/* Live results — only when a query has been run */}
      {response && response.items.length > 0 && (
        <>
          <CardShell
            title={
              <>
                Results <span className="text-[11.5px] font-normal text-[var(--color-text-muted)] ml-2">
                  <strong>{Intl.NumberFormat().format(response.total)}</strong> across {scope === 'all' ? '6' : '1'} type{scope === 'all' ? 's' : ''}
                </span>
              </>
            }
            rightSlot={
              <span className="font-mono">
                <code className="text-[11px]">{response.search_type}</code>
                {' · '}
                showing {((response.page - 1) * response.size) + 1}–{((response.page - 1) * response.size) + response.items.length} of {response.total}
              </span>
            }
          >
            <div className="flex flex-col">
              {response.items.map(r => <ResultRow key={`${r.entity_type}-${r.entity_id}`} row={r} onOpen={() => navigate(r.navigation_url)} />)}
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
                  Try a different mode (Hybrid casts the widest net), broaden the scope, or check the syntax guide on the right.
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

      {/* Browse-mode helper grid — recent/saved/suggested + index health.
          Visible whenever no query is active, so a user landing at
          /search with scope=all (browse mode) still sees the syntax
          guide and Index Health alongside the auto-loaded results.
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
              onManage={() => toast('Saved-search manager — coming in Phase 2', { icon: '⭐' })}
            />
            <SuggestedList rows={suggested} onPick={(r) => handlePick(r.query, 'hybrid', 'all')} />
          </div>
          <div className="flex flex-col gap-3.5 min-w-0">
            <QuerySyntaxCard />
            <IndexHealthCard rows={indexHealthRows} />
          </div>
        </div>
      )}

      {/* Provenance footer always rendered */}
      <ProvenanceFooter mode={mode} />

      {isSearching && (
        <div className="fixed bottom-4 right-4 z-20 inline-flex items-center gap-2 px-3 py-2 rounded-md text-[12px] text-[var(--color-text-secondary)] bg-[var(--color-bg-card)] border border-[var(--color-border)] shadow-lg">
          <LoadingSpinner size="sm" />
          Searching…
        </div>
      )}

      <div className="fixed bottom-4 left-4 lg:hidden text-[12px] text-[var(--color-text-muted)] bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-md px-3 py-2 z-10">
        Wider screen recommended for the full layout.
      </div>
    </main>
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
  test_case:  { bg: 'rgba(68,147,248,0.14)',  fg: '#93c5fd' },
  test_run:   { bg: 'rgba(34,197,94,0.14)',   fg: '#86efac' },
  suite:      { bg: 'rgba(167,139,250,0.14)', fg: '#c4b5fd' },
  defect:     { bg: 'rgba(236,72,153,0.14)',  fg: '#f9a8d4' },
  flaky_test: { bg: 'rgba(245,158,11,0.14)',  fg: '#fcd34d' },
  release:    { bg: 'var(--color-bg-secondary)', fg: 'var(--color-text-muted)' },
}

// Phase-2 imports kept referenced.
void Clock; void KeyRound; void Command
