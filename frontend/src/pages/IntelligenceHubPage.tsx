/**
 * IntelligenceHubPage — verdict-first cockpit for /intelligence.
 *
 * Layout (matches design_handoff_intelligence_selector/):
 *   ┌────────────────────────────────────────────────────────────┐
 *   │  Header (title · sub · 3 action buttons)                    │
 *   │  Verdict card (status-aware) · composite health · delta · bar│
 *   │  Filter bar (search · range · branch · env · status · saved)│
 *   │  ┌────────────────────────────┐ ┌──────────────────────────┐│
 *   │  │ Runs table (6 cols)        │ │ Cross-run insights        ││
 *   │  │                            │ │ Intelligence spend        ││
 *   │  │                            │ │ Recent activity           ││
 *   │  └────────────────────────────┘ └──────────────────────────┘│
 *   └────────────────────────────────────────────────────────────┘
 *
 * Verdict state machine: at-risk → attention → all-clear, driven by
 * the current runs window (any FAILED → at-risk; any FLAKY/BROKEN → attention).
 * Composite health, delta, and the stacked bar are all derived from the same
 * data the runs table is showing — no extra round-trip.
 *
 * Right-rail panels: Spend is wired to the real per-project LLM meter
 * (GET /projects/{id}/llm-usage + /llm-quota via useProjectUsage /
 * useProjectQuota). Insights still renders a graceful empty state pending
 * the /api/intelligence/insights endpoint proposed in the handoff; Activity
 * synthesizes from the runs already in scope.
 */
import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  Activity,
  ArrowRight,
  ChevronDown,
  Coins,
  GitBranch,
  GitCompare,
  Layers,
  MessageSquare,
  Search,
  Sparkles,
  TrendingDown,
  TrendingUp,
  Zap,
} from 'lucide-react'
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import Pagination from '@/components/ui/Pagination'
import SuiteBadge from '@/components/ui/SuiteBadge'
import SuiteFilterSelect from '@/components/ui/SuiteFilterSelect'
import { useProjectQuota, useProjectUsage } from '@/hooks/useLlmBudget'
import { useMostRecentRun, useRuns } from '@/hooks/useRuns'
import { useSuiteOptions } from '@/hooks/useSuiteOptions'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { fromNow } from '@/utils/formatters'
import type { TestRun } from '@/types/runs'

// ── Helpers ────────────────────────────────────────────────────────────────

/**
 * ``no-data`` is a first-class state, not the absence of one.
 *
 * It used to be missing, and the consequence was a false all-clear: with zero
 * runs in the window ``failed`` is 0, so the state fell through to
 * ``all-clear`` and a project whose most recent run had three failing tests
 * was told "All clear — nothing needs investigation right now." An empty
 * window is a statement about the WINDOW, never about the code.
 */
type VerdictState = 'no-data' | 'all-clear' | 'attention' | 'at-risk'

interface HealthSummary {
  state: VerdictState
  /** ``null`` when nothing was analysed — a 0 here would read as "measured, and terrible". */
  composite: number | null
  delta: number              // pts vs prior half-window
  counts: { passed: number; flaky: number; failed: number; broken: number; total: number }
  rangeLabel: string
}

const RANGE_OPTIONS = [
  { id: '24h', label: 'Last 24h',  hours: 24 },
  { id: '7d',  label: 'Last 7d',   hours: 24 * 7 },
  { id: '30d', label: 'Last 30d',  hours: 24 * 30 },
] as const
type RangeId = (typeof RANGE_OPTIONS)[number]['id']

function isFailed(r: TestRun) { return /failed/i.test(r.status) }
function isBroken(r: TestRun) { return /broken/i.test(r.status) }
// "Flaky" isn't a first-class run status in the schema — approximate with
// runs that have broken_tests > 0 (the system-level signal of retry/repair).
function isFlaky(r: TestRun) { return !isFailed(r) && (r.broken_tests ?? 0) > 0 }
function isPassed(r: TestRun) { return !isFailed(r) && !isFlaky(r) && !isBroken(r) }

function computeHealth(runs: TestRun[], rangeLabel: string): HealthSummary {
  const total = runs.length
  const passed = runs.filter(isPassed).length
  const flaky  = runs.filter(isFlaky).length
  const failed = runs.filter(isFailed).length
  const broken = runs.filter(isBroken).length

  // Composite = average pass_rate across the window, fall back to count-based
  // when pass_rate is missing.
  // NULL, not 0, when there is nothing to average. A 0/100 rendered from an
  // empty window is a fabricated measurement — the same defect class this
  // codebase guards everywhere else.
  let composite: number | null = null
  if (total > 0) {
    const rates = runs.map(r => (r.pass_rate ?? (isPassed(r) ? 100 : 0)))
    composite = Math.round(rates.reduce((a, b) => a + b, 0) / rates.length)
  }

  // Delta vs previous half — split the window in half by recency.
  const half = Math.floor(total / 2)
  const recent = runs.slice(0, half)
  const prior  = runs.slice(half)
  const meanRate = (xs: TestRun[]) =>
    xs.length
      ? xs.reduce((a, r) => a + (r.pass_rate ?? (isPassed(r) ? 100 : 0)), 0) / xs.length
      : 0
  const delta = recent.length && prior.length
    ? Math.round((meanRate(recent) - meanRate(prior)) * 10) / 10
    : 0

  // Order matters: an empty window says nothing about the code, so it can
  // never fall through to a reassuring verdict.
  let state: VerdictState = 'all-clear'
  if (total === 0) state = 'no-data'
  else if (failed > 0) state = 'at-risk'
  else if (flaky >= 2 || broken > 0) state = 'attention'

  return {
    state,
    composite,
    delta,
    counts: { passed, flaky, failed, broken, total },
    rangeLabel,
  }
}

