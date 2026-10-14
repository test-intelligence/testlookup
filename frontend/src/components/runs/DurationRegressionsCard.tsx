/**
 * EXEC-05: "Did anything get slower?" on the run's Changes tab.
 *
 * Two checks, from `GET /api/v1/runs/{run_id}/duration-regressions`:
 *  - tests slower than their baseline: "Slower than baseline: checkout::test_pay
 *    +300ms (4.1σ)";
 *  - suites slower than their history: "Suite checkout: 1.8× slower than its
 *    previous 20 runs".
 *
 * Absence is not health: a run the backend could not judge shows why ("Not
 * enough history yet (4 of 10 runs)", "detection is off for this project"),
 * never "nothing got slower". A check that could not be made is named under
 * the one that could.
 */
import { Gauge } from 'lucide-react'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useDurationRegressions } from '@/hooks/useDurationRegressions'
import type {
  DurationCheck,
  DurationSpike,
  SuiteDurationRegression,
} from '@/services/durationRegressionsService'
import { describeDurationReason as describeReason } from '@/utils/durationRegressions'
import { formatDuration } from '@/utils/formatters'

/** Rows of each list before "…and N more". */
const SHOWN = 10

function testLabel(spike: DurationSpike): string {
  return spike.test_name || spike.test_fingerprint
}

function NewBadge() {
  return (
    <span className="rounded border border-[var(--status-flaky)] px-1 text-[10px] font-medium uppercase tracking-wide text-[var(--status-flaky)]">
      New
    </span>
  )
}

function SpikeRow({ spike }: { spike: DurationSpike }) {
  const delta = spike.delta_ms ?? Math.round(spike.observed_ms - spike.baseline_mean_ms)
  return (
    <li data-duration-spike={spike.test_fingerprint} className="flex flex-wrap items-baseline gap-x-2 text-sm text-[var(--color-text-secondary)]">
      <span className="text-[var(--color-text)]">{testLabel(spike)}</span>
      <span className="tabular-nums text-[var(--status-failed)]">+{formatDuration(Math.max(0, delta))} ({spike.sigma}σ)</span>
      <span className="text-xs text-[var(--color-text-muted)] tabular-nums">
        {formatDuration(spike.observed_ms)} vs a mean of {formatDuration(Math.round(spike.baseline_mean_ms))} over {spike.sample_count} runs
      </span>
      {spike.new && <NewBadge />}
    </li>
  )
}

function SuiteRow({ suite }: { suite: SuiteDurationRegression }) {
  const what = suite.metric === 'wall' ? 'wall clock' : 'summed test time'
  return (
    <li data-duration-suite={suite.suite_key} className="flex flex-wrap items-baseline gap-x-2 text-sm text-[var(--color-text-secondary)]">
      <span className="text-[var(--color-text)]">
        Suite {suite.suite_name || suite.suite_key}: {suite.ratio}× slower than its previous {suite.baseline_runs} runs
      </span>
      <span className="text-xs text-[var(--color-text-muted)] tabular-nums">
        median {what} of the last {suite.recent_runs}: {formatDuration(suite.recent_median_ms == null ? null : Math.round(suite.recent_median_ms))}
        {' '}vs {formatDuration(suite.baseline_median_ms == null ? null : Math.round(suite.baseline_median_ms))}
      </span>
      {suite.new && <NewBadge />}
    </li>
  )
}

function More({ shown, total }: { shown: number; total: number }) {
  if (total <= shown) return null
  return <li className="text-xs text-[var(--color-text-muted)]">…and {total - shown} more</li>
}

/** "Tests: not enough history yet (3 of 10 runs)" for a check that could not be made beside one that could. */
function Unmeasured({ label, check }: { label: string; check: DurationCheck }) {
  if (check.measured !== false) return null
  return (
    <p className="text-xs text-[var(--color-text-muted)]">
      {label}: {describeReason(check.reason ?? null).replace(/^./, c => c.toLowerCase())}
    </p>
  )
}

export default function DurationRegressionsCard({ runId }: { runId: string }) {
  const { data, error, isLoading } = useDurationRegressions(runId)

  return (
    <section aria-label="Duration regressions" data-duration-regressions="" className="card">
      <h2 className="m-0 flex items-center gap-2 text-sm font-semibold text-[var(--color-text)]">
        <Gauge className="h-4 w-4 text-[var(--color-text-muted)]" />
        Did anything get slower?
      </h2>
      {isLoading && <div className="flex justify-center py-6"><LoadingSpinner /></div>}
      {!isLoading && Boolean(error) && (
        <p className="mt-3 text-sm text-[var(--status-failed)]">Could not load the duration regressions for this run.</p>
      )}
      {!isLoading && !error && data && !data.measured && (
        <p data-duration-state="unmeasured" className="mt-3 text-sm text-[var(--color-text-muted)]">
          {describeReason(data.reason)}
        </p>
      )}
      {!isLoading && !error && data && data.measured && (
        <div className="mt-3 space-y-3">
          {data.spike_count === 0 && data.suite_regression_count === 0 && (
            <p data-duration-state="none" className="text-sm text-[var(--color-text-secondary)]">
              No test or suite is slower than its baseline.
            </p>
          )}
          {data.spikes.length > 0 && (
            <div>
              <p className="mb-1.5 text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                Slower than baseline ({data.spike_count})
              </p>
              <ul className="space-y-1">
                {data.spikes.slice(0, SHOWN).map(spike => <SpikeRow key={spike.test_fingerprint} spike={spike} />)}
                <More shown={Math.min(SHOWN, data.spikes.length)} total={data.spike_count} />
              </ul>
            </div>
          )}
          {data.suites.length > 0 && (
            <div>
              <p className="mb-1.5 text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                Suites slower than their history ({data.suite_regression_count})
              </p>
              <ul className="space-y-1">
                {data.suites.slice(0, SHOWN).map(suite => <SuiteRow key={suite.suite_key} suite={suite} />)}
                <More shown={Math.min(SHOWN, data.suites.length)} total={data.suite_regression_count} />
              </ul>
            </div>
          )}
          <Unmeasured label="Tests" check={data.test_check} />
          <Unmeasured label="Suites" check={data.suite_check} />
          {!data.stored && (
            <p className="text-xs text-[var(--color-text-faint)]">
              Computed now: this run was not evaluated when it was ingested.
            </p>
          )}
        </div>
      )}
    </section>
  )
}
