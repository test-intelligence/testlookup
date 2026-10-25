/**
 * W14 AGT-12: a bulk AI-analysis job, tracked in the Runs page (above the table, where the page's other in-page
 * sections open; `?bulk_job=<id>` keeps it across a reload).
 *
 * One read (`useBulkJob`, `GET /agents/pipelines/bulk-jobs/{id}`) polled every 5 s ONLY while the job is
 * active; it stops when the job is finished or cancelled, and when the job is gone (an expired record with no
 * pipeline left, or someone else's job: one 404 for both). At the finish the runs list is revalidated ONCE
 * (`onSettled`). The counts are the server's, counted from the pipeline rows on each read: a request record
 * that expired says so, never zeros. Cancel asks first (an `InlineConfirm`) and stops only what has not started;
 * running pipelines keep their own Stop on Pipeline runs.
 */
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import GaugeBar from '@/components/charts/GaugeBar'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import InlineConfirm from '@/components/ui/inPage/InlineConfirm'
import InlineFormSection from '@/components/ui/inPage/InlineFormSection'
import { useBulkJob } from '@/hooks/useAgentOps'
import agentService from '@/services/agentService'
import { pipelineActionError } from '@/services/pipelineActions'
import {
  BULK_JOB_GONE, BULK_LOAD_ERROR, count, isSettled, progressLines,
} from './bulkJobModel'

export interface BulkJobProgressProps {
  jobId: string
  /** QA_ENGINEER: a job is readable only by its requester (or an admin), so the reader may cancel it. */
  canCancel: boolean
  /** Once, when the job settles (finished / cancelled): the runs list revalidates. */
  onSettled: () => void
  /** Close clears `?bulk_job=`. */
  onClose: () => void
}

export const CANCEL_QUESTION =
  'Cancel this bulk job? Queued pipelines that have not started will not run; running ones keep going (stop them on Pipeline runs).'

/** The state chip's ink, from the UI tokens (a job state is not a test result). */
const STATE_TONE: Record<string, { background: string; border: string; color: string }> = {
  active: { background: 'var(--ui-info-bg)', border: '1px solid var(--ui-info-bd)', color: 'var(--ui-info-ink)' },
  finished: { background: 'var(--ui-success-bg)', border: '1px solid var(--ui-success-bd)', color: 'var(--ui-success-ink)' },
  cancelled: { background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text-secondary)' },
}

const statusOf = (error: unknown): number | undefined =>
  (error as { response?: { status?: number } } | null)?.response?.status

export default function BulkJobProgress({ jobId, canCancel, onSettled, onClose }: BulkJobProgressProps) {
  // A 404 (expired, or not this reader's) is final: no key → no request, no poll.
  const [goneId, setGoneId] = useState<string | null>(null)
  const gone = goneId === jobId
  const { data: job, error, mutate } = useBulkJob(gone ? null : jobId)
  if (!gone && statusOf(error) === 404) setGoneId(jobId)

  // The finish revalidates the runs list once: the transition from active, not every settled read.
  const lastState = useRef<string | undefined>(undefined)
  useEffect(() => {
    const previous = lastState.current
    lastState.current = job?.state
    if (previous === 'active' && isSettled(job?.state)) onSettled()
  }, [job?.state, onSettled])

  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [cancelError, setCancelError] = useState<string | null>(null)
  const cancel = async () => {
    setBusy(true)
    setCancelError(null)
    try {
      const next = await agentService.cancelBulkJob(jobId)
      await mutate(next, { revalidate: false })
      setConfirming(false)
    } catch (err) {
      setCancelError(pipelineActionError(err, 'Could not cancel the bulk job.'))
      void mutate()
    } finally {
      setBusy(false)
    }
  }

  const lines = job ? progressLines(job) : null
  const title = (
    <span className="inline-flex flex-wrap items-center gap-2">
      Bulk AI analysis
      {job && lines && (
        <>
          <span
            data-bulk-job-state={job.state}
            className="inline-flex items-center rounded-full px-2 py-0.5 text-2xs font-medium"
            style={STATE_TONE[job.state] ?? STATE_TONE.cancelled}
          >
            {lines.stateLabel}
          </span>
          <span className="text-xs font-normal text-[var(--color-text-muted)]">
            {job.workflow_type} · {job.source === 'filter' ? 'runs matching the list filter' : 'selected runs'}
          </span>
        </>
      )}
    </span>
  )

  return (
    <InlineFormSection
      title={title}
      ariaLabel="Bulk AI analysis"
      showClose={false}
      autoFocus={false}
      onClose={onClose}
      testId="bulk-job-panel"
      dataAttributes={{ 'data-bulk-job': jobId }}
    >
      <div className="flex flex-col gap-2 text-xs">
        {gone ? (
          <p role="note" className="m-0 text-[var(--color-text-secondary)]">{BULK_JOB_GONE}</p>
        ) : !job && error ? (
          <p className="m-0 flex items-center gap-2 text-[var(--color-text-secondary)]">
            {BULK_LOAD_ERROR}
            <button type="button" className="btn-secondary text-xs" onClick={() => void mutate()}>Retry</button>
          </p>
        ) : !job || !lines ? (
          <LoadingSpinner size="sm" />
        ) : (
          <>
            {lines.request != null && (
              <p data-bulk-line="request" className="m-0 tabular-nums text-[var(--color-text)]">{lines.request}</p>
            )}
            {lines.recordNote != null && (
              <p data-bulk-line="record" className="m-0 text-[var(--color-text-secondary)]">{lines.recordNote}</p>
            )}
            <p data-bulk-line="progress" className="m-0 tabular-nums text-[var(--color-text-secondary)]">{lines.progress}</p>
            {lines.gauge && (
              <div className="max-w-[28rem]">
                <GaugeBar
                  value={lines.gauge.started}
                  domain={[0, lines.gauge.queued]}
                  size="sm"
                  tone="accent"
                  label="Pipelines started of those queued"
                  format={(v) => count(Math.round(v))}
                />
              </div>
            )}
            {lines.note != null && (
              <p role="note" data-bulk-line="note" className="m-0 text-[var(--color-text-secondary)]">{lines.note}</p>
            )}
          </>
        )}
        {cancelError && <p role="note" className="m-0 text-[var(--ui-danger-ink)]">{cancelError}</p>}
        {confirming && job?.state === 'active' && (
          <InlineConfirm
            message={CANCEL_QUESTION}
            confirmLabel="Cancel job"
            cancelLabel="Keep running"
            tone="danger"
            busy={busy}
            onConfirm={() => void cancel()}
            onCancel={() => setConfirming(false)}
            testId="bulk-job-cancel-confirm"
          />
        )}
        <div className="flex flex-wrap items-center gap-2 pt-1">
          {canCancel && job?.state === 'active' && !confirming && (
            <button type="button" className="btn-secondary text-xs" onClick={() => setConfirming(true)}>
              Cancel
            </button>
          )}
          <Link to="/agents" className="btn-secondary text-xs">Open Pipeline runs</Link>
          <button type="button" className="btn-secondary text-xs" onClick={onClose}>Close</button>
        </div>
      </div>
    </InlineFormSection>
  )
}