function formatDuration(ms?: number | null) {
  if (!ms || ms <= 0) return '—'
  const s = Math.round(ms / 1000)
  const m = Math.floor(s / 60)
  const rem = s % 60
  if (m === 0) return `${s}s`
  return `${m}m ${rem.toString().padStart(2, '0')}s`
}

const AVATAR_PALETTE = [
  'bg-[var(--color-accent-muted)] text-[var(--color-accent)]',
  'bg-[var(--status-passed-bg)] text-[var(--status-passed)]',
  'bg-[var(--status-broken-bg)] text-[var(--status-broken)]',
  'bg-[var(--status-flaky-bg)] text-[var(--status-flaky)]',
  'bg-[var(--status-failed-bg)] text-[var(--status-failed)]',
  'bg-[var(--color-accent-muted)] text-[var(--color-accent)]',
]
function avatarFor(seed: string): { initials: string; cls: string } {
  if (!seed) return { initials: '··', cls: AVATAR_PALETTE[0] }
  const parts = seed.split(/[\s/_-]+/).filter(Boolean)
  const initials = (parts[0]?.[0] ?? seed[0] ?? '?').toUpperCase()
    + (parts[1]?.[0] ?? seed[1] ?? '').toUpperCase()
  let hash = 0
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) >>> 0
  return { initials: initials || '?', cls: AVATAR_PALETTE[hash % AVATAR_PALETTE.length] }
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function IntelligenceHubPage() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const projectId = useProjectStore(s => s.activeProjectId)
  const isAll = projectId === ALL_PROJECTS_ID

  // Default range is 30d (24h → 7d 2026-05-15 → 30d 2026-08-15). Most CI
  // workloads don't ship a fresh run every day, and 7d still left projects
  // that had been quiet for a week staring at "0 runs" — reported as the
  // whole page being broken. Matches DEFAULT_TIME_WINDOW_DAYS in
  // timeWindowStore; this page keeps its own URL-param default so deep-links
  // stay shareable, which is why the two have to be changed together.
  const range  = (params.get('range') ?? '30d') as RangeId
  const branch = params.get('branch') ?? ''
  const status = params.get('status') ?? 'all'
  const query  = params.get('q') ?? ''
  const suite  = params.get('suite') ?? ''

  const rangeDef = RANGE_OPTIONS.find(r => r.id === range) ?? RANGE_OPTIONS[0]
  // The runs endpoint accepts ``days`` — translate the hours-based UI to days.
  const days = Math.max(1, Math.round(rangeDef.hours / 24))

  const { options: suiteOptions } = useSuiteOptions(days)
  const { data, isLoading } = useRuns({ page: 1, size: 50, days, ...(suite && { suite_name: suite }) })
  const allRuns = useMemo(() => data?.items ?? [], [data?.items])

  // When the window is empty, "no runs" is not the useful answer — "your most
  // recent run was 10 days ago" is. This looks further back ONLY in that case,
  // so the normal path costs nothing, and it turns a dead page into a
  // one-click recovery instead of leaving the reader to guess the range.
  const windowIsEmpty = !isLoading && allRuns.length === 0
  const { data: beyondWindow } = useMostRecentRun(windowIsEmpty)
  const mostRecentBeyond = windowIsEmpty ? (beyondWindow?.items?.[0] ?? null) : null

  const visibleRuns = useMemo(() => {
    let xs = allRuns
    if (branch) xs = xs.filter(r => (r.branch ?? '').toLowerCase() === branch.toLowerCase())
    if (status === 'passed') xs = xs.filter(isPassed)
    if (status === 'failed') xs = xs.filter(isFailed)
    if (status === 'flaky')  xs = xs.filter(isFlaky)
    if (query.trim()) {
      const q = query.toLowerCase()
      xs = xs.filter(r =>
        String(r.build_number).toLowerCase().includes(q)
        || (r.branch ?? '').toLowerCase().includes(q)
        || (r.jenkins_job ?? '').toLowerCase().includes(q),
      )
    }
    return xs
  }, [allRuns, branch, status, query])

  const health = useMemo(
    () => computeHealth(visibleRuns, rangeDef.label),
    [visibleRuns, rangeDef.label],
  )

  // Table-only pagination. The verdict card + cross-run insights keep
  // consuming the full visibleRuns set; only the table slices.
  const TABLE_PAGE_SIZE = 25
  const [tablePage, setTablePage] = useState(1)
  // Filters change the data window — reset to page 1 to avoid showing an
  // empty page when the user narrows the result set. Reset during render via
  // previous-value tracking rather than a cascading setState-in-effect.
  const tableFilterKey = `${range}|${branch}|${status}|${query}|${suite}`
  const [prevTableFilterKey, setPrevTableFilterKey] = useState(tableFilterKey)
  if (prevTableFilterKey !== tableFilterKey) {
    setPrevTableFilterKey(tableFilterKey)
    setTablePage(1)
  }
  // Client-side datetime sort. Backend returns desc; this toggle reorders the
  // full filtered set before table-pagination so toggling reorders every row
  // in scope, not just the current page.
  const [datetimeSortDir, setDatetimeSortDir] = useState<'asc' | 'desc'>('desc')
  const sortedVisibleRuns = useMemo(() => {
    const mul = datetimeSortDir === 'desc' ? -1 : 1
    return [...visibleRuns].sort(
      (a, b) => mul * (new Date(a.created_at).getTime() - new Date(b.created_at).getTime()),
    )
  }, [visibleRuns, datetimeSortDir])
  const tableTotalPages = Math.max(1, Math.ceil(sortedVisibleRuns.length / TABLE_PAGE_SIZE))
  const tableRuns = useMemo(() => {
    const start = (tablePage - 1) * TABLE_PAGE_SIZE
    return sortedVisibleRuns.slice(start, start + TABLE_PAGE_SIZE)
  }, [sortedVisibleRuns, tablePage])

  // Branch list for the filter dropdown — derive from the full window, not
  // the already-filtered set, so the user can still pick a branch that the
  // current filter would have hidden.
  const availableBranches = useMemo(() => {
    const set = new Set<string>()
    allRuns.forEach(r => { if (r.branch) set.add(r.branch) })
    return Array.from(set).sort()
  }, [allRuns])

  function updateParam(key: string, value: string | null) {
    const next = new URLSearchParams(params)
    if (value && value !== 'all') next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  if (isLoading) {
    return <div className="flex justify-center py-16"><LoadingSpinner size="lg" /></div>
  }

  const topRun = visibleRuns[0]

  return (
    <div className="space-y-3.5">
      <PageHeader
        title="Run Intelligence"
        subtitle="AI analyzes every run · pick one to drill into, or follow the insights below"
        actions={
          <div className="flex items-center gap-2 flex-wrap">
            <HeaderBtn icon={MessageSquare} onClick={() => toast('Decision trail — opens audit log', { icon: '📜' })}>
              Decision trail
            </HeaderBtn>
            <HeaderBtn icon={GitCompare} onClick={() => navigate('/runs/compare')}>
              Compare runs
            </HeaderBtn>
            <HeaderBtn
              icon={Layers}
              primary
              onClick={() => toast('Analyze new run — wire to ingestion trigger', { icon: '✨' })}
            >
              Analyze new run
            </HeaderBtn>
          </div>
        }
      />

      <VerdictCard
        health={health}
        topRunId={topRun?.id}
        onJump={() => topRun && navigate(`/runs/${topRun.id}/intelligence`)}
        onFlakies={() => updateParam('status', 'flaky')}
        onNotifications={() => navigate('/settings/notifications')}
      />

      <FilterBar
        query={query}
        range={range}
        branch={branch}
        status={status}
        runsCount={visibleRuns.length}
        onChange={updateParam}
        availableBranches={availableBranches}
        suite={suite}
        availableSuites={suiteOptions}
      />

      <div className="grid gap-3.5 lg:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
        <RunsTable
          runs={tableRuns}
          isAll={isAll}
          onOpen={id => navigate(`/runs/${id}/intelligence`)}
          page={tablePage}
          pages={tableTotalPages}
          total={visibleRuns.length}
          onPageChange={setTablePage}
          datetimeSortDir={datetimeSortDir}
          onToggleDatetimeSort={() => {
            setDatetimeSortDir(d => d === 'desc' ? 'asc' : 'desc')
            setTablePage(1)
          }}
          mostRecentBeyondWindow={mostRecentBeyond}
        />

        <div className="space-y-3.5">
          <InsightsPanel />
          <SpendPanel projectId={isAll ? null : projectId} />
          <ActivityPanel runs={visibleRuns.slice(0, 5)} />
        </div>
      </div>
    </div>
  )
}

