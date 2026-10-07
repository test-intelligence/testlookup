/**
 * The Run page's **Changes** tab (UX redesign P4): what changed since the
 * last good run — the regression diff that used to sit, collapsed, above the
 * test table ("What changed since last good run?") — and the way into
 * Compare (`/runs/compare`) for a side-by-side of two runs.
 *
 * Mounted only while the tab is open, so the diff is asked for then and not
 * at page load (the same rule the collapsed card had).
 */
import { Link } from 'react-router-dom'
import { GitCommit, GitCompare } from 'lucide-react'
import useSWR from 'swr'
import { clsx } from 'clsx'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { api } from '@/services/api'
import type { TestRun } from '@/types/runs'
import { buildCompareWithPreviousHref } from '@/utils/runComparisons'

interface RegressionDiff {
  baseline_available: boolean
  baseline_run_id?: string
  baseline_build_number?: string
  pass_rate?: number
  baseline_pass_rate?: number
  pass_rate_delta?: number
  new_failing_tests?: Array<{ test_name: string; suite_name: string | null }>
  new_failing_count?: number
  resolved_count?: number
  commit_range?: Array<{ sha: string; message: string; author: string; timestamp: string }>
}

const LINK = 'inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border)] px-2.5 py-1.5 text-[13px] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text)]'

export default function RunChangesTab({
  runId,
  run,
  onCompareWithPrevious,
}: {
  runId: string
  run: TestRun | undefined
  /** Opens Compare against the previous run of the same suite (the header's action). */
  onCompareWithPrevious: () => void
}) {
  const { data, isLoading, error } = useSWR<RegressionDiff>(
    `regression-diff-${runId}`,
    () => api.get(`/api/v1/runs/${runId}/regression-diff`).then(r => r.data),
    { revalidateOnFocus: false },
  )

  // The baseline the diff measured against, on the left of Compare.
  const baselineHref = data?.baseline_available && data.baseline_run_id
    ? buildCompareWithPreviousHref(
        { id: runId, primary_suite_name: run?.primary_suite_name ?? null },
        { id: data.baseline_run_id, primary_suite_name: run?.primary_suite_name ?? null },
      )
    : null

  return (
    <div className="space-y-4">
      <section data-primary="" aria-label="What changed since the last good run" className="card">
        <h2 className="m-0 text-sm font-semibold text-[var(--color-text)]">What changed since the last good run</h2>
        {isLoading && <div className="flex justify-center py-8"><LoadingSpinner /></div>}
        {!isLoading && error && (
          <p className="mt-3 text-sm text-[var(--status-failed)]">Could not load the regression diff for this run.</p>
        )}
        {!isLoading && !error && data && !data.baseline_available && (
          <p className="mt-3 text-sm text-[var(--color-text-muted)]">No recent passing baseline run found for comparison.</p>
        )}
        {!isLoading && !error && data && data.baseline_available && (
          <div className="mt-4 space-y-4">
            <div className="grid grid-cols-3 gap-3 text-center">
              <div className="bg-[var(--color-bg-secondary)]/80 rounded-lg p-2">
                <p className={clsx('text-xl font-bold tabular-nums', (data.pass_rate_delta ?? 0) >= 0 ? 'text-[var(--status-passed)]' : 'text-[var(--status-failed)]')}>
                  {(data.pass_rate_delta ?? 0) >= 0 ? '+' : ''}{data.pass_rate_delta?.toFixed(1)}%
                </p>
                <p className="text-xs text-[var(--color-text-muted)] mt-0.5">Pass rate delta</p>
              </div>
              <div className="bg-[var(--color-bg-secondary)]/80 rounded-lg p-2">
                <p className="text-xl font-bold text-[var(--status-failed)]">{data.new_failing_count ?? 0}</p>
                <p className="text-xs text-[var(--color-text-muted)] mt-0.5">New failures</p>
              </div>
              <div className="bg-[var(--color-bg-secondary)]/80 rounded-lg p-2">
                <p className="text-xl font-bold text-[var(--status-passed)]">{data.resolved_count ?? 0}</p>
                <p className="text-xs text-[var(--color-text-muted)] mt-0.5">Resolved</p>
              </div>
            </div>

            <p className="text-xs text-[var(--color-text-muted)]">Baseline: Build #{data.baseline_build_number}</p>

            {(data.new_failing_tests?.length ?? 0) > 0 && (
              <div>
                <p className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-1.5">New failures</p>
                <ul className="space-y-1">
                  {data.new_failing_tests?.slice(0, 10).map((t, i) => (
                    <li key={i} className="text-sm text-[var(--color-text-secondary)] flex items-center gap-2">
                      <div className="h-1.5 w-1.5 rounded-full bg-[var(--status-failed-bg)] flex-shrink-0" />
                      {t.test_name}
                      {t.suite_name && <span className="text-[var(--color-text-muted)] text-xs">· {t.suite_name}</span>}
                    </li>
                  ))}
                  {(data.new_failing_count ?? 0) > 10 && (
                    <li className="text-xs text-[var(--color-text-muted)]">…and {(data.new_failing_count ?? 0) - 10} more</li>
                  )}
                </ul>
              </div>
            )}

            {(data.commit_range?.length ?? 0) > 0 && (
              <div>
                <p className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-1.5">
                  Commits since baseline ({data.commit_range?.length})
                </p>
                <ul className="space-y-1">
                  {data.commit_range?.slice(0, 5).map((c) => (
                    <li key={c.sha} className="flex items-start gap-2 text-xs text-[var(--color-text-muted)]">
                      <GitCommit className="h-3.5 w-3.5 text-[var(--color-text-faint)] flex-shrink-0 mt-0.5" />
                      <span className="font-mono text-[var(--color-text)] mr-1">{c.sha}</span>
                      <span>{c.message}</span>
                      <span className="text-[var(--color-text-faint)]">— {c.author}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </section>

      <section aria-label="Compare" className="flex flex-wrap items-center gap-2">
        {baselineHref && (
          <Link to={baselineHref} className={LINK}>
            <GitCompare className="h-3.5 w-3.5" />
            Compare with build #{data?.baseline_build_number} (last good)
          </Link>
        )}
        <button
          type="button"
          onClick={onCompareWithPrevious}
          disabled={!run?.primary_suite_name}
          title={
            run?.primary_suite_name
              ? `Compare this run to the previous run of "${run.primary_suite_name}"`
              : 'No suite attribution on this run — cannot pick a previous-of-same-suite'
          }
          className={clsx(LINK, 'disabled:opacity-50')}
        >
          <GitCompare className="h-3.5 w-3.5" />
          Compare with the previous run
        </button>
        <Link to={`/runs/compare?left=${encodeURIComponent(runId)}`} className={LINK}>
          Open Compare
        </Link>
      </section>
    </div>
  )
}
