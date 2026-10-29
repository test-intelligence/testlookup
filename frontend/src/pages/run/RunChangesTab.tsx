/**
 * The Run page's **Changes** tab (UX redesign P4): what changed since the
 * last good run — the regression diff that used to sit, collapsed, above the
 * test table ("What changed since last good run?") — and the way into
 * Compare (`/runs/compare`) for a side-by-side of two runs.
 *
 * Mounted only while the tab is open, so the diff is asked for then and not
 * at page load (the same rule the collapsed card had). The same holds for the
 * duration regressions under it (EXEC-05, `DurationRegressionsCard`).
 *
 * W10 FAIL-09: the commits are the project's GitHub / GHE range (the Suspects
 * panel's); when there are none the tab says why (`commit_range_status`). A
 * server without that status renders exactly the markup from before.
 *
 * W22a ACT-04: when the retention purge removed the run's stored baseline,
 * one muted line says `Baseline build N was purged on <date>.` — under the
 * baseline line when a baseline was found, else under "No recent passing …".
 *
 * W23b FAIL-12: each new failure with a `test_fingerprint` gets `Narrow`; the
 * open one (`?bisect=<fingerprint>`; `?bisect=top` = the first new failure,
 * rewritten once the diff arrives) renders the bisect narrowing under its row.
 * No `/bisect` read happens until one is opened. FAIL-13: a separate
 * "Configuration drift" card under the main one, from the same answer.
 */
