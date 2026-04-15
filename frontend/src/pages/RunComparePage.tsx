import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowLeftRight,
  CheckCircle2,
  Clock,
  GitCompare,
  MinusCircle,
  PlusCircle,
  TrendingDown,
  TrendingUp,
  XCircle,
} from 'lucide-react'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import EmptyState from '@/components/ui/EmptyState'
import { useRunCompare } from '@/hooks/useRunCompare'
import type {
  RunCompareClassification,
  RunCompareTestDelta,
} from '@/services/runCompareService'

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
  const [leftInput, setLeftInput] = useState(searchParams.get('left') ?? '')
  const [rightInput, setRightInput] = useState(searchParams.get('right') ?? '')
  const [filter, setFilter] = useState<RunCompareClassification | 'all'>('all')

  const leftId = searchParams.get('left')
  const rightId = searchParams.get('right')
  const { compare, isLoading, isError } = useRunCompare(leftId, rightId)

  function apply() {
    const next = new URLSearchParams()
    if (leftInput.trim()) next.set('left', leftInput.trim())
    if (rightInput.trim()) next.set('right', rightInput.trim())
    setSearchParams(next)
  }

  function swap() {
    setLeftInput(rightInput)
    setRightInput(leftInput)
    const next = new URLSearchParams()
    if (rightInput.trim()) next.set('left', rightInput.trim())
    if (leftInput.trim()) next.set('right', leftInput.trim())
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
        subtitle="Side-by-side diff of two test runs"
      />

      <section className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-card)] p-3 space-y-2">
        <div className="flex items-end gap-2">
          <label className="text-xs block flex-1">
            <span className="text-[var(--color-text-muted)]">Left run (baseline)</span>
            <input
              type="text"
              value={leftInput}
              onChange={(e) => setLeftInput(e.target.value)}
              placeholder="Run UUID — e.g. a1b2c3d4-..."
              className="mt-1 w-full px-2 py-1.5 text-sm bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded font-mono"
            />
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
            <input
              type="text"
              value={rightInput}
              onChange={(e) => setRightInput(e.target.value)}
              placeholder="Run UUID — e.g. f5e6d7c8-..."
              className="mt-1 w-full px-2 py-1.5 text-sm bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded font-mono"
            />
          </label>
          <button type="button" onClick={apply} className="btn-primary text-xs">
            <GitCompare className="h-3 w-3 inline mr-1" /> Compare
          </button>
        </div>
      </section>

      {!leftId || !rightId ? (
        <EmptyState
          title="Pick two runs to compare"
          description="Paste the UUID of a baseline run on the left and a target run on the right to see per-test deltas, regressions, and duration spikes."
        />
      ) : leftId === rightId ? (
        <EmptyState title="Pick two different runs" />
      ) : isLoading ? (
        <LoadingSpinner size="lg" />
      ) : isError ? (
        <EmptyState title="Failed to load compare" />
      ) : !compare ? null : (
        <div className="space-y-4">
          <SummaryTiles compare={compare} />
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
      <div className="grid grid-cols-2 gap-3">
        <SideCard label="Left (baseline)" buildLabel={leftBuild} run={compare.left} />
        <SideCard label="Right (target)" buildLabel={rightBuild} run={compare.right} />
      </div>

      <div className="grid grid-cols-4 gap-3">
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
            {deltas.map((d) => (
              <tr key={d.test_fingerprint} className="border-t border-[var(--color-border)]">
                <td className="px-3 py-2 text-xs text-[var(--color-text)] truncate max-w-[260px]">
                  {d.test_name || d.test_fingerprint.slice(0, 16)}
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
            ))}
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
  }
  const cfg = map[c]
  const Icon = c === 'new_failure' || c === 'regressed' ? XCircle
    : c === 'fixed' || c === 'improved' ? CheckCircle2
    : c === 'new_test' ? PlusCircle
    : c === 'removed_test' ? MinusCircle
    : AlertTriangle
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded border ${cfg.tone} inline-flex items-center gap-1`}>
      <Icon className="h-2.5 w-2.5" />
      {cfg.label}
    </span>
  )
}
