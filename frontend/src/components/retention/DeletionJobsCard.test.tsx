/**
 * W22b ACT-03 — the Deletion jobs card on /settings/retention.
 *
 * The jobs hook is replaced by a world; collect / acknowledge are mocked at
 * the service. Pins (§4.4): each status label; the ratio and the retained
 * net; `not measured` for an unmeasured reclaim; the load error (never an
 * empty list from a failed read); collect → download → size check →
 * acknowledge with `{manifest_sha256}` of THAT download; an incomplete
 * download keeps `I have saved it` disabled; a 409 sentence inline.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  awaitingJobRow,
  EXPORT_PROJECT_ID,
  expiredJobRow,
  failedExportJobRow,
  jobList,
  jobRow,
  MOCK_MANIFEST_SHA256,
  retainedJobRow,
} from '@/test/retentionExportMocks'
import { ARCHIVE_NOT_BACKUP, EXPORT_ERROR_SENTENCES } from '@/types/retentionExport'
import type { DeletionJob, DeletionJobList } from '@/types/retention'

const hook = vi.hoisted(() => ({
  world: {} as { data?: unknown; error?: unknown; isLoading?: boolean },
  mutate: vi.fn(),
}))
vi.mock('@/hooks/useDeletionJobs', () => ({
  useDeletionJobs: () => ({
    data: hook.world.data,
    error: hook.world.error,
    isLoading: Boolean(hook.world.isLoading),
    mutate: hook.mutate,
  }),
}))
const service = vi.hoisted(() => ({
  criteriaDeletionService: { collect: vi.fn(), acknowledge: vi.fn() },
  deletionArchiveErrorMessage: vi.fn(),
}))
vi.mock('@/services/retentionService', () => service)
const download = vi.hoisted(() => ({ downloadBlob: vi.fn() }))
vi.mock('@/utils/download', () => download)

import DeletionJobsCard from './DeletionJobsCard'

function answer(rows: DeletionJob[], total?: number) {
  hook.world = { data: jobList(rows, total) satisfies DeletionJobList }
}

function apiError(status: number, detail: unknown) {
  return Object.assign(new Error('request failed'), { isAxiosError: true, response: { status, data: { detail } } })
}

function row(id: string) {
  const el = document.querySelector(`[data-deletion-job="${id}"]`)
  if (!el) throw new Error(`no row ${id}`)
  return el as HTMLElement
}

beforeEach(() => {
  hook.world = {}
  hook.mutate.mockReset()
  service.criteriaDeletionService.collect.mockReset()
  service.criteriaDeletionService.acknowledge.mockReset()
  service.deletionArchiveErrorMessage.mockReset()
  download.downloadBlob.mockReset()
})

describe('DeletionJobsCard', () => {
  it('labels every status; previewed rows are hidden; Showing n of total', () => {
    const statuses = ['running', 'awaiting_collection', 'completed', 'partial', 'failed', 'expired', 'queued']
    const rows = statuses.map((status) => jobRow({ status: status as DeletionJob['status'] }))
    answer([...rows, jobRow({ status: 'previewed' })], 20)
    render(<DeletionJobsCard projectId={EXPORT_PROJECT_ID} />)
    const chips = Array.from(document.querySelectorAll('[data-deletion-job-status]')).map((el) => el.textContent)
    expect(chips).toEqual(['Running', 'Awaiting collection', 'Completed', 'Partial', 'Failed', 'Expired', 'Queued'])
    expect(screen.getByText('Showing 7 of 20')).toBeInTheDocument()
  })

  it('reads the archive, the retained net and the ratio; an unmeasured reclaim never as 0', () => {
    const retained = retainedJobRow({ bytes_reclaimed: 182 * 1024 * 1024 })
    const unmeasured = jobRow({ bytes_reclaimed: null })
    answer([retained, unmeasured])
    render(<DeletionJobsCard projectId={EXPORT_PROJECT_ID} />)
    const r = within(row(retained.id))
    expect(r.getByText('Full · retain')).toBeInTheDocument()
    expect(r.getByText('verified')).toBeInTheDocument()
    expect(r.getByText('182 MB')).toBeInTheDocument()
    expect(r.getByText('net 168 MB · ratio 1:13')).toBeInTheDocument()
    expect(within(row(unmeasured.id)).getByText('not measured')).toBeInTheDocument()
    expect(within(row(unmeasured.id)).getByText('—')).toBeInTheDocument()
  })

  it('shows a failed export and an expired job with their sentences', () => {
    const failed = failedExportJobRow()
    const expired = expiredJobRow()
    answer([failed, expired])
    render(<DeletionJobsCard projectId={EXPORT_PROJECT_ID} />)
    expect(screen.getByText(/^Export failed — nothing was deleted/)).toBeInTheDocument()
    expect(
      screen.getByText('Not collected within 72 hours — nothing was deleted; the archive was removed.'),
    ).toBeInTheDocument()
  })

  it('shows the load error with Retry, never an empty list', () => {
    hook.world = { error: new Error('boom') }
    render(<DeletionJobsCard projectId={EXPORT_PROJECT_ID} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Could not load the deletion jobs.')
    expect(screen.queryByText('No deletion jobs yet.')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }))
    expect(hook.mutate).toHaveBeenCalled()
  })

  it('reads a list without jobs as empty only when the server answered', () => {
    answer([])
    render(<DeletionJobsCard projectId={EXPORT_PROJECT_ID} />)
    expect(screen.getByText('No deletion jobs yet.')).toBeInTheDocument()
  })
})

describe('collect → download → acknowledge', () => {
  const SIZE = 1234

  function openCollect(job: DeletionJob) {
    answer([job])
    render(<DeletionJobsCard projectId={EXPORT_PROJECT_ID} />)
    fireEvent.click(within(row(job.id)).getByRole('button', { name: 'Collect' }))
    const section = document.querySelector(`[data-deletion-job-collect="${job.id}"]`) as HTMLElement
    expect(section).not.toBeNull()
    return within(section)
  }

  it('downloads, checks the size, then acknowledges with the manifest of that download', async () => {
    const job = awaitingJobRow({ export_bytes: SIZE })
    const blob = new Blob([new Uint8Array(SIZE)])
    service.criteriaDeletionService.collect.mockResolvedValue({
      blob,
      manifestSha256: MOCK_MANIFEST_SHA256,
      archiveSha256: 'beef'.repeat(16),
      filename: 'testlookup-deletion-archive-x.zip',
    })
    service.criteriaDeletionService.acknowledge.mockResolvedValue({ ...job, status: 'queued' })
    const section = openCollect(job)
    expect(section.getByText(ARCHIVE_NOT_BACKUP)).toBeInTheDocument()
    expect(section.getByText(/^Collect by .* — after that nothing is deleted and the archive is removed\.$/)).toBeInTheDocument()
    const saved = section.getByRole('button', { name: 'I have saved it' })
    expect(saved).toBeDisabled()
    fireEvent.click(section.getByRole('button', { name: /^Download archive/ }))
    await waitFor(() => expect(saved).toBeEnabled())
    expect(service.criteriaDeletionService.collect).toHaveBeenCalledWith(EXPORT_PROJECT_ID, job.id)
    expect(download.downloadBlob).toHaveBeenCalledWith(blob, 'testlookup-deletion-archive-x.zip')
    fireEvent.click(saved)
    await waitFor(() =>
      expect(service.criteriaDeletionService.acknowledge).toHaveBeenCalledWith(
        EXPORT_PROJECT_ID,
        job.id,
        MOCK_MANIFEST_SHA256,
      ),
    )
    await waitFor(() => expect(hook.mutate).toHaveBeenCalled())
  })

  it('keeps "I have saved it" disabled after an incomplete download', async () => {
    const job = awaitingJobRow({ export_bytes: SIZE })
    service.criteriaDeletionService.collect.mockResolvedValue({
      blob: new Blob([new Uint8Array(SIZE - 10)]),
      manifestSha256: MOCK_MANIFEST_SHA256,
      archiveSha256: null,
      filename: null,
    })
    const section = openCollect(job)
    fireEvent.click(section.getByRole('button', { name: /^Download archive/ }))
    expect(await section.findByText('The download was incomplete; download it again.')).toBeInTheDocument()
    expect(section.getByRole('button', { name: 'I have saved it' })).toBeDisabled()
    // The fallback name is built from the id, never from anything else.
    expect(download.downloadBlob).toHaveBeenCalledWith(expect.any(Blob), `deletion-archive-${job.id.slice(0, 8)}.zip`)
  })

  it('shows a collect refusal inline (the Blob body read by the service helper)', async () => {
    const job = awaitingJobRow({ export_bytes: SIZE })
    service.criteriaDeletionService.collect.mockRejectedValue(apiError(409, null))
    service.deletionArchiveErrorMessage.mockResolvedValue(EXPORT_ERROR_SENTENCES.collect_expired)
    const section = openCollect(job)
    fireEvent.click(section.getByRole('button', { name: /^Download archive/ }))
    expect(await section.findByRole('alert')).toHaveTextContent(EXPORT_ERROR_SENTENCES.collect_expired)
    expect(section.getByRole('button', { name: 'I have saved it' })).toBeDisabled()
  })

  it('shows a 409 manifest_mismatch sentence inline after acknowledge', async () => {
    const job = awaitingJobRow({ export_bytes: SIZE })
    service.criteriaDeletionService.collect.mockResolvedValue({
      blob: new Blob([new Uint8Array(SIZE)]),
      manifestSha256: MOCK_MANIFEST_SHA256,
      archiveSha256: null,
      filename: null,
    })
    const sentence = EXPORT_ERROR_SENTENCES.manifest_mismatch
    service.criteriaDeletionService.acknowledge.mockRejectedValue(
      apiError(409, { code: 'manifest_mismatch', message: sentence }),
    )
    const section = openCollect(job)
    fireEvent.click(section.getByRole('button', { name: /^Download archive/ }))
    const saved = section.getByRole('button', { name: 'I have saved it' })
    await waitFor(() => expect(saved).toBeEnabled())
    fireEvent.click(saved)
    expect(await section.findByRole('alert')).toHaveTextContent(sentence)
    expect(hook.mutate).not.toHaveBeenCalled()
  })
})