import { useEffect, useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { GitCommit, GitCompare } from 'lucide-react'
import { clsx } from 'clsx'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import ScopedLink from '@/components/ui/ScopedLink'
import DurationRegressionsCard from '@/components/runs/DurationRegressionsCard'
import ConfigDriftSection from '@/components/runs/ConfigDriftSection'
import BisectNarrowing from '@/components/bisect/BisectNarrowing'
import { inlineHeading } from '@/components/bisect/bisectModel'
import { INSIGHT_LINK_CLASS } from '@/components/insights/InsightsList'
import { useRegressionDiff } from '@/hooks/useRegressionDiff'
import { usePermissions } from '@/hooks/usePermissions'
import { parseConfigDrift } from '@/types/configDrift'
import type { CommitRangeStatus, RegressionDiffBaselinePurged } from '@/types/regressionDiff'
import { baselinePurgedLine } from '@/components/retention/holdsModel'
import { formatDay } from '@/utils/formatters'
import type { TestRun } from '@/types/runs'
import { buildCompareWithPreviousHref } from '@/utils/runComparisons'
import {
  SUPPLIED_LINE,
  WEAK_ANCHOR_LINE,
  commitsHeading,
  reasonLine,
  reasonLink,
  safeCommitHref,
} from './commitRangeReasons'

const LINK = 'inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border)] px-2.5 py-1.5 text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text)]'

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
  const { data, isLoading, error } = useRegressionDiff(runId)
  const { isAdmin } = usePermissions()
  const status = data?.commit_range_status
  const commits = data?.commit_range ?? []

  // W23b FAIL-13: the drift card renders only when the answer's `config_drift` parses.
  const drift = useMemo(
    () => (data?.baseline_available ? parseConfigDrift(data.config_drift) : null),
    [data],
  )

  // W23b FAIL-12: the open narrowing lives in the URL (`?bisect=<fingerprint>`, `tab` kept).
  // `?bisect=top` (the Runs CTA) resolves to the first new failure once the diff arrives.
  const [searchParams, setSearchParams] = useSearchParams()
  const bisectParam = searchParams.get('bisect')
  const openFingerprint = bisectParam && bisectParam !== 'top' ? bisectParam : null
  const topFingerprint = data?.new_failing_tests?.[0]?.test_fingerprint ?? null
  useEffect(() => {
    if (bisectParam !== 'top' || !data) return
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      if (topFingerprint) next.set('bisect', topFingerprint)
      else next.delete('bisect')
      return next
    }, { replace: true })
  }, [bisectParam, data, topFingerprint, setSearchParams])
  const toggleNarrow = (fingerprint: string) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      if (next.get('bisect') === fingerprint) next.delete('bisect')
      else next.set('bisect', fingerprint)
      return next
    }, { replace: true })
  }

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
          <>
            <p className="mt-3 text-sm text-[var(--color-text-muted)]">No recent passing baseline run found for comparison.</p>
            {data.baseline_purged && <BaselinePurgedLine purged={data.baseline_purged} className="mt-1" />}
          </>
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

            <div>
              <p className="text-xs text-[var(--color-text-muted)]">Baseline: Build #{data.baseline_build_number}</p>
              {data.baseline_purged && <BaselinePurgedLine purged={data.baseline_purged} className="mt-0.5" />}
            </div>

            {(data.new_failing_tests?.length ?? 0) > 0 && (
              <div>
                <p className="eyebrow uppercase text-[var(--color-text-muted)] mb-1.5">New failures</p>
                <ul className="space-y-1">
                  {data.new_failing_tests?.slice(0, 10).map((t, i) => t.test_fingerprint ? (
                    <NewFailureNarrowRow
                      key={i}
                      runId={runId}
                      runBuild={run?.build_number != null ? String(run.build_number) : null}
                      testName={t.test_name}
                      suiteName={t.suite_name}
                      fingerprint={t.test_fingerprint}
                      open={openFingerprint === t.test_fingerprint}
                      onToggle={toggleNarrow}
                    />
                  ) : (
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

            {status && !status.available && <CommitRangeReason status={status} isAdmin={isAdmin} />}

            {commits.length > 0 && (
              <div>
                <p className="eyebrow uppercase text-[var(--color-text-muted)] mb-1.5">
                  {status ? commitsHeading(status, commits.length) : `Commits since baseline (${commits.length})`}
                </p>
                {status && !status.base_anchor_is_strong && (
                  <p data-commit-range-weak-anchor="" className="text-xs text-[var(--color-text-muted)] mb-1.5">{WEAK_ANCHOR_LINE}</p>
                )}
                {status?.source === 'supplied' && (
                  <p data-commit-range-supplied="" className="text-xs text-[var(--color-text-muted)] mb-1.5">{SUPPLIED_LINE}</p>
                )}
                <ul className="space-y-1">
                  {commits.slice(0, 5).map((c) => {
                    // A link only to the server-built http(s) URL; a sha, message and author are text.
                    const href = safeCommitHref(c.commit_url)
                    return (
                    <li key={c.sha} className="flex items-start gap-2 text-xs text-[var(--color-text-muted)]">
                      <GitCommit className="h-3.5 w-3.5 text-[var(--color-text-faint)] flex-shrink-0 mt-0.5" />
                      {href ? (
                        <a href={href} target="_blank" rel="noopener noreferrer" data-commit-link=""
                          className="font-mono text-[var(--color-text)] mr-1 hover:underline">{c.sha}</a>
                      ) : (
                        <span className="font-mono text-[var(--color-text)] mr-1">{c.sha}</span>
                      )}
                      <span>{c.message}</span>
                      {(!status || c.author) && <span className="text-[var(--color-text-faint)]">— {c.author}</span>}
                    </li>
                    )
                  })}
                </ul>
              </div>
            )}
          </div>
        )}
      </section>

      {/* W23b FAIL-13: runtime / dependency / environment drift against the baseline above. */}
      {!isLoading && !error && drift && <ConfigDriftSection drift={drift} />}

      {/* EXEC-05: tests slower than their baseline, suites slower than their history. */}
      <DurationRegressionsCard runId={runId} />

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

/**
 * W22a ACT-04: the retention purge removed this run's stored baseline — say
 * so, with its build number (which survives the purge) and the purge date
 * (`formatDay`, the app's date format). React text only.
 */
function BaselinePurgedLine({ purged, className }: { purged: RegressionDiffBaselinePurged; className?: string }) {
  return (
    <p data-baseline-purged="" className={clsx('text-xs text-[var(--color-text-muted)]', className)}>
      {baselinePurgedLine(purged, formatDay)}
    </p>
  )
}

/**
 * W23b FAIL-12: a new failure WITH a fingerprint gets `Narrow`; the open one
 * renders the narrowing in place under its row (mount = open, so the `/bisect`
 * read happens only then). Names are React text and wrap.
 */
function NewFailureNarrowRow({
  runId, runBuild, testName, suiteName, fingerprint, open, onToggle,
}: {
  runId: string
  runBuild: string | null
  testName: string
  suiteName: string | null
  fingerprint: string
  open: boolean
  onToggle: (fingerprint: string) => void
}) {
  const boxId = `narrow-${fingerprint}`
  return (
    <li className="text-sm text-[var(--color-text-secondary)]" data-new-failure-narrowable="">
      <div className="flex items-center gap-2">
        <div className="h-1.5 w-1.5 rounded-full bg-[var(--status-failed-bg)] flex-shrink-0" />
        <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">
          {testName}
          {suiteName && <span className="text-[var(--color-text-muted)] text-xs"> · {suiteName}</span>}
        </span>
        <button
          type="button"
          className={clsx(INSIGHT_LINK_CLASS, 'ml-auto flex-none')}
          aria-expanded={open}
          aria-controls={boxId}
          onClick={() => onToggle(fingerprint)}
        >
          Narrow
        </button>
      </div>
      {open && (
        <div id={boxId} data-narrow-box="" className="ml-3.5 mt-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3">
          <BisectNarrowing runId={runId} fingerprint={fingerprint} heading={inlineHeading(testName)} runBuild={runBuild} />
        </div>
      )}
    </li>
  )
}

/** W10 FAIL-09: why the commit list is empty, and where to fix it when somewhere can. */
function CommitRangeReason({ status, isAdmin }: { status: CommitRangeStatus; isAdmin: boolean }) {
  const link = reasonLink(status.reason, isAdmin)
  const linkClass = 'text-[var(--color-accent)] hover:underline'
  return (
    <p data-commit-range-reason={status.reason ?? ''} className="text-sm text-[var(--color-text-muted)]">
      {reasonLine(status)}
      {link && ' '}
      {link && (link.scoped ? (
        <ScopedLink to={link.to} className={linkClass}>{link.label}</ScopedLink>
      ) : (
        <Link to={link.to} className={linkClass}>{link.label}</Link>
      ))}
    </p>
  )
}
