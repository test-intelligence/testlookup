import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowLeftRight,
  CheckCircle2,
  Clock,
  GitBranch,
  GitCompare,
  Info,
  MinusCircle,
  PlusCircle,
  Sparkles,
  TrendingDown,
  TrendingUp,
  XCircle,
} from 'lucide-react'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import EmptyState from '@/components/ui/EmptyState'
import SuiteBadge from '@/components/ui/SuiteBadge'
import { extractCompareErrorMessage, useLatestSuiteCompare, useRunCompare } from '@/hooks/useRunCompare'
import { useRuns } from '@/hooks/useRuns'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import type {
  RunCompareAIReport,
  RunCompareClassification,
  RunCompareTestDelta,
} from '@/services/runCompareService'
import type { TestRun } from '@/types/runs'

/**
 * Two-run compare page — Tier 2 item 8.
 *
 * Accepts ``left`` and ``right`` run UUIDs via query string (the Run
 * Intelligence and Runs list pages both link here with pre-filled
 * IDs), or lets the user paste them manually. The response is driven
 * entirely by the backend ``/runs/compare`` endpoint so all diff
 * classification happens server-side.
 */
export default function RunComparePage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const activeProjectId = useProjectStore((state) => state.activeProjectId)
  const activeProject = useProjectStore((state) => state.activeProject)
  const [leftInput, setLeftInput] = useState(searchParams.get('left') ?? '')
  const [rightInput, setRightInput] = useState(searchParams.get('right') ?? '')
  const [manualSuiteInput, setManualSuiteInput] = useState(searchParams.get('suite') ?? '')
  const [suiteInput, setSuiteInput] = useState(searchParams.get('suite') ?? '')
  const [filter, setFilter] = useState<RunCompareClassification | 'all'>('all')

  const mode = searchParams.get('mode') ?? 'manual'
  const leftId = searchParams.get('left')
  const rightId = searchParams.get('right')
  const suiteName = searchParams.get('suite')
  const latestSuiteName = mode === 'latest' ? suiteName : null
  const selectedProjectId = activeProjectId && activeProjectId !== ALL_PROJECTS_ID ? activeProjectId : null
  const runsQuery = useMemo(() => ({ page: 1, size: 100, days: 0 }), [])
  const { data: runsData, isLoading: runsLoading } = useRuns(runsQuery)
  const runs = useMemo<TestRun[]>(() => runsData?.items ?? [], [runsData?.items])
  const suiteOptions = useMemo(() => collectSuiteOptions(runs), [runs])
  const manualRunOptions = useMemo(
    () => filterRunsBySuite(runs, manualSuiteInput || suiteInput),
    [manualSuiteInput, runs, suiteInput],
  )
  const latestRunOptions = useMemo(
    () => filterRunsBySuite(runs, suiteInput),
    [runs, suiteInput],
  )
  const manual = useRunCompare(leftId, rightId, mode === 'manual' ? suiteName : null)
  const latest = useLatestSuiteCompare(latestSuiteName, selectedProjectId)
  const compare = mode === 'latest' ? latest.compare : manual.compare
  const isLoading = mode === 'latest' ? latest.isLoading : manual.isLoading
  const isError = mode === 'latest' ? latest.isError : manual.isError
  const error = mode === 'latest' ? latest.error : manual.error

  function apply() {
    const next = new URLSearchParams()
    next.set('mode', 'manual')
    if (leftInput.trim()) next.set('left', leftInput.trim())
    if (rightInput.trim()) next.set('right', rightInput.trim())
    if (manualSuiteInput.trim()) next.set('suite', manualSuiteInput.trim())
    setSearchParams(next)
  }

  function applyLatestSuite() {
    const next = new URLSearchParams()
    next.set('mode', 'latest')
    if (suiteInput.trim()) next.set('suite', suiteInput.trim())
    setSearchParams(next)
  }

  function swap() {
    setLeftInput(rightInput)
    setRightInput(leftInput)
    const next = new URLSearchParams()
    next.set('mode', 'manual')
    if (rightInput.trim()) next.set('left', rightInput.trim())
    if (leftInput.trim()) next.set('right', leftInput.trim())
    if (manualSuiteInput.trim()) next.set('suite', manualSuiteInput.trim())
    setSearchParams(next)
  }

  const filteredDeltas = useMemo(() => {
    if (!compare) return []
    if (filter === 'all') return compare.test_deltas
    return compare.test_deltas.filter((d) => d.classification === filter)
  }, [compare, filter])

  return (
    <div className="space-y-4">
      <PageHeader
        title="Run Compare"
        subtitle="Side-by-side diff of suite or run results"
      />

      <section className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-card)] p-3 space-y-2">
        <div className="flex items-center gap-2">
          <GitBranch className="h-4 w-4 text-[var(--color-accent)]" />
          <div>
            <div className="text-sm font-semibold text-[var(--color-text)]">Latest suite comparison</div>
            <p className="text-xs text-[var(--color-text-muted)] m-0">
              Compares the latest completed run with the previous completed run for the same suite and branch.
            </p>
          </div>
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.35fr)_auto] gap-2 items-end">
          <label className="text-xs block">
            <span className="text-[var(--color-text-muted)]">Suite name</span>
            <select
              value={suiteInput}
              onChange={(e) => setSuiteInput(e.target.value)}
              className="mt-1 w-full px-2 py-1.5 text-sm bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded"
            >
              <option value="">Select suite</option>
              {suiteOptions.map((suite) => (
                <option key={suite} value={suite}>{suite}</option>
              ))}
            </select>
          </label>
          <RunSelectPreview
            label="Runs in suite"
            runs={latestRunOptions}
            isLoading={runsLoading}
            emptyLabel={suiteInput ? 'No runs found for this suite' : 'Select a suite to view runs'}
          />
          <button type="button" onClick={applyLatestSuite} disabled={!suiteInput.trim() || !selectedProjectId} className="btn-primary text-xs disabled:opacity-50">
            <GitCompare className="h-3 w-3 inline mr-1" /> Compare latest
          </button>
        </div>
        {!selectedProjectId && (
          <p className="text-[11px] text-amber-400 m-0">
            Select a single project in the top project selector to compare latest suite runs.
          </p>
        )}
        {activeProject && (
          <p className="text-[11px] text-[var(--color-text-faint)] m-0">
            Project scope: {activeProject.name}
          </p>
        )}
      </section>

      <section className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-card)] p-3 space-y-2">
        <div className="text-sm font-semibold text-[var(--color-text)]">Manual historical comparison</div>
        <div className="flex flex-col lg:flex-row lg:items-end gap-2">
          <label className="text-xs block flex-1">
            <span className="text-[var(--color-text-muted)]">Suite scope</span>
            <select
              value={manualSuiteInput}
              onChange={(e) => {
                setManualSuiteInput(e.target.value)
                setLeftInput('')
                setRightInput('')
              }}
              className="mt-1 w-full px-2 py-1.5 text-sm bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded"
            >
              <option value="">All suites</option>
              {suiteOptions.map((suite) => (
                <option key={suite} value={suite}>{suite}</option>
              ))}
            </select>
          </label>
          <label className="text-xs block flex-1">
            <span className="text-[var(--color-text-muted)]">Left run (baseline)</span>
            <select
              value={leftInput}
              onChange={(e) => setLeftInput(e.target.value)}
              className="mt-1 w-full px-2 py-1.5 text-sm bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded font-mono"
            >
              <option value="">Select baseline run</option>
              {manualRunOptions.map((run) => (
                <option key={run.id} value={run.id}>{formatRunOption(run)}</option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={swap}
            className="btn-secondary text-xs p-2"
            title="Swap left and right"
          >
            <ArrowLeftRight className="h-3 w-3" />
          </button>
          <label className="text-xs block flex-1">
            <span className="text-[var(--color-text-muted)]">Right run (target)</span>
            <select
              value={rightInput}
              onChange={(e) => setRightInput(e.target.value)}
              className="mt-1 w-full px-2 py-1.5 text-sm bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded font-mono"
            >
              <option value="">Select target run</option>
              {manualRunOptions.map((run) => (
                <option key={run.id} value={run.id}>{formatRunOption(run)}</option>
              ))}
            </select>
          </label>
          <button type="button" onClick={apply} className="btn-primary text-xs">
            <GitCompare className="h-3 w-3 inline mr-1" /> Compare
          </button>
        </div>
        {runsLoading && (
          <p className="text-[11px] text-[var(--color-text-muted)] m-0">Loading available suites and runs…</p>
        )}
      </section>

      {mode === 'latest' && !latestSuiteName ? (
        <EmptyState title="Choose a suite" description="Enter a suite name to compare its latest run against the previous run on the same branch." />
      ) : mode !== 'latest' && (!leftId || !rightId) ? (
        <EmptyState
          title="Pick two runs to compare"
          description="Use the suite-first default above, or paste the UUID of a baseline run and target run for an explicit historical comparison."
        />
      ) : mode !== 'latest' && leftId === rightId ? (
        <EmptyState title="Pick two different runs" />
      ) : isLoading ? (
        <LoadingSpinner size="lg" />
      ) : isError ? (
        // Surface the backend's actual ``detail`` instead of a generic
        // "Failed to load". The 404 paths in particular ("At least two
        // completed runs are required …", "Suite X was not found in the
        // left and right run") are useful diagnostics — users need to
        // know whether to wait for more runs or to fix the suite-name
        // mapping.
        (() => {
          const message = extractCompareErrorMessage(error)
          const isInsufficientData =
            message.toLowerCase().includes('at least two')
            || message.toLowerCase().includes('was not found')
          return (
            <EmptyState
              title={isInsufficientData ? 'Not enough data to compare' : 'Failed to load compare'}
              description={message}
            />
          )
        })()
      ) : !compare ? null : (
        <div className="space-y-4">
          <SummaryTiles compare={compare} />
          {compare.selection && <SelectionNotice compare={compare} />}
          {compare.ai_report && <AIReportPanel report={compare.ai_report} />}
          <DeltaFilterBar
            compare={compare}
            filter={filter}
            onFilterChange={setFilter}
          />
          <DeltaTable deltas={filteredDeltas} truncated={compare.truncated} />
        </div>
      )}
    </div>
  )
}

function collectSuiteOptions(runs: TestRun[]): string[] {
  const suites = new Map<string, string>()
  for (const run of runs) {
    const values = [
      run.primary_suite_name,
      ...(run.suite_names ?? []),
    ]
    for (const value of values) {
      const name = value?.trim()
      if (!name) continue
      const key = name.toLowerCase()
      if (!suites.has(key)) suites.set(key, name)
    }
  }
  return [...suites.values()].sort((a, b) => a.localeCompare(b))
}

function runHasSuite(run: TestRun, suiteName: string): boolean {
  const target = suiteName.trim().toLowerCase()
  if (!target) return true
  const names = [
    run.primary_suite_name,
    ...(run.suite_names ?? []),
  ]
  return names.some((name) => name?.trim().toLowerCase() === target)
}

function filterRunsBySuite(runs: TestRun[], suiteName: string): TestRun[] {
  return runs
    .filter((run) => runHasSuite(run, suiteName))
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
}

function formatRunOption(run: TestRun): string {
  // User feedback 2026-05-19: the "Runs in suite" preview should
  // identify the run by its stable Run #N (or short UUID) rather than
  // the SDK-supplied build_number, which is often a noisy timestamp
  // slug (e.g. ``testng-1779170467999``). Falls back to the short id
  // when the run hasn't been numbered yet.
  const seq = (run as TestRun & { run_seq?: number | null }).run_seq
  const ident = seq != null ? `Run #${seq}` : `Run ${run.id.slice(0, 8)}`
  const branch = run.branch ? ` · ${run.branch}` : ''
  const release = run.release_name ? ` · ${run.release_name}` : ''
  const created = new Date(run.created_at).toLocaleString()
  return `${ident} · ${run.status}${branch}${release} · ${created}`
}

function RunSelectPreview({
  label,
  runs,
  isLoading,
  emptyLabel,
}: {
  label: string
  runs: TestRun[]
  isLoading: boolean
  emptyLabel: string
}) {
  return (
    <label className="text-xs block">
      <span className="text-[var(--color-text-muted)]">{label}</span>
      <select
        value=""
        disabled
        className="mt-1 w-full px-2 py-1.5 text-sm bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded font-mono disabled:opacity-100"
      >
        <option>
          {isLoading
            ? 'Loading runs…'
            : runs.length === 0
              ? emptyLabel
              : `${runs.length} available · latest ${formatRunOption(runs[0])}`}
        </option>
      </select>
    </label>
  )
}

// ── Summary tiles ──────────────────────────────────────────────────────────

function SummaryTiles({
  compare,
}: {
  compare: import('@/services/runCompareService').RunCompareResponse
}) {
  const leftBuild = compare.left.build_number || compare.left.id.slice(0, 8)
  const rightBuild = compare.right.build_number || compare.right.id.slice(0, 8)
  const passRateDelta = compare.delta_pass_rate
  const durationDelta = compare.delta_duration_ms

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <SideCard label="Left (baseline)" buildLabel={leftBuild} run={compare.left} />
        <SideCard label="Right (target)" buildLabel={rightBuild} run={compare.right} />
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <DeltaTile
          label="Pass rate"
          value={
            passRateDelta == null
              ? '—'
              : `${passRateDelta >= 0 ? '+' : ''}${passRateDelta.toFixed(1)}%`
          }
          trend={passRateDelta == null ? 'flat' : passRateDelta >= 0 ? 'up' : 'down'}
        />
        <DeltaTile
          label="Failed tests"
          value={
            compare.delta_failed === 0
              ? '±0'
              : `${compare.delta_failed >= 0 ? '+' : ''}${compare.delta_failed}`
          }
          trend={compare.delta_failed > 0 ? 'down' : compare.delta_failed < 0 ? 'up' : 'flat'}
        />
        <DeltaTile
          label="New failures"
          value={String(compare.new_failures)}
          trend={compare.new_failures > 0 ? 'down' : 'flat'}
        />
        <DeltaTile
          label="Duration"
          value={
            durationDelta == null
              ? '—'
              : `${durationDelta >= 0 ? '+' : ''}${(durationDelta / 1000).toFixed(1)}s`
          }
          trend={durationDelta == null ? 'flat' : durationDelta > 0 ? 'down' : 'up'}
        />
      </div>
    </div>
  )
}

