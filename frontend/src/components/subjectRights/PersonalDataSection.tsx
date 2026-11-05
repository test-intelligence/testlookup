/**
 * PersonalDataSection — a Users row's "Personal data" (W31 STRAT-12, contract §4.1, §4.2), opened in the row
 * (`InlineRowSection`, the reset-link precedent) by the row's FileLock2 action. ADMIN only.
 *
 *   - Export: "Export this person's data" → `POST /api/v1/users/{id}/data-export` → the job polls → Download (the
 *     job's own download route). The newest export comes from `GET …/data-jobs`, read ONLY while the section is open.
 *   - Erase: `<ErasePanel />` (preview first, the typed confirm, the phase line).
 *   - An erased row: the erasure's outcome and its counts, read-only — no Export, no Erase (a resumable erasure
 *     still shows the panel's "Resume erasure").
 *
 * Layout `flex flex-col gap-4` (Tailwind v4: `space-y-*` loses to the children's `m-0`). No modal; React text only.
 */
import { useEffect, useState } from 'react'
import { Download } from 'lucide-react'

import InlineFormSection from '@/components/ui/inPage/InlineFormSection'
import type { InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useSubjectJob, useUserJobs } from '@/hooks/useSubjectRights'
import {
  runningSubjectExportJobId,
  subjectRightsErrorMessage,
  subjectRightsService,
} from '@/services/subjectRightsService'
import { isActiveJob, type SubjectJob } from '@/types/subjectRights'
import ErasePanel from './ErasePanel'
import {
  ERASED_COUNTS_TITLE,
  ERASED_NO_JOB,
  erasedLine,
  erasedName,
  erasureCountLines,
  isErasedUser,
  sectionTitle,
  type UserRowLike,
} from './erasureModel'
import {
  DOWNLOAD_FAILED,
  DOWNLOAD_LABEL,
  EXPORT_PERSON,
  exportView,
  LOAD_JOBS_FAILED,
  newestErasure,
  newestExport,
  START_FAILED,
  watchedJobId,
} from './myDataModel'

/** The Export block: start an export of this person, watch it, download it. */
function ExportBlock({ userId, latest, loadError, onChanged }: {
  userId: string
  latest: SubjectJob | null | undefined
  loadError: unknown
  onChanged: () => void
}) {
  const [startedId, setStartedId] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)

  const watchedId = watchedJobId(startedId, latest)
  const { data: watched } = useSubjectJob(watchedId)
  const polled = watched && watched.id === watchedId ? watched : null
  const job = polled ?? (startedId ? { id: startedId, kind: 'export', status: 'queued', source: 'admin', requested_at: '' } : latest ?? null)
  const view = exportView(job, { audience: 'person' })

  const ended = polled && !isActiveJob(polled) ? `${polled.id}:${polled.status}` : null
  useEffect(() => {
    if (ended) onChanged()
  }, [ended, onChanged])

  async function start() {
    if (starting) return
    setStarting(true)
    setRefusal(null)
    try {
      const created = await subjectRightsService.startUserExport(userId)
      setStartedId(created.id)
      onChanged()
    } catch (err: unknown) {
      const running = runningSubjectExportJobId(err)
      if (running) setStartedId(running)
      else setRefusal(await subjectRightsErrorMessage(err, START_FAILED))
    } finally {
      setStarting(false)
    }
  }

  async function download() {
    if (!job?.id || downloading) return
    setDownloading(true)
    setRefusal(null)
    try {
      await subjectRightsService.downloadExport(job.id)
    } catch (err: unknown) {
      setRefusal(await subjectRightsErrorMessage(err, DOWNLOAD_FAILED))
      onChanged()
    } finally {
      setDownloading(false)
    }
  }

  const loading = latest === undefined && !loadError && !startedId
  const listFailed = Boolean(loadError) && !startedId
  return (
    <div className="flex flex-col gap-2" data-person-export="" data-export-state={loading ? 'loading' : listFailed ? 'error' : view.kind}>
      <div className="flex items-center gap-3">
        <button
          type="button"
          className="btn-secondary inline-flex items-center gap-1.5 disabled:cursor-not-allowed disabled:opacity-50"
          onClick={start}
          disabled={view.startDisabled || starting || loading}
        >
          {starting && <LoadingSpinner size="sm" />}
          {EXPORT_PERSON}
        </button>
        {view.download && !listFailed && (
          <button
            type="button"
            className="btn-secondary inline-flex items-center gap-1.5 disabled:cursor-not-allowed disabled:opacity-50"
            onClick={download}
            disabled={downloading}
          >
            {downloading ? <LoadingSpinner size="sm" /> : <Download className="h-4 w-4" aria-hidden="true" />}
            {DOWNLOAD_LABEL}
          </button>
        )}
      </div>
      {listFailed ? (
        <p className="m-0 text-xs text-[var(--ui-danger-ink)]">{LOAD_JOBS_FAILED}</p>
      ) : loading ? (
        <LoadingSpinner size="sm" />
      ) : (
        <div
          data-person-export-line=""
          className={`m-0 flex items-center gap-2 text-xs ${view.kind === 'failed' ? 'text-[var(--ui-danger-ink)]' : 'text-[var(--color-text)]'}`}
        >
          {view.kind === 'running' && <LoadingSpinner size="sm" />}
          <span>{view.line}</span>
        </div>
      )}
      {refusal && (
        <p role="alert" className="m-0 text-xs text-[var(--ui-danger-ink)]" data-person-export-refusal="">
          {refusal}
        </p>
      )}
    </div>
  )
}