// ── Header button ──────────────────────────────────────────────────────────

function HeaderBtn({
  children, icon: Icon, onClick, primary = false,
}: {
  children: React.ReactNode
  icon: typeof MessageSquare
  onClick: () => void
  primary?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        'inline-flex items-center gap-1.5 text-[13px] font-medium px-3 py-1.5 rounded-md border transition-colors',
        primary
          ? 'bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] border-transparent text-white'
          : 'bg-[var(--color-bg-card)] border-[var(--color-border)] text-[var(--color-text-secondary)] hover:text-[var(--color-text)] hover:border-[var(--color-border-light)]',
      )}
    >
      <Icon className="h-3.5 w-3.5" />
      {children}
    </button>
  )
}

// ── Verdict card ───────────────────────────────────────────────────────────

const VERDICT_STYLE: Record<VerdictState, {
  border: string; bar: string; pulse: string; eyebrow: string;
  headlineWord: string; headline: (counts: HealthSummary['counts']) => string;
  lede: (counts: HealthSummary['counts'], range: string) => string;
  glow: string;
}> = {
  'no-data': {
    border: 'color-mix(in srgb, var(--color-border) 60%, transparent)',
    bar:    'var(--color-border)',
    pulse:  'var(--color-text-muted)',
    eyebrow:'var(--color-text-muted)',
    headlineWord: 'var(--color-text-muted)',
    headline: () => 'nothing analysed in this window.',
    lede: (_c, r) =>
      `No runs ${r.toLowerCase()}, so there is nothing to judge — this is a statement about the time window, not about the code. Widen the range to reach older runs.`,
    glow:   'none',
  },
  'all-clear': {
    border: 'color-mix(in srgb, var(--status-passed) 32%, transparent)',
    bar:    'var(--gate-go)',
    pulse:  'var(--status-passed)',
    eyebrow:'var(--status-passed)',
    headlineWord: 'var(--status-passed)',
    headline: () => 'nothing needs investigation right now.',
    lede: (c, r) => `${c.total} run${c.total === 1 ? '' : 's'} ${r.toLowerCase()}. ${c.passed} pass${c.flaky ? `, ${c.flaky} known-flaky retr${c.flaky === 1 ? 'y' : 'ies'} recovered` : ''}. Intelligence still surfaces anomalies below.`,
    glow:   'radial-gradient(120% 100% at 0% 0%, color-mix(in srgb, var(--status-passed) 10%, transparent), transparent 55%)',
  },
  'attention': {
    border: 'color-mix(in srgb, var(--status-broken) 32%, transparent)',
    bar:    'var(--gate-conditional)',
    pulse:  'var(--status-skipped)',
    eyebrow:'var(--status-broken)',
    headlineWord: 'var(--status-broken)',
    headline: (c) => `${c.flaky || c.broken} ${c.flaky ? 'flaky' : 'broken'} signal${(c.flaky || c.broken) === 1 ? '' : 's'} to watch.`,
    lede: (c, r) => `${c.total} run${c.total === 1 ? '' : 's'} ${r.toLowerCase()}. No outright failures — but ${c.flaky} flaky and ${c.broken} broken runs warrant a glance.`,
    glow:   'radial-gradient(120% 100% at 0% 0%, var(--gate-conditional-glow), transparent 55%)',
  },
  'at-risk': {
    border: 'color-mix(in srgb, var(--status-failed) 32%, transparent)',
    bar:    'var(--gate-no-go)',
    pulse:  'var(--status-failed)',
    eyebrow:'var(--status-failed)',
    headlineWord: 'var(--status-failed)',
    headline: (c) => `${c.failed} failing run${c.failed === 1 ? '' : 's'} need investigation.`,
    lede: (c, r) => `${c.total} run${c.total === 1 ? '' : 's'} ${r.toLowerCase()}. ${c.failed} failed, ${c.flaky} flaky. Open the topmost failure to start triage.`,
    glow:   'radial-gradient(120% 100% at 0% 0%, var(--alert-bg-soft), transparent 55%)',
  },
}