function SelectionNotice({
  compare,
}: {
  compare: import('@/services/runCompareService').RunCompareResponse
}) {
  const selection = compare.selection
  if (!selection) return null
  const leftBranch = compare.left.branch || '—'
  const rightBranch = compare.right.branch || '—'
  const sameBranch = leftBranch === rightBranch
  return (
    <section className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-card)] p-3">
      <div className="flex items-start gap-2">
        <Info className="h-4 w-4 text-[var(--color-accent)] mt-0.5" />
        <div className="min-w-0">
          <div className="text-sm font-semibold text-[var(--color-text)]">
            {selection.mode === 'latest_vs_previous' ? 'Latest vs previous suite run' : 'Manual comparison'}
          </div>
          <p className="text-xs text-[var(--color-text-muted)] m-0">
            {selection.selection_reason}
          </p>
          <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-[var(--color-text-muted)]">
            {compare.suite_name && <span>suite: <code>{compare.suite_name}</code></span>}
            <span>left branch: <code>{leftBranch}</code></span>
            <span>right branch: <code>{rightBranch}</code></span>
            <span className={sameBranch ? 'text-emerald-400' : 'text-amber-400'}>
              {sameBranch ? 'same branch' : 'branch differs'}
            </span>
          </div>
        </div>
      </div>
    </section>
  )
}