/** An erased row: the finished erasure's line and its per-location counts, read-only. */
function ErasedOutcome({ job, pseudonym }: { job: SubjectJob | null; pseudonym: string }) {
  const { lines, untouched } = erasureCountLines(job)
  return (
    <div className="flex flex-col gap-2" data-erased-outcome="">
      <p className="m-0 text-sm text-[var(--color-text)]">{job ? erasedLine(pseudonym) : ERASED_NO_JOB}</p>
      {lines.length > 0 && (
        <div className="flex flex-col gap-1">
          <h4 className="m-0 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">{ERASED_COUNTS_TITLE}</h4>
          <ul className="m-0 flex list-none flex-col gap-0.5 p-0">
            {lines.map((line) => (
              <li key={line.key} className="flex max-w-md items-baseline justify-between gap-3 text-xs text-[var(--color-text)]" data-erased-count={line.key}>
                <span className="min-w-0">{line.label}</span>
                <span className="shrink-0 tabular-nums">{line.count}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {untouched && <p className="m-0 text-xs text-[var(--color-text-muted)]">{untouched}</p>}
    </div>
  )
}

interface Props {
  user: UserRowLike
  onClose: () => void
  /** Re-read the users list (the row turns Inactive after the lock-out, Erased at the end). */
  onUsersChanged: () => void
  slot?: InlineSlot
}

export default function PersonalDataSection({ user, onClose, onUsersChanged, slot }: Props) {
  const erased = isErasedUser(user)
  const { data: jobs, error: jobsError, mutate: refreshJobs } = useUserJobs(user.id)
  const latestErasure = newestErasure(jobs)
  const latestExport = newestExport(jobs)
  const exportState = latestExport ? `${latestExport.id}:${latestExport.status}` : null
  const erasureFinished = Boolean(latestErasure && (latestErasure.phase === 'done' || latestErasure.status === 'succeeded'))
  const display = erased ? erasedName(user) : user.full_name?.trim() || user.username

  const panel = (
    <ErasePanel
      user={user}
      latestErasure={latestErasure}
      onChanged={refreshJobs}
      onUsersChanged={onUsersChanged}
      exportState={exportState}
    />
  )
  let erasure
  if (!erased) {
    erasure = panel
  } else if (jobs === undefined && !jobsError) {
    erasure = <LoadingSpinner size="sm" />
  } else if (latestErasure && !erasureFinished) {
    erasure = panel
  } else {
    erasure = <ErasedOutcome job={latestErasure} pseudonym={user.username} />
  }

  return (
    <InlineFormSection
      title={sectionTitle(display)}
      titleId={`personal-data-${user.id}`}
      onClose={onClose}
      slot={slot}
      dataAttributes={{ 'data-personal-data': user.id }}
    >
      <div className="flex flex-col gap-4">
        {!erased && (
          <ExportBlock userId={user.id} latest={latestExport} loadError={jobsError} onChanged={refreshJobs} />
        )}
        {erasure}
      </div>
    </InlineFormSection>
  )
}
