import { ArrowDown, ArrowUp, CheckCircle, AlertTriangle, TrendingDown, TrendingUp } from 'lucide-react'
import clsx from 'clsx'
import type { ExecutivePanel } from '@/services/runIntelligenceService'

const STATUS_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  GO: { bg: 'bg-[var(--status-passed-bg)] border-[var(--status-passed-bd)]', text: 'text-[var(--gate-go)]', label: 'GO' },
  CONDITIONAL_GO: { bg: 'bg-[var(--gate-conditional-bg)] border-[var(--gate-conditional-border)]', text: 'text-[var(--gate-conditional)]', label: 'CONDITIONAL GO' },
  NO_GO: { bg: 'bg-[var(--status-failed-bg)] border-[var(--status-failed-bd)]', text: 'text-[var(--gate-no-go)]', label: 'NO GO' },
}

const CATEGORY_LABELS: Record<string, string> = {
  PRODUCT_BUG: 'Product Bug',
  INFRASTRUCTURE: 'Infrastructure',
  TEST_DATA: 'Test Data',
  AUTOMATION_DEFECT: 'Automation Defect',
  FLAKY: 'Flaky',
  UNKNOWN: 'Unknown',
}

const CATEGORY_COLOUR: Record<string, string> = {
  PRODUCT_BUG: 'text-[var(--status-failed)]',
  INFRASTRUCTURE: 'text-[var(--status-broken)]',
  TEST_DATA: 'text-[var(--color-cyan)]',
  AUTOMATION_DEFECT: 'text-[var(--color-purple)]',
  FLAKY: 'text-[var(--status-flaky)]',
  UNKNOWN: 'text-[var(--color-text-muted)]',
}

function passRateColour(rate: number): string {
  if (rate >= 90) return 'text-[var(--status-passed)]'
  if (rate >= 70) return 'text-[var(--status-broken)]'
  return 'text-[var(--status-failed)]'
}

interface Props {
  panel: ExecutivePanel
  compact?: boolean
}