function AIReportPanel({ report }: { report: RunCompareAIReport }) {
  const tone =
    report.status === 'queued'
      ? 'border-amber-500/40'
      : report.risk_level === 'CRITICAL' || report.risk_level === 'HIGH'
      ? 'border-rose-500/40'
      : report.risk_level === 'MEDIUM'
      ? 'border-amber-500/40'
      : 'border-emerald-500/40'
  return (
    <section className={`rounded-md border ${tone} bg-[var(--color-bg-card)] p-3 space-y-3`}>
      <div className="flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-[var(--color-accent)]" />
        <div className="min-w-0">
          <div className="text-sm font-semibold text-[var(--color-text)]">AI Comparison Report</div>
          <p className="text-xs text-[var(--color-text-muted)] m-0">
            {report.status === 'queued'
              ? report.message || 'Report generation is queued.'
              : `${report.risk_level} risk · confidence ${report.confidence}/100${report.fallback_used ? ' · deterministic fallback' : ''}`}
          </p>
        </div>
      </div>
      {report.status === 'queued' ? null : (
        <>
          <p className="text-sm text-[var(--color-text-secondary)] m-0">{report.executive_summary}</p>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
            <MiniList title="Key differences" items={report.key_differences} />
            <MiniList title="New risks" items={report.new_risks} />
            <MiniList title="Actions" items={report.recommended_actions} />
          </div>
        </>
      )}
    </section>
  )
}