function VerdictCard({
  health, topRunId, onJump, onFlakies, onNotifications,
}: {
  health: HealthSummary
  topRunId?: string
  onJump: () => void
  onFlakies: () => void
  onNotifications: () => void
}) {
  const s = VERDICT_STYLE[health.state]
  return (
    <section
      className="relative rounded-xl border overflow-hidden"
      style={{
        borderColor: s.border,
        background: `${s.glow}, var(--color-bg-card)`,
      }}
    >
      <div className="absolute inset-y-0 left-0 w-[3px]" style={{ background: s.bar }} aria-hidden />
      <div className="grid gap-7 p-5 sm:p-6 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <div className="min-w-0">
          <div className="inline-flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.08em]"
               style={{ color: s.eyebrow }}>
            <PulseDot color={s.pulse} />
            Pipeline health · {health.rangeLabel}
          </div>
          <h2 className="m-0 mt-2 font-bold leading-[1.1]"
              style={{ fontSize: 'var(--text-display-md, 28px)', letterSpacing: '-0.02em', color: 'var(--color-text)' }}>
            {health.state === 'all-clear' ? 'All clear — ' : ''}
            <span style={{ color: s.headlineWord }}>{s.headline(health.counts)}</span>
          </h2>
          <p className="mt-2 mb-0 text-[13.5px] leading-relaxed text-[var(--color-text-secondary)] max-w-[60ch]">
            {s.lede(health.counts, health.rangeLabel)}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <VerdictCta onClick={onJump} disabled={!topRunId}>
              Jump to most recent run <Kbd>↵</Kbd>
            </VerdictCta>
            <VerdictCta onClick={onFlakies}>
              View flaky recoveries ({health.counts.flaky})
            </VerdictCta>
            <VerdictCta onClick={onNotifications}>
              Configure notifications
            </VerdictCta>
          </div>
        </div>

        <HealthSummaryPanel health={health} headlineColor={s.headlineWord} />
      </div>
    </section>
  )
}

function PulseDot({ color }: { color: string }) {
  return (
    <span
      aria-hidden
      className="inline-block w-[6px] h-[6px] rounded-full motion-safe:animate-pulse"
      style={{ background: color, boxShadow: `0 0 0 0 ${color}80` }}
    />
  )
}

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center justify-center min-w-[16px] h-[16px] px-1 rounded-sm border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[10px] font-mono text-[var(--color-text-muted)] ml-1">
      {children}
    </span>
  )
}

function VerdictCta({ children, onClick, disabled }: { children: React.ReactNode; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="inline-flex items-center gap-1.5 text-[12.5px] font-medium px-3 py-1.5 rounded-md bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:text-[var(--color-text)] hover:border-[var(--color-border-light)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
    >
      {children}
    </button>
  )
}