export default function ExecutiveSummaryPanel({ panel, compact = false }: Props) {
  const status = STATUS_STYLES[panel.status_signal] ?? STATUS_STYLES.CONDITIONAL_GO
  const { metrics } = panel

  return (
    <div className="card border border-[var(--color-border)] rounded-xl space-y-4">
      {/* Header: Headline + Status Signal + Risk Score */}
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="flex-1 min-w-0">
          <h3 className="text-base font-semibold text-[var(--color-text)] leading-tight">{panel.headline}</h3>
          {metrics.branch && (
            <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
              Branch: <span className="font-mono">{metrics.branch}</span>
            </p>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span
            role="status"
            aria-label={`Release readiness: ${status.label}`}
            className={clsx('px-2.5 py-1 rounded-md text-xs font-bold border', status.bg, status.text)}
            title="Release readiness assessment"
          >
            {status.label}
          </span>
          {panel.risk_score != null && (
            <span
              className="text-xs text-[var(--color-text-muted)] tabular-nums"
              title="Composite risk score from 0 (safe) to 100 (critical)"
            >
              Risk {panel.risk_score}/100
            </span>
          )}
        </div>
      </div>

      {/* Metric Strip */}
      <dl className="grid grid-cols-3 sm:grid-cols-6 gap-2">
        <MetricCell
          label="Pass Rate"
          value={`${metrics.pass_rate.toFixed(1)}%`}
          valueClass={passRateColour(metrics.pass_rate)}
          title="Percentage of tests that passed"
        />
        <MetricCell
          label="Total Tests"
          value={String(metrics.total_tests)}
          title="Total number of tests in this run"
        />
        <MetricCell
          label="Failed"
          value={String(metrics.failed)}
          valueClass={metrics.failed === 0 ? 'text-[var(--status-passed)]' : 'text-[var(--status-failed)]'}
          title="Number of tests that failed"
        />
        <MetricCell
          label="Skipped"
          value={String(metrics.skipped)}
          title="Number of tests that were skipped"
        />
        <MetricCell
          label="Failure Groups"
          value={String(metrics.failure_clusters)}
          title="Groups of related failures sharing a common root cause"
        />
        <MetricCell
          label="Anomalies"
          value={String(metrics.anomaly_count)}
          title="Log patterns or test behaviors flagged as statistically unusual"
        />
      </dl>

      {/* Dominant Failure */}
      {panel.dominant_failure && (
        <div className="flex items-center gap-3 bg-[var(--color-bg-secondary)]/60 border border-[var(--color-border)] rounded-lg px-3 py-2">
          <AlertTriangle className={clsx('h-4 w-4 shrink-0', CATEGORY_COLOUR[panel.dominant_failure.category] ?? 'text-[var(--color-text-muted)]')} />
          <div className="flex-1 min-w-0">
            <span className="text-sm text-[var(--color-text)]">
              <span className={clsx('font-medium', CATEGORY_COLOUR[panel.dominant_failure.category])}>
                {CATEGORY_LABELS[panel.dominant_failure.category] ?? panel.dominant_failure.category}
              </span>
              {' '}&mdash; {panel.dominant_failure.count} failure{panel.dominant_failure.count !== 1 ? 's' : ''} ({panel.dominant_failure.percentage}%)
            </span>
          </div>
          <div className="h-1.5 w-20 bg-[var(--color-bg-secondary)] rounded-full overflow-hidden shrink-0">
            <div
              className={clsx('h-full rounded-full', panel.dominant_failure.percentage >= 70 ? 'bg-[var(--status-failed)]' : 'bg-[var(--status-broken)]')}
              style={{ width: `${Math.min(panel.dominant_failure.percentage, 100)}%` }}
            />
          </div>
        </div>
      )}

      {/* Key Takeaways */}
      {panel.key_takeaways.length > 0 && (
        <div>
          <p className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-1.5">Key Takeaways</p>
          <ul className="space-y-1">
            {panel.key_takeaways.map((item, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-[var(--color-text-secondary)]">
                <CheckCircle className="h-3.5 w-3.5 text-[var(--color-text-faint)] flex-shrink-0 mt-0.5" />
                {item}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Baseline Comparison (hidden in compact mode) */}
      {!compact && panel.baseline_comparison && (
        <div className="bg-[var(--color-bg-secondary)]/40 border border-[var(--color-border)]/40 rounded-lg px-3 py-2.5">
          <p className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-2">Baseline Comparison</p>
          <div className="flex flex-wrap gap-4 text-sm">
            <div className="flex items-center gap-1.5" title="Change in pass rate compared to last good run">
              {panel.baseline_comparison.pass_rate_delta >= 0
                ? <TrendingUp className="h-3.5 w-3.5 text-[var(--status-passed)]" />
                : <TrendingDown className="h-3.5 w-3.5 text-[var(--status-failed)]" />}
              <span className={panel.baseline_comparison.pass_rate_delta >= 0 ? 'text-[var(--status-passed)]' : 'text-[var(--status-failed)]'}>
                {panel.baseline_comparison.pass_rate_delta > 0 ? '+' : ''}{panel.baseline_comparison.pass_rate_delta.toFixed(1)}%
              </span>
              <span className="text-[var(--color-text-muted)]">pass rate</span>
            </div>
            {panel.baseline_comparison.new_failures > 0 && (
              <div className="flex items-center gap-1.5" title="Tests that were passing in baseline but failing now">
                <ArrowUp className="h-3.5 w-3.5 text-[var(--status-failed)]" />
                <span className="text-[var(--status-failed)]">{panel.baseline_comparison.new_failures}</span>
                <span className="text-[var(--color-text-muted)]">new failures</span>
              </div>
            )}
            {panel.baseline_comparison.resolved > 0 && (
              <div className="flex items-center gap-1.5" title="Tests that were failing in baseline but passing now">
                <ArrowDown className="h-3.5 w-3.5 text-[var(--status-passed)]" />
                <span className="text-[var(--status-passed)]">{panel.baseline_comparison.resolved}</span>
                <span className="text-[var(--color-text-muted)]">resolved</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Next Actions (hidden in compact mode) */}
      {!compact && panel.next_actions.length > 0 && (
        <div>
          <p className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-1.5">Next Actions</p>
          <ol className="space-y-1 list-none">
            {panel.next_actions.map((action, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-[var(--color-text-secondary)]">
                <span className="shrink-0 w-5 h-5 rounded-full bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] text-xs flex items-center justify-center font-medium mt-px">
                  {i + 1}
                </span>
                {action}
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  )
}

function MetricCell({ label, value, valueClass, title }: {
  label: string
  value: string
  valueClass?: string
  title: string
}) {
  return (
    <div className="text-center bg-[var(--color-bg-secondary)]/40 rounded-lg px-2 py-2" title={title}>
      <dd className={clsx('text-lg font-bold tabular-nums', valueClass ?? 'text-[var(--color-text)]')}>{value}</dd>
      <dt className="text-[10px] text-[var(--color-text-muted)] uppercase tracking-wider mt-0.5">{label}</dt>
    </div>
  )
}
