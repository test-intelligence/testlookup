/**
 * DeletionJobsCard — the project's deletion jobs (W22b ACT-03; the "job list"
 * the criteria toast always promised, §0.11).
 *
 * A W29 table: Job (when / kind and run count), Status (a chip through the
 * ONE status vocabulary), Archive (tier · target / verified), Reclaimed (the
 * exact bytes or `not measured`, the retained net, the archive ratio). A
 * failed / expired job shows the server's sentence under its row.
 *
 * An `awaiting_collection` job has `Collect`, which opens an in-page row:
 * `Download archive` streams the archive through the API and saves it
 * (`downloadBlob`); `I have saved it` is enabled only after a download in
 * this session whose blob size equals `export_bytes`; it acknowledges with
 * the `x-manifest-sha256` of THAT download. Refusals render inline.
 *
 * Safety: no storage key, presigned URL or token is ever in a job answer or
 * in the DOM — the archive arrives as a Blob from an authenticated POST. A
 * failed read shows the load-error sentence, never an empty list.
 */
import { useEffect, useState } from 'react'
import { History, RefreshCw } from 'lucide-react'
import { isAxiosError } from 'axios'
import { useDeletionJobs } from '@/hooks/useDeletionJobs'
import { criteriaDeletionService, deletionArchiveErrorMessage } from '@/services/retentionService'
import { extractErrorMessage } from '@/services/apiErrors'
import { InlineRowSection } from '@/components/ui/inPage/InlineRowSection'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { Table, Td, Th, Tr } from '@/components/ui/Table'
import type { DeletionJob } from '@/types/retention'
import { downloadBlob } from '@/utils/download'
import { formatCompactDateTime } from '@/utils/formatters'
import {
  ARCHIVE_NOT_BACKUP,
  archiveLines,
  collectByLine,
  COLLECT_LABEL,
  DOWNLOAD_INCOMPLETE,
  downloadComplete,
  downloadLabel,
  fallbackArchiveName,
  jobErrorLine,
  jobKindLine,
  JOBS_EMPTY,
  JOBS_HEADING,
  JOBS_LOAD_ERROR,
  jobStatusLabel,
  jobStatusTone,
  reclaimedLines,
  SAVED_LABEL,
  showingLine,
  toneStyle,
} from './exportModel'
import { formatLocalDateTime } from './scheduleModel'

const COLUMNS = 4
const ACK_FALLBACK = 'Could not confirm the archive was saved.'

interface CollectRowProps {
  projectId: string
  job: DeletionJob
  onAcknowledged: (job: DeletionJob) => void
}

/** The in-page collect section of one awaiting job. */
function CollectRow({ projectId, job, onAcknowledged }: CollectRowProps) {
  const [busy, setBusy] = useState<'download' | 'acknowledge' | null>(null)
  // The manifest digest of a COMPLETE download in this session; null until then.
  const [manifest, setManifest] = useState<string | null>(null)
  const [incomplete, setIncomplete] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleDownload() {
    if (busy) return
    setBusy('download')
    setError(null)
    setIncomplete(false)
    try {
      const result = await criteriaDeletionService.collect(projectId, job.id)
      downloadBlob(result.blob, result.filename || fallbackArchiveName(job.id))
      if (downloadComplete(result.blob, job) && result.manifestSha256) {
        setManifest(result.manifestSha256)
      } else {
        setManifest(null)
        setIncomplete(true)
      }
    } catch (err) {
      setManifest(null)
      setError(await deletionArchiveErrorMessage(err))
    } finally {
      setBusy(null)
    }
  }

  async function handleAcknowledge() {
    if (busy || !manifest) return
    setBusy('acknowledge')
    setError(null)
    try {
      const saved = await criteriaDeletionService.acknowledge(projectId, job.id, manifest)
      onAcknowledged(saved)
    } catch (err) {
      const detail = isAxiosError(err)
        ? (err.response?.data as { detail?: unknown } | undefined)?.detail
        : undefined
      setError(extractErrorMessage(detail, ACK_FALLBACK))
    } finally {
      setBusy(null)
    }
  }

  const collectBy = collectByLine(job, formatLocalDateTime)
  return (
    <div className="space-y-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => void handleDownload()}
          disabled={busy !== null}
          className="btn-secondary btn-sm inline-flex items-center gap-1.5"
        >
          {busy === 'download' && <LoadingSpinner size="sm" />}
          {downloadLabel(job)}
        </button>
        <button
          type="button"
          onClick={() => void handleAcknowledge()}
          disabled={busy !== null || manifest === null}
          className="btn-primary btn-sm inline-flex items-center gap-1.5 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy === 'acknowledge' && <LoadingSpinner size="sm" />}
          {SAVED_LABEL}
        </button>
      </div>
      {incomplete && (
        <p role="alert" data-deletion-job-incomplete="" className="text-[var(--ui-danger-ink)]">
          {DOWNLOAD_INCOMPLETE}
        </p>
      )}
      {error && (
        <p role="alert" data-deletion-job-collect-error="" className="text-[var(--ui-danger-ink)]">
          {error}
        </p>
      )}
      {collectBy && <p className="text-[var(--color-text-muted)]">{collectBy}</p>}
      <p className="text-[var(--color-text-muted)]">{ARCHIVE_NOT_BACKUP}</p>
    </div>
  )
}

