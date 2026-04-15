import { useMemo } from 'react'
import {
  ActivitySquare,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ChevronRight,
  Clock,
  Cpu,
  FileSearch,
  GitBranch,
  Info,
  SkipForward,
  X,
} from 'lucide-react'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import EmptyState from '@/components/ui/EmptyState'
import { useDecisionTrail } from '@/hooks/useDecisionTrail'
import type {
  DecisionLogEntry,
  PerTestRouting,
  StageDecisionSummary,
  WorkflowDecisionEvent,
} from '@/types/decisionTrail'

interface Props {
  runId: string | null
  open: boolean
  onClose: () => void
}

/**
 * AI decision trail drawer — the user-facing "why did the AI do that" audit
 * surface introduced in Tier 0B. Renders the full trail in a single scroll:
 *
 * 1. Top summary: pipeline status, cost, token count, mode distribution
 * 2. Timeline: stages in execution order, each with its decision_log expanded
 * 3. Per-test routing table at the bottom for QA leads who want the detail
 *
 * The drawer is lazy — the ``useDecisionTrail`` hook only fires when ``open``
 * flips to true, so closed runs don't incur the round trip.
 */
export default function DecisionTrailDrawer({ runId, open, onClose }: Props) {
  const { data, error, isLoading } = useDecisionTrail(runId, open)

  if (!open) return null

  return (
    <div
      role="dialog"
      aria-label="AI decision trail"
      className="fixed inset-y-0 right-0 w-[540px] max-w-[95vw] bg-[var(--color-bg-card)] border-l border-[var(--color-border)] shadow-2xl z-50 flex flex-col"
    >
      <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border)]">
        <div className="flex items-center gap-2">
          <FileSearch className="h-4 w-4 text-[var(--color-accent)]" />
          <h3 className="text-sm font-semibold text-[var(--color-text)]">
            AI Decision Trail
          </h3>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="p-1 rounded hover:bg-[var(--color-bg-secondary)]"
          aria-label="Close decision trail"
        >
          <X className="h-4 w-4 text-[var(--color-text-muted)]" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {isLoading && (
          <div className="flex items-center justify-center py-16">
            <LoadingSpinner size="lg" />
          </div>
        )}
        {error && !isLoading && (
          <EmptyState
            title="Could not load decision trail"
            description={String((error as Error).message || error)}
          />
        )}
        {!isLoading && !error && data && (
          <div className="p-4 space-y-5">
            <TrailHeader data={data} />
            <WorkflowEventsSection events={data.workflow_events} />
            <StageTimelineSection stages={data.stages} />
            <PerTestSection perTest={data.per_test} fallbackCount={data.fallback_count} />
          </div>
        )}
      </div>
    </div>
  )
}

// ── Header / summary ────────────────────────────────────────────────────────

function TrailHeader({
  data,
}: {
  data: {
    pipeline_status: string | null
    workflow_type: string | null
    total_cost_usd: number
    total_tokens: number
    mode_distribution: Record<string, number>
    fallback_count: number
  }
}) {
  const modes = Object.entries(data.mode_distribution)
  return (
    <section className="space-y-3">
      <div className="flex items-center gap-2">
        <StatusPill status={data.pipeline_status} />
        {data.workflow_type && (
          <span className="text-xs text-[var(--color-text-muted)] uppercase tracking-wide">
            {data.workflow_type}
          </span>
        )}
      </div>
      <div className="grid grid-cols-3 gap-2">
        <MiniStat label="Cost" value={`$${data.total_cost_usd.toFixed(4)}`} />
        <MiniStat label="Tokens" value={data.total_tokens.toLocaleString()} />
        <MiniStat
          label="Fallbacks"
          value={data.fallback_count.toString()}
          tone={data.fallback_count > 0 ? 'warn' : 'neutral'}
        />
      </div>
      {modes.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {modes.map(([mode, count]) => (
            <span
              key={mode}
              className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)]"
              title={`${count} tests routed via ${mode}`}
            >
              <Cpu className="h-3 w-3 text-[var(--color-text-muted)]" />
              {mode}
              <span className="text-[var(--color-text-faint)]">×{count}</span>
            </span>
          ))}
        </div>
      )}
    </section>
  )
}

function MiniStat({
  label,
  value,
  tone = 'neutral',
}: {
  label: string
  value: string
  tone?: 'neutral' | 'warn'
}) {
  return (
    <div
      className={`rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-2 ${tone === 'warn' ? 'border-amber-500/40' : ''}`}
    >
      <div className="text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">
        {label}
      </div>
      <div className="text-sm font-semibold text-[var(--color-text)]">{value}</div>
    </div>
  )
}