function MiniList({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-2">
      <div className="text-[10px] uppercase tracking-wide text-[var(--color-text-muted)] mb-1">{title}</div>
      {items.length === 0 ? (
        <p className="text-xs text-[var(--color-text-faint)] m-0">None</p>
      ) : (
        <ul className="space-y-1 m-0 p-0 list-none">
          {items.slice(0, 4).map((item, idx) => (
            <li key={`${title}-${idx}`} className="text-xs text-[var(--color-text-secondary)] truncate" title={item}>
              {item}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function SideCard({
  label,
  buildLabel,
  run,
}: {
  label: string
  buildLabel: string
  run: import('@/services/runCompareService').RunCompareSummary
}) {
  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-card)] p-3">
      <div className="text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">
        {label}
      </div>
      <div className="text-sm font-semibold text-[var(--color-text)]">{buildLabel}</div>
      {run.branch && (
        <div className="text-xs text-[var(--color-text-muted)]">branch: {run.branch}</div>
      )}
      <div className="mt-1">
        <SuiteBadge primary={run.primary_suite_name} all={run.suite_names} />
      </div>
      <div className="mt-2 flex items-center gap-3 text-xs">
        <span className="text-emerald-400">✓ {run.passed_tests}</span>
        <span className="text-rose-400">✗ {run.failed_tests}</span>
        <span className="text-amber-400">⚠ {run.broken_tests}</span>
        <span className="text-[var(--color-text-muted)]">◯ {run.skipped_tests}</span>
        <span className="ml-auto text-[var(--color-text-faint)]">
          {run.pass_rate != null ? `${run.pass_rate.toFixed(1)}%` : '—'}
        </span>
      </div>
    </div>
  )
}

function DeltaTile({
  label,
  value,
  trend,
}: {
  label: string
  value: string
  trend: 'up' | 'down' | 'flat'
}) {
  const Icon = trend === 'up' ? TrendingUp : trend === 'down' ? TrendingDown : Clock
  const tone =
    trend === 'up'
      ? 'border-emerald-500/40 text-emerald-400'
      : trend === 'down'
      ? 'border-rose-500/40 text-rose-400'
      : 'border-[var(--color-border)] text-[var(--color-text-muted)]'
  return (
    <div className={`rounded-md border ${tone} bg-[var(--color-bg-card)] p-3`}>
      <div className="text-[10px] uppercase tracking-wide text-[var(--color-text-muted)] flex items-center gap-1">
        <Icon className="h-3 w-3" />
        {label}
      </div>
      <div className="text-lg font-semibold text-[var(--color-text)] mt-1">{value}</div>
    </div>
  )
}

// ── Filter bar ─────────────────────────────────────────────────────────────

const FILTER_OPTIONS: Array<{
  key: RunCompareClassification | 'all'
  label: string
  countKey?: keyof import('@/services/runCompareService').RunCompareResponse
}> = [
  { key: 'all', label: 'All' },
  { key: 'new_failure', label: 'New failures', countKey: 'new_failures' },
  { key: 'regressed', label: 'Regressed', countKey: 'regressed' },
  { key: 'still_failing', label: 'Still failing', countKey: 'still_failing' },
  { key: 'duration_spike', label: 'Duration spikes', countKey: 'duration_spikes' },
  { key: 'fixed', label: 'Fixed', countKey: 'fixed' },
  { key: 'new_test', label: 'New tests', countKey: 'new_tests' },
  { key: 'removed_test', label: 'Removed', countKey: 'removed_tests' },
  { key: 'renamed', label: 'Renamed', countKey: 'renamed' },
]

function DeltaFilterBar({
  compare,
  filter,
  onFilterChange,
}: {
  compare: import('@/services/runCompareService').RunCompareResponse
  filter: RunCompareClassification | 'all'
  onFilterChange: (f: RunCompareClassification | 'all') => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {FILTER_OPTIONS.map((opt) => {
        const count =
          opt.countKey && typeof compare[opt.countKey] === 'number'
            ? (compare[opt.countKey] as number)
            : opt.key === 'all'
            ? compare.test_deltas.length
            : 0
        const active = filter === opt.key
        return (
          <button
            key={opt.key}
            type="button"
            onClick={() => onFilterChange(opt.key)}
            className={`text-xs px-2 py-1 rounded border ${
              active
                ? 'border-[var(--color-accent)] bg-[var(--color-accent)]/10 text-[var(--color-accent)]'
                : 'border-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
            }`}
          >
            {opt.label} ({count})
          </button>
        )
      })}
    </div>
  )
}

// ── Delta table ───────────────────────────────────────────────────────────

function DeltaTable({
  deltas,
  truncated,
}: {
  deltas: RunCompareTestDelta[]
  truncated: boolean
}) {
  if (deltas.length === 0) {
    return (
      <EmptyState
        title="No matching deltas"
        description="Try clearing the filter or comparing two different runs."
      />
    )
  }
  return (
    <div className="space-y-2">
      <div className="overflow-hidden rounded-md border border-[var(--color-border)]">
        <table className="w-full text-sm">
          <thead className="bg-[var(--color-bg-secondary)] text-xs text-[var(--color-text-muted)]">
            <tr>
              <th className="text-left px-3 py-2">Test</th>
              <th className="text-left px-3 py-2">Suite</th>
              <th className="text-center px-3 py-2">Left</th>
              <th className="text-center px-3 py-2">→</th>
              <th className="text-center px-3 py-2">Right</th>
              <th className="text-right px-3 py-2">Δ Duration</th>
              <th className="text-left px-3 py-2">Change</th>
            </tr>
          </thead>
          <tbody>
            {deltas.map((d) => {
              const isRenamed =
                d.paired_by === 'fuzzy_name_match' &&
                !!d.previous_test_name &&
                d.previous_test_name !== d.test_name
              return (
              <tr key={d.test_fingerprint} className="border-t border-[var(--color-border)]">
                <td className="px-3 py-2 text-xs text-[var(--color-text)] truncate max-w-[260px]">
                  <div className="truncate">
                    {d.test_name || d.test_fingerprint.slice(0, 16)}
                  </div>
                  {isRenamed && (
                    <div
                      className="text-[10px] text-[var(--color-text-faint)] truncate"
                      title={d.previous_test_name ?? undefined}
                    >
                      was: {d.previous_test_name}
                    </div>
                  )}
                </td>
                <td className="px-3 py-2 text-xs text-[var(--color-text-muted)] truncate max-w-[200px]">
                  {d.suite_name || '—'}
                </td>
                <td className="px-3 py-2 text-center">
                  <StatusIcon status={d.left_status} />
                </td>
                <td className="px-3 py-2 text-center text-[var(--color-text-faint)]">→</td>
                <td className="px-3 py-2 text-center">
                  <StatusIcon status={d.right_status} />
                </td>
                <td className="px-3 py-2 text-right text-xs font-mono text-[var(--color-text-muted)]">
                  {d.delta_duration_ms == null
                    ? '—'
                    : `${d.delta_duration_ms >= 0 ? '+' : ''}${(d.delta_duration_ms / 1000).toFixed(1)}s`}
                </td>
                <td className="px-3 py-2 text-left">
                  <ClassificationBadge c={d.classification} />
                </td>
              </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {truncated && (
        <p className="text-[10px] text-[var(--color-text-faint)]">
          Showing the first 500 deltas, sorted by urgency.
        </p>
      )}
    </div>
  )
}

function StatusIcon({ status }: { status: string | null }) {
  if (!status) return <MinusCircle className="h-3.5 w-3.5 text-[var(--color-text-faint)] inline" />
  const s = status.toUpperCase()
  if (s === 'PASSED') return <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400 inline" />
  if (s === 'FAILED') return <XCircle className="h-3.5 w-3.5 text-rose-400 inline" />
  if (s === 'BROKEN') return <AlertTriangle className="h-3.5 w-3.5 text-amber-400 inline" />
  if (s === 'SKIPPED')
    return <MinusCircle className="h-3.5 w-3.5 text-[var(--color-text-muted)] inline" />
  return <span className="text-xs text-[var(--color-text-muted)]">{status}</span>
}

function ClassificationBadge({ c }: { c: RunCompareClassification }) {
  const map: Record<RunCompareClassification, { label: string; tone: string }> = {
    new_failure: { label: 'new failure', tone: 'border-rose-500/40 text-rose-400 bg-rose-500/10' },
    regressed: { label: 'regressed', tone: 'border-rose-500/40 text-rose-400' },
    still_failing: { label: 'still failing', tone: 'border-amber-500/40 text-amber-400' },
    duration_spike: { label: 'duration spike', tone: 'border-amber-500/40 text-amber-400' },
    fixed: { label: 'fixed', tone: 'border-emerald-500/40 text-emerald-400 bg-emerald-500/10' },
    improved: { label: 'improved', tone: 'border-emerald-500/40 text-emerald-400' },
    new_test: { label: 'new test', tone: 'border-[var(--color-accent)]/40 text-[var(--color-accent)]' },
    removed_test: { label: 'removed', tone: 'border-[var(--color-border)] text-[var(--color-text-muted)]' },
    renamed: { label: 'renamed', tone: 'border-sky-500/40 text-sky-400' },
  }
  const cfg = map[c]
  const Icon = c === 'new_failure' || c === 'regressed' ? XCircle
    : c === 'fixed' || c === 'improved' ? CheckCircle2
    : c === 'new_test' ? PlusCircle
    : c === 'removed_test' ? MinusCircle
    : c === 'renamed' ? ArrowLeftRight
    : AlertTriangle
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded border ${cfg.tone} inline-flex items-center gap-1`}>
      <Icon className="h-2.5 w-2.5" />
      {cfg.label}
    </span>
  )
}