/** Two stacked lines of a cell: the value, then a muted second line. */
function TwoLines({ primary, secondary }: { primary: string; secondary?: string | null }) {
  return (
    <>
      <span className="block text-[var(--color-text)]">{primary}</span>
      {secondary && <span className="block text-xs text-[var(--color-text-muted)]">{secondary}</span>}
    </>
  )
}

export interface DeletionJobsCardProps {
  projectId: string
  /** Bumped by the page after a criteria execute: the list re-reads (a settled list does not poll). */
  refreshSignal?: number
}

export default function DeletionJobsCard({ projectId, refreshSignal = 0 }: DeletionJobsCardProps) {
  const { data, error, isLoading, mutate } = useDeletionJobs(projectId)
  const [collectingId, setCollectingId] = useState<string | null>(null)

  useEffect(() => {
    if (refreshSignal > 0) void mutate()
  }, [refreshSignal, mutate])
  // Absence is not health: only a list the server answered is a list.
  const answered = !error && Array.isArray(data?.jobs)
  const jobs = answered && data ? data.jobs.filter((job) => job.status !== 'previewed') : []
  const total = answered && typeof data?.total === 'number' ? data.total : null

  async function acknowledged(saved: DeletionJob) {
    setCollectingId(null)
    await mutate(
      (current) =>
        current && Array.isArray(current.jobs)
          ? { ...current, jobs: current.jobs.map((job) => (job.id === saved.id ? saved : job)) }
          : current,
      { revalidate: true },
    )
  }

  return (
    <section data-deletion-jobs="" aria-labelledby="deletion-jobs-heading" className="card space-y-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <div className="flex items-center gap-2">
          <History aria-hidden="true" className="h-4 w-4 text-[var(--color-accent)]" />
          <h2
            id="deletion-jobs-heading"
            className="text-sm font-semibold uppercase tracking-wider text-[var(--color-text)]"
          >
            {JOBS_HEADING}
          </h2>
        </div>
        {total !== null && jobs.length > 0 && (
          <span data-deletion-jobs-total="" className="ml-auto text-xs text-[var(--color-text-muted)]">
            {showingLine(jobs.length, total)}
          </span>
        )}
      </div>

      {isLoading && !answered && !error && (
        <div className="flex justify-center py-3">
          <LoadingSpinner size="sm" />
        </div>
      )}

      {error && (
        <div role="alert" data-deletion-jobs-error="" className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-[var(--ui-danger-ink)]">{JOBS_LOAD_ERROR}</span>
          <button
            type="button"
            onClick={() => void mutate()}
            className="btn-secondary btn-sm inline-flex items-center gap-1.5"
          >
            <RefreshCw aria-hidden="true" className="h-3 w-3" /> Retry
          </button>
        </div>
      )}

      {answered && jobs.length === 0 && (
        <p data-deletion-jobs-empty="" className="text-xs text-[var(--color-text-muted)]">
          {JOBS_EMPTY}
        </p>
      )}

      {answered && jobs.length > 0 && (
        <Table density="dense" className="text-xs">
          <thead>
            <tr>
              <Th>Job</Th>
              <Th>Status</Th>
              <Th>Archive</Th>
              <Th>Reclaimed</Th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <JobRows
                key={job.id}
                projectId={projectId}
                job={job}
                collecting={collectingId === job.id}
                onCollect={() => setCollectingId(collectingId === job.id ? null : job.id)}
                onAcknowledged={(saved) => void acknowledged(saved)}
              />
            ))}
          </tbody>
        </Table>
      )}
    </section>
  )
}

interface JobRowsProps {
  projectId: string
  job: DeletionJob
  collecting: boolean
  onCollect: () => void
  onAcknowledged: (job: DeletionJob) => void
}

/** One job: its row, its error line, and (while collecting) its collect row. */
function JobRows({ projectId, job, collecting, onCollect, onAcknowledged }: JobRowsProps) {
  const archive = archiveLines(job)
  const reclaimed = reclaimedLines(job)
  const errorLine = jobErrorLine(job)
  const awaiting = job.status === 'awaiting_collection'
  return (
    <>
      <Tr data-deletion-job={job.id} data-status={job.status}>
        <Td>
          <TwoLines primary={formatCompactDateTime(job.requested_at)} secondary={jobKindLine(job)} />
        </Td>
        <Td>
          <span
            data-deletion-job-status=""
            className="inline-block rounded-full border px-2 py-0.5 text-xs whitespace-nowrap"
            style={toneStyle(jobStatusTone(job.status))}
          >
            {jobStatusLabel(job.status)}
          </span>
          {awaiting && (
            <button type="button" onClick={onCollect} className="btn-primary btn-sm mt-1 block">
              {COLLECT_LABEL}
            </button>
          )}
        </Td>
        <Td>
          <TwoLines primary={archive.primary} secondary={archive.secondary} />
        </Td>
        <Td>
          <TwoLines primary={reclaimed[0]} secondary={reclaimed.slice(1).join(' · ') || null} />
        </Td>
      </Tr>
      {errorLine && (
        <tr data-deletion-job-error={job.id}>
          <td colSpan={COLUMNS} className="px-3 pb-2 text-xs text-[var(--color-text-muted)]">
            <div style={{ width: 0, minWidth: '100%' }}>{errorLine}</div>
          </td>
        </tr>
      )}
      {awaiting && collecting && (
        <InlineRowSection colSpan={COLUMNS} dataAttributes={{ 'data-deletion-job-collect': job.id }}>
          <CollectRow projectId={projectId} job={job} onAcknowledged={onAcknowledged} />
        </InlineRowSection>
      )}
    </>
  )
}