function StatusPill({ status }: { status: string | null }) {
  if (!status) return null
  const tone =
    status === 'completed'
      ? 'text-emerald-400 border-emerald-500/40'
      : status === 'failed'
      ? 'text-rose-400 border-rose-500/40'
      : status === 'partial'
      ? 'text-amber-400 border-amber-500/40'
      : 'text-[var(--color-text-muted)] border-[var(--color-border)]'
  return (
    <span
      className={`text-xs px-2 py-0.5 rounded border bg-[var(--color-bg-secondary)] ${tone}`}
    >
      {status}
    </span>
  )
}

// ── Workflow events (fast-path skips etc.) ──────────────────────────────────

function WorkflowEventsSection({ events }: { events: WorkflowDecisionEvent[] }) {
  if (!events.length) return null
  return (
    <section className="space-y-2">
      <SectionHeader icon={<GitBranch className="h-3.5 w-3.5" />} label="Workflow routing" />
      <ul className="space-y-2">
        {events.map((ev, i) => (
          <li
            key={`${ev.decision_point}-${i}`}
            className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-2 text-xs space-y-0.5"
          >
            <div className="flex items-center gap-1.5 text-[var(--color-text)]">
              <span className="font-mono font-semibold">{ev.decision_point}</span>
              <ArrowRight className="h-3 w-3 text-[var(--color-text-muted)]" />
              <span className="font-semibold text-[var(--color-accent)]">{ev.chosen}</span>
            </div>
            <p className="text-[var(--color-text-muted)]">{ev.rationale}</p>
            {ev.alternatives && ev.alternatives.length > 0 && (
              <p className="text-[var(--color-text-faint)]">
                Rejected: {ev.alternatives.join(', ')}
              </p>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}

// ── Stage timeline ──────────────────────────────────────────────────────────

function StageTimelineSection({ stages }: { stages: StageDecisionSummary[] }) {
  if (!stages.length) {
    return (
      <section className="space-y-2">
        <SectionHeader icon={<ActivitySquare className="h-3.5 w-3.5" />} label="Pipeline stages" />
        <p className="text-xs text-[var(--color-text-muted)]">
          No pipeline has been executed for this run yet.
        </p>
      </section>
    )
  }
  return (
    <section className="space-y-2">
      <SectionHeader icon={<ActivitySquare className="h-3.5 w-3.5" />} label="Pipeline stages" />
      <ol className="space-y-3">
        {stages.map((stage) => (
          <StageCard key={stage.stage_name} stage={stage} />
        ))}
      </ol>
    </section>
  )
}

function StageCard({ stage }: { stage: StageDecisionSummary }) {
  const toneClass = useMemo(() => {
    switch (stage.status) {
      case 'completed':
        return 'border-emerald-500/30'
      case 'failed':
        return 'border-rose-500/40'
      case 'skipped':
        return 'border-[var(--color-border)] opacity-80'
      default:
        return 'border-[var(--color-border)]'
    }
  }, [stage.status])

  return (
    <li
      className={`rounded-md border ${toneClass} bg-[var(--color-bg-secondary)] px-3 py-2.5 space-y-1.5`}
    >
      <div className="flex items-center gap-2">
        <StageIcon status={stage.status} />
        <span className="text-sm font-semibold text-[var(--color-text)]">
          {stage.stage_name}
        </span>
        {stage.duration_seconds != null && (
          <span className="text-xs text-[var(--color-text-faint)] ml-auto flex items-center gap-1">
            <Clock className="h-3 w-3" />
            {stage.duration_seconds.toFixed(2)}s
          </span>
        )}
      </div>

      {/* Subheader — mode, fallback, cost */}
      <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-[var(--color-text-muted)]">
        {stage.analysis_mode && <BadgePill label={`mode: ${stage.analysis_mode}`} />}
        {stage.fallback_used && (
          <BadgePill
            label={`fallback: ${stage.fallback_reason || 'unspecified'}`}
            tone="warn"
          />
        )}
        {stage.error_category && (
          <BadgePill label={`error: ${stage.error_category}`} tone="error" />
        )}
        {stage.execution_path && <BadgePill label={stage.execution_path} />}
        {stage.cost_usd != null && stage.cost_usd > 0 && (
          <BadgePill label={`$${stage.cost_usd.toFixed(4)}`} />
        )}
        {stage.confidence_score != null && (
          <BadgePill label={`conf: ${stage.confidence_score}`} />
        )}
      </div>

      {stage.skipped_reason && (
        <p className="text-xs text-[var(--color-text-muted)] italic">
          Skipped: {stage.skipped_reason}
        </p>
      )}
      {stage.route_rationale && (
        <p className="text-xs text-[var(--color-text-muted)]">{stage.route_rationale}</p>
      )}

      {/* Decision log */}
      {stage.decision_log.length > 0 && (
        <details className="mt-1" open={stage.decision_log.length <= 3}>
          <summary className="text-xs text-[var(--color-accent)] cursor-pointer hover:underline">
            {stage.decision_log.length} decision
            {stage.decision_log.length === 1 ? '' : 's'}
          </summary>
          <ul className="mt-2 space-y-1.5">
            {stage.decision_log.map((entry, i) => (
              <DecisionLogRow key={i} entry={entry} />
            ))}
          </ul>
        </details>
      )}
    </li>
  )
}

function StageIcon({ status }: { status: string }) {
  if (status === 'completed') return <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />
  if (status === 'failed') return <AlertTriangle className="h-3.5 w-3.5 text-rose-400" />
  if (status === 'skipped') return <SkipForward className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
  return <Info className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
}

function BadgePill({
  label,
  tone = 'neutral',
}: {
  label: string
  tone?: 'neutral' | 'warn' | 'error'
}) {
  const toneClass =
    tone === 'warn'
      ? 'border-amber-500/40 text-amber-300'
      : tone === 'error'
      ? 'border-rose-500/40 text-rose-300'
      : 'border-[var(--color-border)] text-[var(--color-text-muted)]'
  return (
    <span
      className={`inline-flex items-center text-[10px] px-1.5 py-0.5 rounded border bg-[var(--color-bg-card)] ${toneClass}`}
    >
      {label}
    </span>
  )
}

function DecisionLogRow({ entry }: { entry: DecisionLogEntry }) {
  return (
    <li className="text-[11px] leading-relaxed border-l-2 border-[var(--color-accent)]/50 pl-2">
      <div className="flex items-center gap-1 text-[var(--color-text)]">
        <span className="font-mono font-semibold">{entry.decision_point}</span>
        <ChevronRight className="h-3 w-3 text-[var(--color-text-muted)]" />
        <span className="font-semibold text-[var(--color-accent)]">{entry.chosen}</span>
        {entry.test_case_id && (
          <span className="ml-auto text-[10px] font-mono text-[var(--color-text-faint)]">
            {entry.test_case_id.slice(0, 8)}
          </span>
        )}
      </div>
      <p className="text-[var(--color-text-muted)]">{entry.rationale}</p>
      {entry.alternatives && entry.alternatives.length > 0 && (
        <p className="text-[var(--color-text-faint)]">
          Rejected: {entry.alternatives.join(', ')}
        </p>
      )}
    </li>
  )
}

// ── Per-test routing rollup ────────────────────────────────────────────────

function PerTestSection({
  perTest,
  fallbackCount,
}: {
  perTest: PerTestRouting[]
  fallbackCount: number
}) {
  if (!perTest.length) return null

  // Sort: fallbacks first, then by duration descending.
  const sorted = [...perTest].sort((a, b) => {
    if (a.fallback_from && !b.fallback_from) return -1
    if (!a.fallback_from && b.fallback_from) return 1
    return (b.duration_seconds ?? 0) - (a.duration_seconds ?? 0)
  })

  return (
    <section className="space-y-2">
      <SectionHeader
        icon={<Cpu className="h-3.5 w-3.5" />}
        label={`Per-test routing (${perTest.length})`}
      />
      {fallbackCount > 0 && (
        <p className="text-[11px] text-amber-400/90">
          {fallbackCount} test{fallbackCount === 1 ? '' : 's'} fell back from the requested engine —
          shown first below.
        </p>
      )}
      <div className="overflow-hidden rounded-md border border-[var(--color-border)]">
        <table className="w-full text-[11px]">
          <thead className="bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)]">
            <tr>
              <th className="text-left px-2 py-1.5">Test</th>
              <th className="text-left px-2 py-1.5">Engine</th>
              <th className="text-left px-2 py-1.5">Fallback</th>
              <th className="text-right px-2 py-1.5">Duration</th>
            </tr>
          </thead>
          <tbody>
            {sorted.slice(0, 100).map((row) => (
              <tr
                key={row.test_case_id}
                className="border-t border-[var(--color-border)]"
              >
                <td className="px-2 py-1.5 text-[var(--color-text)] truncate max-w-[180px]">
                  {row.test_name || row.test_case_id.slice(0, 8)}
                </td>
                <td className="px-2 py-1.5 text-[var(--color-text-muted)]">
                  {row.analysis_mode || '—'}
                </td>
                <td className="px-2 py-1.5 text-[var(--color-text-muted)]">
                  {row.fallback_from ? (
                    <span className="text-amber-400">
                      {row.fallback_from} → {row.analysis_mode}
                    </span>
                  ) : (
                    '—'
                  )}
                </td>
                <td className="px-2 py-1.5 text-right text-[var(--color-text-faint)]">
                  {row.duration_seconds != null ? `${row.duration_seconds.toFixed(2)}s` : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sorted.length > 100 && (
        <p className="text-[10px] text-[var(--color-text-faint)] text-right">
          Showing 100 of {sorted.length} tests.
        </p>
      )}
    </section>
  )
}

// ── Primitives ──────────────────────────────────────────────────────────────

function SectionHeader({ icon, label }: { icon: React.ReactNode; label: string }) {
  return (
    <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">
      {icon}
      {label}
    </div>
  )
}
