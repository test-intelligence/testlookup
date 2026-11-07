/**
 * W36 STRAT-08 — "Likely affected tests" on the run's Changes tab, after the
 * commit list: this run's tests ranked by how well their names / locations
 * overlap the run's changed files and, once the project has enough commit
 * history, how often they failed in past runs that touched the same files
 * (`GET /api/v1/runs/{id}/likely-affected-tests`).
 *
 * Advisory only. It never selects or skips a test, and nothing reads it to
 * decide what runs, what is quarantined or what a gate says; the server's
 * caveat is the footer in every state.
 *
 * Honest states, every number from the answer:
 *   loading / error   a spinner / one line
 *   unavailable       why, in words (no commit range; a range without files),
 *                     never an empty list standing in for "nothing affected"
 *   path-only         history not ready: the list ranks by path overlap alone
 *                     and the banner names the readiness reason; each row says
 *                     "no history yet" (`co_failure_runs` is null, not 0)
 *   none matched      available, no test scored: a measured result, said so
 *   the list          name, status in this run, a score bar with its number,
 *                     the matched paths and "failed with these files in N of M
 *                     past runs"; a known-flaky test carries a "flaky" tag
 */
import { Crosshair } from 'lucide-react'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useLikelyAffectedTests } from '@/hooks/useCommitAttribution'
import {
  LIKELY_AFFECTED_LIMIT,
  type LikelyAffectedReason,
  type LikelyAffectedTest,
  type LikelyAffectedTests,
} from '@/services/commitAttributionService'
import { formatNumber } from '@/utils/formatters'

/** The footer while the answer (and its own caveat) has not arrived; the server's text once it has. */
export const ADVISORY_CAVEAT = 'Advisory only: ranked by path overlap and past co-failures. It never selects or skips tests.'

const UNAVAILABLE_COPY: Record<string, string> = {
  no_commit_range: 'No commit range is known for this run, so there are no changed files to match its tests against.',
  no_changed_files:
    "This run's commit range lists no changed files (the source sent none), so there is nothing to match its tests against.",
}

function unavailableLine(reason: string | null): string {
  return (reason && UNAVAILABLE_COPY[reason]) || 'Likely affected tests are not available for this run.'
}

const KNOWN_STATUSES = new Set(['passed', 'failed', 'broken', 'skipped'])

function StatusTag({ status }: { status: string }) {
  const key = status.toLowerCase()
  const tone = KNOWN_STATUSES.has(key) ? key : 'unknown'
  return (
    <span data-likely-status={key} className="text-xs" style={{ color: `var(--status-${tone})` }}>
      {key}
    </span>
  )
}

function FlakyTag() {
  return (
    <span
      data-likely-flaky=""
      title="Known flaky: its past co-failures add nothing to the score (its flakiness alone explains them)."
      className="eyebrow rounded border border-[var(--status-flaky)] px-1 uppercase text-[var(--status-flaky)]"
    >
      flaky
    </span>
  )
}

function ScoreBar({ score }: { score: number }) {
  const share = Math.min(1, Math.max(0, score))
  return (
    <span className="inline-flex flex-none items-center gap-1.5" title="Score, 0 to 1">
      <span aria-hidden="true" className="inline-block h-1.5 w-16 overflow-hidden rounded bg-[var(--color-bg-secondary)]">
        <span className="block h-full rounded bg-[var(--color-accent)]" style={{ width: `${Math.round(share * 100)}%` }} />
      </span>
      <span data-likely-score="" className="text-xs tabular-nums text-[var(--color-text-secondary)]">
        {score.toFixed(2)}
      </span>
    </span>
  )
}

/**
 * "failed with these files in 2 of 7 past runs"; "no earlier run changed these files" when history is ready and
 * none did (a measured 0 of 0); "no history yet" when the count is unknown (null).
 */
function historyText(reason: LikelyAffectedReason): string {
  const failed = reason.co_failure_runs
  if (failed === null) return 'no history yet'
  const considered = reason.history_runs_considered
  if (considered === 0) return 'no earlier run changed these files'
  if (considered === null) return `failed with these files in ${formatNumber(failed)} past run${failed === 1 ? '' : 's'}`
  return `failed with these files in ${formatNumber(failed)} of ${formatNumber(considered)} past run${considered === 1 ? '' : 's'}`
}

