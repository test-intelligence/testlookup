import { Link } from 'react-router-dom'
import { ArrowDownRight, ArrowUpRight } from 'lucide-react'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useRunStepFlips } from '@/hooks/useRuns'
import type { RunStepFlipTest } from '@/types/runs'

/** One run test with a flickering step — links to its cross-run detail. */
function FlippingTestRow({ runId, test }: { runId: string; test: RunStepFlipTest }) {
  const top = test.report.flipping_steps[0]
  const stepLabel = top ? top.step_name || `step #${top.ordinal}` : ''
  const recovered = top?.last_status === 'PASSED'
  return (
    <Link
      to={`/runs/${runId}/tests/${test.test_id}`}
      className="flex items-center gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-hover)]/40 px-2.5 py-2 hover:bg-[var(--color-bg-hover)]"
    >
      {recovered ? (
        <ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-[var(--status-passed)]" />
      ) : (
        <ArrowDownRight className="h-3.5 w-3.5 shrink-0 text-[var(--status-failed)]" />
      )}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm text-[var(--color-text)]">{test.test_name}</span>
        {stepLabel && (
          <span className="block truncate text-xs text-[var(--color-text-muted)]">
            step “{stepLabel}” now {top?.last_status}
          </span>
        )}
      </span>
      <span className="badge-flaky shrink-0">
        {test.report.total_flips} {test.report.total_flips === 1 ? 'flip' : 'flips'}
      </span>
    </Link>
  )
}

/**
 * Run-level roll-up of cross-run step-flip (FLK-P6 slice 5). Surfaces WHICH
 * TESTS in this run have a flickering step — step-level flakiness across the
 * whole run rather than per-test — so a QA engineer triaging the run sees it
 * without opening each test. Read-only; lazy SWR fetch.
 */
export default function RunStepFlipCard({ runId }: { runId?: string }) {
  const { data, isLoading } = useRunStepFlips(runId)

  return (
    <div
      className="overflow-hidden rounded-xl"
      style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)' }}
    >
      <div
        className="flex items-center justify-between gap-2.5 px-4 py-3"
        style={{ borderBottom: '1px solid var(--color-border)' }}
      >
        <h3 className="m-0 text-[13px] font-semibold text-[var(--color-text)]">
          Step-level flakiness
        </h3>
        {data && (
          <span className="text-[12px] text-[var(--color-text-muted)]">
            {data.tests_with_flips} of {data.tests_analyzed} test
            {data.tests_analyzed === 1 ? '' : 's'}
          </span>
        )}
      </div>

      <div className="p-3">
        {isLoading ? (
          <div className="flex items-center justify-center py-6">
            <LoadingSpinner size="md" />
          </div>
        ) : !data || data.tests_with_flips === 0 ? (
          <p className="py-4 text-center text-sm text-[var(--color-text-muted)]">
            No flickering steps across {data?.tests_analyzed ?? 0} analysed test
            {(data?.tests_analyzed ?? 0) === 1 ? '' : 's'} — steps are stable.
          </p>
        ) : (
          <div className="space-y-1.5">
            {data.tests.map(test => (
              <FlippingTestRow key={test.test_id} runId={runId as string} test={test} />
            ))}
            {data.truncated && (
              <p className="pt-1 text-center text-xs text-[var(--color-text-muted)]">
                Showing the analysed subset — this run has more tests than the
                step-flip analysis cap.
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