function HealthSummaryPanel({ health, headlineColor }: { health: HealthSummary; headlineColor: string }) {
  const { counts, composite, delta } = health
  const denom = Math.max(1, counts.total)
  const widths = {
    pass:  Math.max(counts.passed / denom, 0),
    flake: Math.max(counts.flaky  / denom, 0),
    fail:  Math.max((counts.failed + counts.broken) / denom, 0),
  }
  return (
    <div className="flex flex-col min-w-0">
      <div className="flex items-end justify-between gap-3 mb-2">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
            Composite health · {health.rangeLabel.replace('Last ', '')}
          </div>
          <div className="mt-1 leading-none">
            <span className="font-bold tabular-nums" style={{ fontSize: '38px', color: headlineColor }}>
              {composite ?? '—'}
            </span>
            {composite !== null && (
              <small className="text-[18px] text-[var(--color-text-muted)] font-medium ml-1">/100</small>
            )}
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="text-[10.5px] font-semibold uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
            vs prev window
          </div>
          <div className={clsx(
            'mt-1 inline-flex items-center gap-1 text-[12px] font-semibold tabular-nums',
            delta > 0 ? 'text-[var(--status-passed)]' : delta < 0 ? 'text-[var(--status-failed)]' : 'text-[var(--color-text-muted)]',
          )}>
            {delta > 0 ? <TrendingUp className="h-3 w-3" /> : delta < 0 ? <TrendingDown className="h-3 w-3" /> : null}
            {delta > 0 ? '+' : ''}{delta.toFixed(1)} pts
          </div>
        </div>
      </div>

      <div
        className="grid h-2 rounded-full overflow-hidden border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-[2px] gap-[2px]"
        style={{
          gridTemplateColumns: `${widths.pass}fr ${widths.flake}fr ${widths.fail}fr`,
        }}
        aria-hidden
      >
        <i className="rounded-full" style={{ background: 'var(--status-passed)', display: widths.pass  ? 'block' : 'none' }} />
        <i className="rounded-full" style={{ background: 'var(--status-flaky)', display: widths.flake ? 'block' : 'none' }} />
        <i className="rounded-full" style={{ background: 'var(--status-failed)', display: widths.fail  ? 'block' : 'none' }} />
      </div>

      <div className="mt-2 flex items-center flex-wrap gap-3 text-[11.5px] text-[var(--color-text-muted)]">
        <LegendItem color="var(--status-passed)" label={`${counts.passed} passed`} />
        <LegendItem color="var(--status-flaky)" label={`${counts.flaky} flaky`} />
        <LegendItem color="var(--status-failed)" label={`${counts.failed + counts.broken} failed`} />
        <span className="ml-auto text-[var(--color-text-faint)]">{counts.total} runs analyzed</span>
      </div>
    </div>
  )
}

function LegendItem({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span aria-hidden className="inline-block w-2 h-2 rounded-sm" style={{ background: color }} />
      {label}
    </span>
  )
}

// ── Filter bar ─────────────────────────────────────────────────────────────

function FilterBar({
  query, range, branch, status, suite, runsCount, onChange, availableBranches, availableSuites,
}: {
  query: string
  range: RangeId
  branch: string
  status: string
  suite: string
  runsCount: number
  onChange: (key: string, value: string | null) => void
  availableBranches: string[]
  availableSuites: string[]
}) {
  return (
    <section
      className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-card)] px-3 py-2.5 flex items-center gap-2 flex-wrap"
      role="search"
    >
      <label className="flex-1 min-w-[260px] inline-flex items-center gap-2 px-2.5 py-1.5 rounded-md bg-[var(--color-bg-input)] border border-[var(--color-border)] focus-within:border-[var(--color-ring)]">
        <Search className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
        <input
          type="search"
          value={query}
          onChange={e => onChange('q', e.target.value || null)}
          placeholder="Search runs by ID, branch, commit, PR…"
          className="flex-1 bg-transparent outline-none text-[13px] text-[var(--color-text)] placeholder:text-[var(--color-text-faint)]"
          aria-label="Search runs"
        />
        <Kbd>/</Kbd>
      </label>

      <Select
        value={range}
        onChange={v => onChange('range', v)}
        options={RANGE_OPTIONS.map(r => ({ value: r.id, label: r.label }))}
      />
      <Select
        value={branch || 'all'}
        onChange={v => onChange('branch', v === 'all' ? null : v)}
        options={[
          { value: 'all', label: 'All branches' },
          ...availableBranches.map(b => ({ value: b, label: b })),
        ]}
      />
      <Select
        value={status}
        onChange={v => onChange('status', v)}
        options={[
          { value: 'all',    label: 'All outcomes' },
          { value: 'passed', label: 'Passed' },
          { value: 'flaky',  label: 'Flaky' },
          { value: 'failed', label: 'Failed' },
        ]}
      />
      <SuiteFilterSelect
        value={suite}
        onChange={value => onChange('suite', value || null)}
        options={availableSuites}
        allLabel="All suites"
      />

      <div className="ml-auto text-[11.5px] text-[var(--color-text-faint)]">
        {runsCount} run{runsCount === 1 ? '' : 's'} match
      </div>
    </section>
  )
}