function ReasonLine({ reason }: { reason: LikelyAffectedReason }) {
  const paths = reason.overlapping_paths
  return (
    <p data-likely-reason="" className="m-0 text-xs text-[var(--color-text-muted)] [overflow-wrap:anywhere]">
      {paths.length > 0 ? (
        <>
          Matches{' '}
          {paths.map((path, index) => (
            <span key={path}>
              {index > 0 && ', '}
              <code className="font-mono">{path}</code>
            </span>
          ))}
        </>
      ) : (
        'No changed path matches it'
      )}
      {' · '}
      {historyText(reason)}
    </p>
  )
}

function TestRow({ test }: { test: LikelyAffectedTest }) {
  return (
    <li data-likely-test={test.test_fingerprint} className="flex flex-col gap-0.5">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="min-w-0 text-sm text-[var(--color-text)] [overflow-wrap:anywhere]">
          {test.test_name}
          {test.class_name && <span className="text-xs text-[var(--color-text-muted)]"> · {test.class_name}</span>}
        </span>
        <StatusTag status={test.status_in_run} />
        {test.reason.known_flaky && <FlakyTag />}
        <span className="ml-auto">
          <ScoreBar score={test.score} />
        </span>
      </div>
      <ReasonLine reason={test.reason} />
    </li>
  )
}

function Body({ data }: { data: LikelyAffectedTests }) {
  if (!data.available) {
    return (
      <p data-likely-state="unavailable" data-likely-reason-code={data.reason ?? ''} className="mt-3 text-sm text-[var(--color-text-muted)]">
        {unavailableLine(data.reason)}
      </p>
    )
  }
  const { history } = data
  const tests = data.tests.slice(0, LIKELY_AFFECTED_LIMIT)
  return (
    <div className="mt-3 space-y-3">
      {!history.available && (
        <p data-likely-path-only="" className="m-0 rounded border border-[var(--color-border)] px-2.5 py-1.5 text-xs text-[var(--color-text-secondary)]">
          Ranked by path overlap only: past co-failures are not counted yet
          {history.insufficient_data_reason ? ` — ${history.insufficient_data_reason}.` : '.'}
        </p>
      )}
      {tests.length === 0 ? (
        <p data-likely-state="none" className="m-0 text-sm text-[var(--color-text-secondary)]">
          No test in this run matched the {formatNumber(data.changed_files_count)} changed file
          {data.changed_files_count === 1 ? '' : 's'}.
        </p>
      ) : (
        <>
          <p className="m-0 text-xs text-[var(--color-text-muted)]">
            {formatNumber(tests.length)} of this run&apos;s {formatNumber(data.total_candidates)} tests, ranked against{' '}
            {formatNumber(data.changed_files_count)} changed file{data.changed_files_count === 1 ? '' : 's'}.
          </p>
          <ol data-likely-list="" className="m-0 list-none space-y-2 p-0">
            {tests.map((test) => (
              <TestRow key={test.test_fingerprint} test={test} />
            ))}
          </ol>
        </>
      )}
      {history.available && history.scan_capped && (
        <p data-likely-scan-capped="" className="m-0 text-xs text-[var(--color-text-faint)]">
          Only the newest {formatNumber(history.runs_considered)} past runs were read, so the counts are floors.
        </p>
      )}
    </div>
  )
}

export default function LikelyAffectedTestsCard({ runId }: { runId: string }) {
  const { data, isLoading, isError } = useLikelyAffectedTests(runId)
  return (
    <section aria-label="Likely affected tests" data-likely-affected="" className="card">
      <h2 className="m-0 flex items-center gap-2 text-sm font-semibold text-[var(--color-text)]">
        <Crosshair className="h-4 w-4 text-[var(--color-text-muted)]" aria-hidden="true" />
        Likely affected tests
      </h2>
      {isLoading && (
        <div data-likely-state="loading" className="flex justify-center py-6">
          <LoadingSpinner />
        </div>
      )}
      {!isLoading && isError && (
        <p data-likely-state="error" className="mt-3 text-sm text-[var(--status-failed)]">
          Could not load the likely affected tests for this run.
        </p>
      )}
      {!isLoading && !isError && data && <Body data={data} />}
      <p data-likely-caveat="" className="mb-0 mt-3 text-xs text-[var(--color-text-faint)]">
        {data?.caveat || ADVISORY_CAVEAT}
      </p>
    </section>
  )
}
