import { clsx } from 'clsx'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useTestCaseHistory } from '@/hooks/useRuns'
import { formatDuration, formatRunWhen } from '@/utils/formatters'
import type { TestCaseHistoryPoint } from '@/types/runs'

const PASSED_STATUSES = ['PASSED']
const FAILED_STATUSES = ['FAILED', 'BROKEN']

/** Single colored dot for one run in the pass/fail timeline. */
function HistoryDot({ point }: { point: TestCaseHistoryPoint }) {
  const status = point.status?.toUpperCase()
  const color = PASSED_STATUSES.includes(status)
    ? 'bg-[var(--status-passed)]'
    : FAILED_STATUSES.includes(status)
      ? 'bg-[var(--status-failed)]'
      : 'bg-[var(--status-skipped)]'
  const when = formatRunWhen(point.created_at)
  const title = [
    point.run_label,
    point.status,
    formatDuration(point.duration_ms),
    when,
  ]
    .filter(Boolean)
    .join(' · ')
  return (
    <span
      className={clsx('inline-block h-3 w-3 shrink-0 rounded-full', color)}
      title={title}
      aria-label={title}
    />
  )
}

const CLASSIFICATION_BADGE: Record<string, string> = {
  QUARANTINE: 'badge-failed',
  INVESTIGATE: 'badge-broken',
  MONITOR: 'badge-flaky',
  HEALTHY: 'badge-passed',
}

function MetaRow({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="shrink-0 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
        {label}
      </span>
      <span className="truncate text-right text-xs text-[var(--color-text-secondary)]">
        {value}
      </span>
    </div>
  )
}

export default function TestHistoryPanel({ runId, testId }: { runId?: string; testId?: string }) {
  const { data, isLoading } = useTestCaseHistory(runId, testId)

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <LoadingSpinner size="md" />
      </div>
    )
  }

  // `!data` alone was not enough. The body below dereferences `flakiness` and
  // `metadata` unguarded -- `flakiness.classification?.` guards the property
  // but not the object -- so a response that merely LACKS those sections threw
  // "Cannot read properties of undefined (reading 'classification')" and took
  // the whole test-case page into its error boundary ("Something went wrong
  // loading this page"). A panel with no history to show should render its
  // empty state, not destroy the page around it.
  if (!data || !data.flakiness || !data.metadata) {
    return (
      <div className="card py-8 text-center text-sm text-[var(--color-text-muted)]">
        No cross-run history available for this test.
      </div>
    )
  }

  const { history, flakiness, metadata } = data
  const classBadge = CLASSIFICATION_BADGE[flakiness.classification?.toUpperCase()] ?? 'badge'

  const firstSeen = metadata.first_seen_run_label
    ? `${metadata.first_seen_run_label}${
        formatRunWhen(metadata.first_seen_at) ? ` · ${formatRunWhen(metadata.first_seen_at)}` : ''
      }`
    : formatRunWhen(metadata.first_seen_at) || null
  const lastSeen = metadata.last_seen_run_label
    ? `${metadata.last_seen_run_label}${
        formatRunWhen(metadata.last_seen_at) ? ` · ${formatRunWhen(metadata.last_seen_at)}` : ''
      }`
    : formatRunWhen(metadata.last_seen_at) || null

  return (
    <div className="card space-y-4 p-3">
      {/* Flakiness + counts header */}
      <div className="flex flex-wrap items-center gap-2">
        {flakiness.is_flaky && (
          <>
            <span className={classBadge}>{flakiness.classification}</span>
            <span className="badge-flaky">flaky · {flakiness.failure_rate_pct.toFixed(1)}% fail</span>
            <span className="badge bg-[var(--color-bg-secondary)] border border-[var(--color-border)]">
              impact {flakiness.impact_score.toFixed(1)}
            </span>
          </>
        )}
        <span className="ml-auto text-xs text-[var(--color-text-muted)]">
          <span className="text-[var(--status-passed)]">{flakiness.passed} passed</span>
          {' · '}
          <span className="text-[var(--status-failed)]">{flakiness.failed} failed</span>
          {' · '}
          {flakiness.total_runs} runs ({flakiness.window_days}d)
        </span>
      </div>

      {/* Pass/fail timeline (most-recent-first → render reversed so oldest is left) */}
      {history.length > 0 ? (
        <div className="space-y-1.5">
          <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
            Recent runs (oldest → newest)
          </p>
          <div className="flex flex-wrap items-center gap-1.5">
            {[...history].reverse().map((point, i) => (
              <HistoryDot key={point.run_id ?? `h-${i}`} point={point} />
            ))}
          </div>
        </div>
      ) : (
        <p className="text-xs text-[var(--color-text-muted)]">No prior runs for this test.</p>
      )}

      {/* Metadata block */}
      <div className="border-t border-[var(--color-border)] pt-2">
        <MetaRow label="Suite" value={metadata.suite} />
        <MetaRow label="Owner" value={metadata.owner} />
        <MetaRow label="Severity" value={metadata.severity} />
        <MetaRow label="Feature" value={metadata.feature} />
        <MetaRow label="First seen" value={firstSeen} />
        <MetaRow label="Last seen" value={lastSeen} />
        <MetaRow label="Created" value={formatRunWhen(metadata.created_at) || null} />
        <MetaRow label="Updated" value={formatRunWhen(metadata.updated_at) || null} />
      </div>
    </div>
  )
}