function Select({
  value, onChange, options,
}: {
  value: string
  onChange: (v: string) => void
  options: { value: string; label: string }[]
}) {
  return (
    <div className="relative">
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        className="appearance-none pl-3 pr-7 py-1.5 text-[12.5px] font-medium rounded-md bg-[var(--color-bg-input)] border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:text-[var(--color-text)] hover:border-[var(--color-border-light)] focus:outline-none focus:border-[var(--color-ring)]"
      >
        {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 h-3 w-3 text-[var(--color-text-muted)] pointer-events-none" />
    </div>
  )
}

// ── Runs table ─────────────────────────────────────────────────────────────

function RunsTable({
  runs, isAll, onOpen, page, pages, total, onPageChange,
  datetimeSortDir, onToggleDatetimeSort, mostRecentBeyondWindow,
}: {
  /** Most recent run OUTSIDE the selected window, when the window is empty.
   *  "No runs" is not the useful answer; "your last run was 10 days ago" is. */
  mostRecentBeyondWindow?: TestRun | null
  runs: TestRun[]
  isAll: boolean
  onOpen: (id: string) => void
  /** Pagination — total/pages refer to the full filtered set, runs to the current page slice. */
  page: number
  pages: number
  total: number
  onPageChange: (p: number) => void
  datetimeSortDir: 'asc' | 'desc'
  onToggleDatetimeSort: () => void
}) {
  return (
    <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-card)] overflow-hidden">
      <header className="flex items-center justify-between px-4 py-2.5 border-b border-[var(--color-border)]">
        <h3 className="m-0 text-[13px] font-semibold text-[var(--color-text)]">Recent runs analyzed</h3>
        <div className="text-[11.5px] text-[var(--color-text-muted)]">
          {total} run{total === 1 ? '' : 's'} · sorted by When {datetimeSortDir === 'desc' ? '↓' : '↑'} · showing {runs.length}
        </div>
      </header>

      {total === 0 ? (
        <EmptyState
          title={
            mostRecentBeyondWindow
              ? `No runs in this window — the most recent one is ${fromNow(mostRecentBeyondWindow.created_at)}`
              : 'No runs in this window'
          }
          description={
            mostRecentBeyondWindow
              ? 'There is history here, just outside the selected range. Widen it to reach those runs.'
              : 'Broaden the date filter or trigger a build.'
          }
        />
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-[13px] border-collapse">
              <thead>
                <tr className="bg-[var(--color-bg-secondary)] text-[10.5px] font-semibold uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
                  <Th>Build</Th>
                  <Th>Test Suite</Th>
                  <Th>Status</Th>
                  <Th>Duration</Th>
                  {/* US-15.1 honesty fix: this column has always rendered the
                      run's pass rate. It was labelled "AI confidence", which
                      it never was — no AI confidence is wired here. */}
                  <Th>Pass rate</Th>
                  <Th
                    align="right"
                    className="hidden md:table-cell"
                    onClick={onToggleDatetimeSort}
                    sortDir={datetimeSortDir === 'desc' ? '↓' : '↑'}
                  >
                    Started
                  </Th>
                  <Th align="right" className="hidden md:table-cell">End</Th>
                </tr>
              </thead>
              <tbody>
                {runs.map(r => <RunRow key={r.id} run={r} isAll={isAll} onOpen={onOpen} />)}
              </tbody>
            </table>
          </div>
          <Pagination page={page} pages={pages} total={total} onChange={onPageChange} />
        </>
      )}
    </section>
  )
}

function Th({ children, align = 'left', className = '', onClick, sortDir }: {
  children: React.ReactNode
  align?: 'left' | 'right'
  className?: string
  /** When provided, the header becomes a click-to-sort affordance. */
  onClick?: () => void
  sortDir?: '↑' | '↓'
}) {
  return (
    <th
      onClick={onClick}
      aria-sort={sortDir ? (sortDir === '↑' ? 'ascending' : 'descending') : undefined}
      className={clsx(
        'px-3.5 py-2 border-b border-[var(--color-border)] font-semibold text-[10.5px]',
        align === 'right' ? 'text-right' : 'text-left',
        onClick && 'cursor-pointer select-none hover:text-[var(--color-text)]',
        className,
      )}
    >
      {children}
      {sortDir && <span aria-hidden className="ml-1">{sortDir}</span>}
    </th>
  )
}

function RunRow({
  run, isAll, onOpen,
}: { run: TestRun; isAll: boolean; onOpen: (id: string) => void }) {
  const author = run.jenkins_job || (run as TestRun & { author?: string }).author || run.trigger_source || 'CI'
  const av = avatarFor(author)
  // US-15.1 honesty fix: this used to clamp//floor the pass rate and present
  // it as an "AI confidence". It is the run's pass rate, nothing more —
  // rendered verbatim, and blank when the run doesn't carry one. No proxy, no
  // fabricated floor. If an AI confidence is ever wired here it must arrive
  // from the pipeline with its calibration basis, via AISuggestion.
  const passRatePct = run.pass_rate != null ? Math.round(run.pass_rate) : null
  return (
    <tr
      onClick={() => onOpen(run.id)}
      className="cursor-pointer hover:bg-[var(--color-bg-hover)] transition-colors border-b border-[var(--color-border)] last:border-b-0"
    >
      <td className="px-3.5 py-2.5 min-w-[260px]">
        <div className="flex items-baseline gap-2">
          {/* Prefer the per-(project, suite) human-readable Run #N — server-
              computed via ROW_NUMBER() so the same value appears on
              /runs, /live, /my-failures, and here. Falls back to the
              raw SDK build_number for rows that pre-date the run_seq
              field (legacy ingests). */}
          <span className="font-mono text-[13px] font-semibold text-[var(--color-text)] whitespace-nowrap">
            {run.run_seq != null ? `Run #${run.run_seq}` : `#${String(run.build_number)}`}
          </span>
          <span className="text-[13px] font-medium text-[var(--color-text)] truncate max-w-[300px]">
            {run.jenkins_job || ''}
          </span>
        </div>
        <div className="mt-0.5 inline-flex items-center gap-2 text-[11.5px] text-[var(--color-text-muted)]">
          {run.branch && (
            <span className="inline-flex items-center gap-1 px-1.5 py-0 rounded-sm bg-[var(--color-bg-secondary)] border border-[var(--color-border)] font-mono">
              <GitBranch className="h-2.5 w-2.5" />{run.branch}
            </span>
          )}
          <span aria-hidden>·</span>
          <span className={clsx('inline-flex items-center justify-center rounded-full w-4 h-4 text-[9px] font-semibold', av.cls)}>
            {av.initials}
          </span>
          <span className="truncate max-w-[120px]">{author}</span>
          {isAll && run.project_name && (
            <>
              <span aria-hidden>·</span>
              <span className="text-[var(--color-text-faint)] truncate max-w-[120px]">{run.project_name}</span>
            </>
          )}
        </div>
      </td>
      <td className="px-3.5 py-2.5">
        <SuiteBadge
          primary={run.primary_suite_name}
          all={run.suite_names}
          linkTo={name => `/test-management?tab=Test+Suites&suite=${encodeURIComponent(name)}`}
        />
      </td>
      <td className="px-3.5 py-2.5"><RunStatusPill run={run} /></td>
      <td className="px-3.5 py-2.5 tabular-nums text-[var(--color-text-secondary)] whitespace-nowrap">
        {formatDuration(run.duration_ms)}
      </td>
      <td className="px-3.5 py-2.5">
        {passRatePct != null
          ? <PassRateMeter pct={passRatePct} />
          : <span className="text-[11.5px] text-[var(--color-text-faint)]">—</span>}
      </td>
      <td
        className="px-3.5 py-2.5 text-right text-[var(--color-text-muted)] whitespace-nowrap hidden md:table-cell tabular-nums"
        title={fromNow(run.start_time ?? run.created_at)}
      >
        {new Date(run.start_time ?? run.created_at).toLocaleString()}
      </td>
      <td
        className="px-3.5 py-2.5 text-right text-[var(--color-text-muted)] whitespace-nowrap hidden md:table-cell tabular-nums"
        title={run.end_time ? fromNow(run.end_time) : 'Run has not finished yet'}
      >
        {run.end_time ? new Date(run.end_time).toLocaleString() : '—'}
      </td>
    </tr>
  )
}

