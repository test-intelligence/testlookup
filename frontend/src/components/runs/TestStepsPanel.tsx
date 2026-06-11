import { useState } from 'react'
import { clsx } from 'clsx'
import { ChevronRight, Paperclip } from 'lucide-react'
import StatusBadge from '@/components/ui/StatusBadge'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useTestSteps } from '@/hooks/useRuns'
import { formatDuration } from '@/utils/formatters'
import type { TestAttachment, TestStep } from '@/types/runs'

const FAILED_STATUSES = ['FAILED', 'BROKEN']

function AttachmentList({ attachments }: { attachments: TestAttachment[] }) {
  if (attachments.length === 0) return null
  return (
    <ul className="mt-1.5 space-y-1">
      {attachments.map(att => (
        <li
          key={att.id}
          className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]"
        >
          <Paperclip className="h-3 w-3 shrink-0" />
          <span className="truncate">{att.name}</span>
          {att.media_type && (
            <span className="text-[10px] text-[var(--color-text-muted)]/70">
              {att.media_type}
            </span>
          )}
        </li>
      ))}
    </ul>
  )
}

function StepNode({ step, depth }: { step: TestStep; depth: number }) {
  const isFailed = FAILED_STATUSES.includes(step.status?.toUpperCase())
  const hasDetail = Boolean(
    step.assertion_message || step.assertion_trace || step.expected_value || step.actual_value,
  )
  const hasChildren = step.steps.length > 0
  // Auto-expand failing steps so the user lands on the relevant detail.
  const [open, setOpen] = useState<boolean>(isFailed)
  const expandable = hasDetail || hasChildren

  return (
    <div>
      <div
        className={clsx(
          'flex items-start gap-2 rounded-md px-2 py-1.5',
          isFailed
            ? 'bg-[var(--color-danger)]/10 border-l-2 border-[var(--color-danger)]'
            : 'hover:bg-[var(--color-bg-hover)]/50',
        )}
        style={{ marginLeft: depth * 16 }}
      >
        <button
          type="button"
          onClick={() => expandable && setOpen(o => !o)}
          className={clsx(
            'mt-0.5 shrink-0 text-[var(--color-text-muted)]',
            expandable ? 'cursor-pointer hover:text-[var(--color-text)]' : 'invisible',
          )}
          aria-label={open ? 'Collapse step' : 'Expand step'}
          aria-expanded={expandable ? open : undefined}
        >
          <ChevronRight className={clsx('h-3.5 w-3.5 transition-transform', open && 'rotate-90')} />
        </button>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <StatusBadge status={step.status} className="shrink-0" />
            <span className="truncate text-sm text-[var(--color-text-secondary)]">
              {step.keyword ? `${step.keyword} ` : ''}
              {step.name}
            </span>
            <span className="ml-auto shrink-0 font-mono text-xs text-[var(--color-text-muted)]">
              {formatDuration(step.duration_ms)}
            </span>
          </div>

          {open && hasDetail && (
            <div className="mt-1.5 space-y-1.5">
              {step.assertion_message && (
                <p className="text-xs text-[var(--color-danger)]">{step.assertion_message}</p>
              )}
              {(step.expected_value != null || step.actual_value != null) && (
                <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
                  {step.expected_value != null && (
                    <div className="rounded bg-[var(--color-bg-hover)]/50 px-2 py-1">
                      <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
                        Expected
                      </span>
                      <p className="break-words font-mono text-xs text-[var(--color-text-secondary)]">
                        {step.expected_value}
                      </p>
                    </div>
                  )}
                  {step.actual_value != null && (
                    <div className="rounded bg-[var(--color-bg-hover)]/50 px-2 py-1">
                      <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
                        Actual
                      </span>
                      <p className="break-words font-mono text-xs text-[var(--color-text-secondary)]">
                        {step.actual_value}
                      </p>
                    </div>
                  )}
                </div>
              )}
              {step.assertion_trace && (
                <pre className="max-h-64 overflow-auto rounded bg-[var(--color-bg-secondary)] px-2 py-1.5 font-mono text-[11px] leading-relaxed text-[var(--color-text-muted)]">
                  {step.assertion_trace}
                </pre>
              )}
            </div>
          )}

          {open && <AttachmentList attachments={step.attachments} />}
        </div>
      </div>

      {open && hasChildren && (
        <div className="mt-0.5 space-y-0.5">
          {step.steps.map(child => (
            <StepNode key={child.id} step={child} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  )
}

export default function TestStepsPanel({ runId, testId }: { runId?: string; testId?: string }) {
  const { data, isLoading } = useTestSteps(runId, testId)

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <LoadingSpinner size="md" />
      </div>
    )
  }

  const steps = data?.steps ?? []
  const testAttachments = data?.attachments ?? []

  if (steps.length === 0 && testAttachments.length === 0) {
    return (
      <div className="card py-8 text-center text-sm text-[var(--color-text-muted)]">
        No granular steps captured for this test.
      </div>
    )
  }

  return (
    <div className="card space-y-3 p-3">
      {data && (data.retry_count != null || data.is_flaky_run) && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-muted)]">
          {data.retry_count != null && data.retry_count > 0 && (
            <span className="badge bg-[var(--color-bg-secondary)] border border-[var(--color-border)]">
              {data.retry_count} {data.retry_count === 1 ? 'retry' : 'retries'}
            </span>
          )}
          {data.is_flaky_run && <span className="badge-flaky">flaky run</span>}
        </div>
      )}

      <div className="space-y-0.5">
        {steps.map(step => (
          <StepNode key={step.id} step={step} depth={0} />
        ))}
      </div>

      {testAttachments.length > 0 && (
        <div className="border-t border-[var(--color-border)] pt-2">
          <p className="mb-1 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
            Attachments
          </p>
          <AttachmentList attachments={testAttachments} />
        </div>
      )}
    </div>
  )
}
