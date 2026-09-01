import { useState } from 'react'
import { clsx } from 'clsx'
import { ChevronRight, Paperclip } from 'lucide-react'
import StatusBadge from '@/components/ui/StatusBadge'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useTestSteps } from '@/hooks/useRuns'
import { formatDuration } from '@/utils/formatters'
import { isSafeExternalUrl } from '@/utils/safeUrl'
import { normalizeAttachments, normalizeParameters, normalizeSteps } from '@/utils/testCaseDetail'
import type { NormalizedTestCaseDetail, TestCaseDetailAttachment, TestCaseDetailStep, TestCaseLink } from '@/types/test-case-detail'

const FAILED_STATUSES = ['FAILED', 'BROKEN']

function AttachmentList({ attachments }: { attachments?: TestCaseDetailAttachment[] | null }) {
  const safeAttachments = attachments ?? []
  if (safeAttachments.length === 0) return null
  return (
    <ul className="mt-1.5 space-y-1">
      {safeAttachments.map(att => (
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

function LinkList({ links }: { links?: TestCaseLink[] | null }) {
  const safeLinks = (links ?? []).filter(link => isSafeExternalUrl(link.url))
  if (safeLinks.length === 0) return null
  return (
    <ul className="mt-1.5 space-y-1">
      {safeLinks.map((link, index) => (
        <li key={`${link.url}-${index}`} className="text-xs">
          <a
            href={link.url}
            target="_blank"
            rel="noreferrer"
            className="text-[var(--color-accent)] underline-offset-2 hover:underline"
          >
            {link.name || link.url}
          </a>
          {link.type && <span className="ml-1.5 text-[10px] text-[var(--color-text-muted)]">{link.type}</span>}
        </li>
      ))}
    </ul>
  )
}

function ParameterList({ parameters }: { parameters?: TestCaseDetailStep['parameters'] }) {
  const safeParameters = normalizeParameters(parameters)
  if (safeParameters.length === 0) return null
  return (
    <dl className="mt-1.5 grid grid-cols-1 gap-1 sm:grid-cols-2">
      {safeParameters.map((parameter, index) => (
        <div key={`${parameter.name}-${index}`} className="rounded bg-[var(--color-bg-hover)]/50 px-2 py-1">
          <dt className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">{parameter.name}</dt>
          <dd className="break-words font-mono text-xs text-[var(--color-text-secondary)]">
            {parameter.masked ? 'Masked' : (parameter.display_value ?? parameter.value ?? 'Not supplied')}
          </dd>
        </div>
      ))}
    </dl>
  )
}

function StepNode({ step, depth }: { step: TestCaseDetailStep; depth: number }) {
  const isFailed = FAILED_STATUSES.includes(step.status?.toUpperCase())
  const hasParameters = normalizeParameters(step.parameters).length > 0
  const hasDetail = Boolean(
    step.assertion_message || step.assertion_trace || step.expected || step.actual ||
      step.action || step.error_message || step.error_trace || hasParameters || step.links?.length,
  )
  const hasChildren = step.steps.length > 0
  const hasAttachments = (step.attachments?.length ?? 0) > 0
  // Auto-expand failing steps so the user lands on the relevant detail.
  const [open, setOpen] = useState<boolean>(isFailed)
  const expandable = hasDetail || hasChildren || hasAttachments

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
              {(step.assertion_message || step.error_message) && (
                <p className="text-xs text-[var(--color-danger)]">{step.assertion_message || step.error_message}</p>
              )}
              {(step.action || step.expected != null || step.actual != null) && (
                <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
                  {step.action && (
                    <div className="rounded bg-[var(--color-bg-hover)]/50 px-2 py-1 sm:col-span-2">
                      <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Action</span>
                      <p className="break-words text-xs text-[var(--color-text-secondary)]">{step.action}</p>
                    </div>
                  )}
                  {step.expected != null && (
                    <div className="rounded bg-[var(--color-bg-hover)]/50 px-2 py-1">
                      <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
                        Expected
                      </span>
                      <p className="break-words font-mono text-xs text-[var(--color-text-secondary)]">
                        {step.expected}
                      </p>
                    </div>
                  )}
                  {step.actual != null && (
                    <div className="rounded bg-[var(--color-bg-hover)]/50 px-2 py-1">
                      <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
                        Actual
                      </span>
                      <p className="break-words font-mono text-xs text-[var(--color-text-secondary)]">
                        {step.actual}
                      </p>
                    </div>
                  )}
                </div>
              )}
              {step.assertion_trace || step.error_trace ? (
                <pre className="max-h-64 overflow-auto rounded bg-[var(--color-bg-secondary)] px-2 py-1.5 font-mono text-[11px] leading-relaxed text-[var(--color-text-muted)]">
                  {step.assertion_trace || step.error_trace}
                </pre>
              ) : null}
              <ParameterList parameters={step.parameters} />
              <LinkList links={step.links} />
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

export default function TestStepsPanel({
  runId,
  testId,
  detail,
}: {
  runId?: string
  testId?: string
  detail?: NormalizedTestCaseDetail | null
}) {
  const { data, isLoading } = useTestSteps(runId, testId)

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <LoadingSpinner size="md" />
      </div>
    )
  }

  // An explicitly supplied [] is meaningful: the enriched endpoint has
  // confirmed that this source had no steps. Null means fall back to the
  // legacy lazy endpoint while the backend contract is being migrated.
  const steps = detail?.steps !== null && detail?.steps !== undefined
    ? detail.steps
    : normalizeSteps(data?.steps)
  const testAttachments = detail?.attachments !== null && detail?.attachments !== undefined
    ? detail.attachments
    : normalizeAttachments(data?.attachments)

  if (steps.length === 0 && testAttachments.length === 0) {
    return (
      <div className="card py-8 text-center text-sm text-[var(--color-text-muted)]">
        No granular steps captured for this test.
      </div>
    )
  }

  return (
    <div className="card space-y-3 p-3">
      {(data || detail?.execution) && ((data?.retry_count ?? detail?.execution?.retry_count) != null || data?.is_flaky_run || detail?.execution?.is_flaky) && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-muted)]">
          {(data?.retry_count ?? detail?.execution?.retry_count ?? 0) > 0 && (
            <span className="badge bg-[var(--color-bg-secondary)] border border-[var(--color-border)]">
              {data?.retry_count ?? detail?.execution?.retry_count} {(data?.retry_count ?? detail?.execution?.retry_count) === 1 ? 'retry' : 'retries'}
            </span>
          )}
          {(data?.is_flaky_run || detail?.execution?.is_flaky) && <span className="badge-flaky">flaky run</span>}
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