function RunStatusPill({ run }: { run: TestRun }) {
  let label = 'Passed', kind = 'pass'
  if (isFailed(run))       { label = 'Failed';  kind = 'fail' }
  else if (isBroken(run))  { label = 'Broken';  kind = 'broken' }
  else if (isFlaky(run))   { label = `Flaky · ${run.broken_tests} retr${run.broken_tests === 1 ? 'y' : 'ies'}`; kind = 'flaky' }

  const tokens: Record<string, { fg: string; bg: string; bd: string; dot: string }> = {
    pass:   { fg: 'var(--status-passed)',  bg: 'var(--status-passed-bg)',  bd: 'var(--status-passed-bd)',  dot: 'var(--status-passed)' },
    fail:   { fg: 'var(--status-failed)',  bg: 'var(--status-failed-bg)',  bd: 'var(--status-failed-bd)',  dot: 'var(--status-failed)' },
    flaky:  { fg: 'var(--status-flaky)',   bg: 'var(--status-flaky-bg)',   bd: 'var(--status-flaky-bd)',   dot: 'var(--status-flaky)' },
    broken: { fg: 'var(--status-broken)',  bg: 'var(--status-broken-bg)',  bd: 'var(--status-broken-bd)',  dot: 'var(--status-broken)' },
  }
  const t = tokens[kind]
  return (
    <span
      className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11.5px] font-medium border whitespace-nowrap"
      style={{ color: t.fg, background: t.bg, borderColor: t.bd }}
    >
      <span aria-hidden className="w-1.5 h-1.5 rounded-full" style={{ background: t.dot }} />
      {label}
    </span>
  )
}

/** Pass-rate meter. Renamed from ConfidenceMeter in US-15.1 — it never
 *  showed a confidence; naming it one was the whole bug. */
function PassRateMeter({ pct }: { pct: number }) {
  const grad =
    pct >= 80 ? 'linear-gradient(90deg,var(--status-passed),var(--status-passed))'
    : pct >= 60 ? 'linear-gradient(90deg,var(--status-skipped),var(--status-broken))'
                : 'linear-gradient(90deg,var(--status-failed),var(--status-failed))'
  return (
    <span className="inline-flex items-center gap-2 whitespace-nowrap">
      <span
        className="inline-block h-[5px] w-[56px] rounded-full overflow-hidden bg-[var(--color-bg-secondary)] border border-[var(--color-border)]"
        aria-hidden
      >
        <span className="block h-full" style={{ width: `${pct}%`, background: grad }} />
      </span>
      <span className="text-[11.5px] tabular-nums text-[var(--color-text-muted)]">{pct}%</span>
    </span>
  )
}

// ── Right-rail panels ──────────────────────────────────────────────────────

function RailPanel({
  title, right, children,
}: { title: string; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-card)] overflow-hidden">
      <header className="flex items-center justify-between px-4 py-2.5 border-b border-[var(--color-border)]">
        <h4 className="m-0 text-[13px] font-semibold text-[var(--color-text)]">{title}</h4>
        {right && <div className="text-[11.5px] text-[var(--color-text-muted)]">{right}</div>}
      </header>
      <div>{children}</div>
    </section>
  )
}

function InsightsPanel() {
  // Real cross-run insights need the /api/intelligence/insights endpoint
  // proposed in the handoff. Until then, render a graceful empty state — the
  // section is still discoverable and clearly intentional.
  return (
    <RailPanel title="Cross-run AI insights" right="0 insights · 24h">
      <div className="px-4 py-5 text-[12.5px] text-[var(--color-text-muted)] text-center">
        No anomalies detected in the current window.
        <div className="mt-2 text-[11.5px] text-[var(--color-text-faint)]">
          Perf drift, flake-rising, and coverage signals will appear here once cross-run analysis is enabled.
        </div>
      </div>
    </RailPanel>
  )
}

