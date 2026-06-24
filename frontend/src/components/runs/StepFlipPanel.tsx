import { clsx } from 'clsx'
import { ArrowDownRight, ArrowUpRight } from 'lucide-react'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useTestStepFlips } from '@/hooks/useRuns'
import type { StepFlipSummary } from '@/types/runs'

/** Roll-up row for one step that flipped PASSED↔FAILED across runs. */
function FlippingStepRow({ step }: { step: StepFlipSummary }) {
  const label = step.step_name || `step #${step.ordinal}`
  const recovered = step.last_status === 'PASSED'
  return (
    <div className="flex items-center gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-hover)]/40 px-2.5 py-2">
      {recovered ? (
        <ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-[var(--status-passed)]" />
      ) : (
        <ArrowDownRight className="h-3.5 w-3.5 shrink-0 text-[var(--status-failed)]" />
      )}
      <span className="min-w-0 flex-1 truncate text-sm text-[var(--color-text-secondary)]">
        {label}
      </span>
      <span className="badge-flaky shrink-0">
        {step.flip_count} {step.flip_count === 1 ? 'flip' : 'flips'}
      </span>
      <span
        className={clsx(
          'shrink-0 text-xs',
          recovered ? 'text-[var(--status-passed)]' : 'text-[var(--status-failed)]',
        )}
      >
        now {step.last_status}
      </span>
    </div>
  )
}

/**
 * Cross-run step-flip report (FLK-P6 slice 4). Shows which step oscillated
 * PASSED↔FAILED across runs — pointing at one flickering step rather than a
 * whole-test verdict. Read-only; lazy SWR fetch.
 */
export default function StepFlipPanel({ runId, testId }: { runId?: string; testId?: string }) {
  const { data, isLoading } = useTestStepFlips(runId, testId)

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <LoadingSpinner size="md" />
      </div>
    )
  }

  const report = data?.report
  if (!report || report.runs_analyzed < 2) {
    return (
      <div className="card py-8 text-center text-sm text-[var(--color-text-muted)]">
        Not enough cross-run step history to detect step-level flakiness yet.
      </div>
    )
  }

  if (!report.has_step_flip) {
    return (
      <div className="card py-8 text-center text-sm text-[var(--color-text-muted)]">
        No cross-run step-flip across {report.runs_analyzed} runs — steps are stable.
      </div>
    )
  }

  return (
    <div className="card space-y-3 p-3">
      <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-muted)]">
        <span className="badge-flaky">step-level flakiness</span>
        <span className="badge bg-[var(--color-bg-secondary)] border border-[var(--color-border)]">
          {report.total_flips} total flips
        </span>
        <span className="ml-auto">across {report.runs_analyzed} runs</span>
      </div>

      <p className="text-xs leading-relaxed text-[var(--color-text-secondary)]">
        {report.summary}
      </p>

      <div className="space-y-1.5">
        {report.flipping_steps.map(step => (
          <FlippingStepRow key={step.ordinal} step={step} />
        ))}
      </div>
    </div>
  )
}