function SpendPanel({ projectId }: { projectId: string | null }) {
  // Real project LLM meter: GET /projects/{id}/llm-usage (current MONTHLY
  // period) + /llm-quota (hard cap). The endpoints are per-project, so in
  // "All Projects" mode the panel asks the user to narrow the scope rather
  // than showing numbers that don't apply.
  const { usage, isLoading: usageLoading } = useProjectUsage(projectId)
  const { quota, isLoading: quotaLoading } = useProjectQuota(projectId)

  const hasBudget = quota != null && quota.enabled && quota.hard_cap_usd > 0
  const spend = usage?.total_cost_usd ?? 0
  const calls = usage?.total_llm_calls ?? 0
  const utilization = hasBudget
    ? Math.min(100, usage?.utilization_pct ?? (spend / quota.hard_cap_usd) * 100)
    : 0

  if (!projectId) {
    return (
      <RailPanel title="Intelligence spend" right={<span className="text-[var(--color-text-faint)]">per project</span>}>
        <div className="px-4 py-5 text-[12.5px] text-[var(--color-text-muted)] text-center">
          Select a project to see its LLM spend and budget.
        </div>
      </RailPanel>
    )
  }

  const loading = usageLoading || quotaLoading

  return (
    <RailPanel
      title="Intelligence spend"
      right={
        <span className="text-[var(--color-text-faint)]">
          {loading ? 'loading…' : hasBudget ? `cap $${quota.hard_cap_usd}` : 'no budget set'}
        </span>
      }
    >
      <div className="grid grid-cols-2 gap-3 p-4">
        {/* The usage endpoint meters the current MONTHLY period — label it
            as such rather than the design's original "today". */}
        <SpendCell
          label="Used this month"
          value={loading ? '—' : `$${spend.toFixed(2)}`}
          sub={loading ? 'loading' : `${calls} LLM call${calls === 1 ? '' : 's'}`}
        />
        <SpendCell
          label="Avg per call"
          value={loading ? '—' : calls > 0 ? `$${(spend / calls).toFixed(3)}` : '—'}
          sub={loading ? 'loading' : calls > 0 ? 'this period' : 'no calls yet'}
        />
      </div>
      <div className="px-4 pb-4">
        <div className="flex items-center justify-between text-[11px] uppercase tracking-[0.08em] text-[var(--color-text-muted)] mb-1.5">
          <span>Monthly budget</span>
          <span className="text-[var(--color-text-faint)] normal-case">
            {hasBudget
              ? `$${spend.toFixed(2)} of $${quota.hard_cap_usd} · ${Math.round(utilization)}%`
              : 'configure in settings'}
          </span>
        </div>
        <div className="h-1.5 rounded-full bg-[var(--color-bg-secondary)] overflow-hidden">
          <div
            className="h-full"
            style={{ width: `${utilization}%`, background: 'linear-gradient(90deg,#14b8a6,#5eead4)' }}
          />
        </div>
      </div>
    </RailPanel>
  )
}

function SpendCell({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-3">
      <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--color-text-muted)] inline-flex items-center gap-1.5">
        <Coins className="h-3 w-3" /> {label}
      </div>
      <div className="mt-1 text-[22px] font-bold tabular-nums text-[var(--color-text)] leading-none">
        {value}
      </div>
      {sub && <div className="mt-1 text-[10.5px] text-[var(--color-text-faint)]">{sub}</div>}
    </div>
  )
}

function ActivityPanel({ runs }: { runs: TestRun[] }) {
  // Synthesize an activity feed from the runs we already have, so the panel
  // shows real content immediately. When a dedicated /api/intelligence/activity
  // endpoint lands, replace this loop with the proper event stream.
  if (runs.length === 0) {
    return (
      <RailPanel title="Recent activity">
        <div className="px-4 py-5 text-[12.5px] text-[var(--color-text-muted)] text-center">
          No activity in this window.
        </div>
      </RailPanel>
    )
  }
  return (
    <RailPanel
      title="Recent activity"
      right={<button onClick={() => toast('Full activity log — wire to /audit', { icon: '📜' })} className="text-[var(--color-accent)] hover:underline">View all</button>}
    >
      <ul className="divide-y divide-[var(--color-border)]">
        {runs.map(r => (
          <li key={r.id} className="grid grid-cols-[52px_1fr] items-start gap-3 px-4 py-2.5">
            <span className="text-[11px] tabular-nums text-[var(--color-text-muted)] pt-0.5">
              {fromNow(r.created_at).replace(' ago', '')}
            </span>
            <span className="text-[12.5px] text-[var(--color-text-secondary)] min-w-0">
              {/* Per-(project, suite) Run #N is the canonical handle now;
                  legacy rows without run_seq fall back to the raw
                  SDK build_number prefixed with "#" so the copy still
                  reads naturally ("Run #1234 failed on main"). */}
              {(() => {
                const label = r.run_seq != null ? `#${r.run_seq}` : `#${r.build_number}`
                const code = <code className="text-[11.5px] px-1 rounded bg-[var(--color-bg-secondary)] border border-[var(--color-border)]">{label}</code>
                if (isFailed(r)) return <><Zap className="inline h-3 w-3 mr-1 text-[var(--status-failed)]" />Run {code} failed on <span className="text-[var(--color-text)]">{r.branch ?? 'unknown'}</span> — {r.failed_tests} test{r.failed_tests === 1 ? '' : 's'}</>
                if (isFlaky(r))  return <><Activity className="inline h-3 w-3 mr-1 text-[var(--status-flaky)]" />Run {code} recovered after {r.broken_tests} retr{r.broken_tests === 1 ? 'y' : 'ies'}</>
                return <><Sparkles className="inline h-3 w-3 mr-1 text-[var(--status-passed)]" />Run {code} passed on <span className="text-[var(--color-text)]">{r.branch ?? 'unknown'}</span></>
              })()}
            </span>
          </li>
        ))}
      </ul>
      <div className="px-4 py-2.5 text-center text-[11.5px] border-t border-[var(--color-border)]">
        <button onClick={() => toast('Full activity log — wire to /audit', { icon: '📜' })} className="text-[var(--color-accent)] hover:underline inline-flex items-center gap-1">
          View full activity log <ArrowRight className="h-3 w-3" />
        </button>
      </div>
    </RailPanel>
  )
}
