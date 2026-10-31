/**
 * W24 ADM-05 — the Export card: each state, Export → POST then polling, a 409 `export_running` resumes that
 * job, the download's refusal read OUT OF THE BLOB, the load error sentence. The job hook and the service are
 * mocked (the unit-test network guard).
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ProjectExportCard from './ProjectExportCard'
import { ERROR_SENTENCES, EXPORT_QUEUED, SIGNED_HERE, UI_SENTENCES } from './projectDataModel'

const jobs: Record<string, unknown> = {}
const mockUseJob = vi.fn((id: string | null) => ({ data: id ? jobs[id] : undefined }))
vi.mock('@/hooks/useProjectDataJobs', () => ({
  useProjectDataJob: (id: string | null) => mockUseJob(id),
}))

const mockStart = vi.fn()
const mockDownload = vi.fn()
// The requests are mocked; the refusal readers (`projectDataErrorMessage`, `runningExportJobId`) stay real.
vi.mock('@/services/projectDataService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/projectDataService')>()),
  projectDataService: {
    startExport: (...args: unknown[]) => mockStart(...args),
    downloadExport: (...args: unknown[]) => mockDownload(...args),
  },
}))

const MB = 1024 * 1024
const onChanged = vi.fn()

function readyJob(id = 'job-ready') {
  return {
    id,
    kind: 'export',
    status: 'succeeded',
    bytes: Math.round(2.1 * MB),
    counts: { test_plans: 30, releases: 4 },
    signature: 'signed',
    expires_at: new Date(Date.now() + 72 * 3_600_000).toISOString(),
    download_path: `/api/v1/project-data-jobs/${id}/download`,
  }
}

function renderCard(props: Partial<Parameters<typeof ProjectExportCard>[0]> = {}) {
  return render(<ProjectExportCard projectId="proj-1" latest={null} onChanged={onChanged} {...props} />)
}

const exportButton = () => screen.getByRole('button', { name: 'Export' })

describe('ProjectExportCard', () => {
  beforeEach(() => {
    for (const key of Object.keys(jobs)) delete jobs[key]
    mockUseJob.mockClear()
    mockStart.mockReset()
    mockDownload.mockReset()
    onChanged.mockReset()
  })

  it('no export yet: the sentence, an enabled Export, and no poll without a job id', () => {
    renderCard()
    expect(screen.getByRole('heading', { name: UI_SENTENCES.export_heading })).toBeInTheDocument()
    expect(screen.getByText(UI_SENTENCES.export_intro)).toBeInTheDocument()
    expect(screen.getByText('No export of this project yet.')).toBeInTheDocument()
    expect(exportButton()).toBeEnabled()
    expect(mockUseJob.mock.calls.every(([id]) => id === null)).toBe(true)
  })

  it('while the page read is in flight, Export waits; a failed read says so', () => {
    const { rerender } = renderCard({ latest: undefined })
    expect(exportButton()).toBeDisabled()
    rerender(<ProjectExportCard projectId="proj-1" latest={undefined} loadError={new Error('x')} onChanged={onChanged} />)
    expect(screen.getByText("Could not load this project's exports.")).toBeInTheDocument()
    expect(exportButton()).toBeEnabled()
  })

  it('Export → one POST, then the job polls: "Exporting… 12 of 30 tables" with Export disabled', async () => {
    mockStart.mockResolvedValue({ id: 'job-new', kind: 'export', status: 'queued' })
    const { rerender } = renderCard()
    fireEvent.click(exportButton())
    await waitFor(() => expect(mockUseJob).toHaveBeenLastCalledWith('job-new'))
    expect(mockStart).toHaveBeenCalledTimes(1)
    expect(mockStart).toHaveBeenCalledWith('proj-1')
    expect(onChanged).toHaveBeenCalled()
    expect(screen.getByText(EXPORT_QUEUED)).toBeInTheDocument()

    jobs['job-new'] = { id: 'job-new', kind: 'export', status: 'running', progress: { done: 12, total: 30 } }
    rerender(<ProjectExportCard projectId="proj-1" latest={null} onChanged={onChanged} />)
    expect(screen.getByText('Exporting… 12 of 30 tables')).toBeInTheDocument()
    expect(exportButton()).toBeDisabled()

    onChanged.mockClear()
    jobs['job-new'] = readyJob('job-new')
    rerender(<ProjectExportCard projectId="proj-1" latest={null} onChanged={onChanged} />)
    expect(screen.getByRole('button', { name: 'Download (2.1 MB)' })).toBeInTheDocument()
    expect(onChanged, 'the finished job refreshes the list').toHaveBeenCalledTimes(1)
  })

  it('a 409 export_running resumes the running job (no error line)', async () => {
    jobs['job-running'] = { id: 'job-running', kind: 'export', status: 'running', progress: { done: 3, total: 30 } }
    mockStart.mockRejectedValue(
      Object.assign(new Error('Request failed with status code 409'), { isAxiosError: true,
        response: { status: 409, data: { detail: { code: 'export_running', message: ERROR_SENTENCES.export_running, job_id: 'job-running' } } },
      }),
    )
    renderCard()
    fireEvent.click(exportButton())
    expect(await screen.findByText('Exporting… 3 of 30 tables')).toBeInTheDocument()
    expect(mockUseJob).toHaveBeenLastCalledWith('job-running')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it("another refusal reads as the server's sentence", async () => {
    mockStart.mockRejectedValue(
      Object.assign(new Error('Request failed with status code 403'), { isAxiosError: true,
        response: { status: 403, data: { detail: 'You do not have access to this project' } },
      }),
    )
    renderCard()
    fireEvent.click(exportButton())
    expect(await screen.findByRole('alert')).toHaveTextContent('You do not have access to this project')
    expect(screen.queryByText(/Request failed/)).toBeNull()
  })

  it('ready: rows · tables, the signature + "Available until …", Download (2.1 MB) → the blob download', async () => {
    mockDownload.mockResolvedValue('checkout-export.zip')
    renderCard({ latest: readyJob() as never })
    expect(screen.getByText('34 rows in 2 tables')).toBeInTheDocument()
    expect(screen.getByText(SIGNED_HERE)).toBeInTheDocument()
    expect(screen.getByText(/^Available until .+\.$/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Export again' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: 'Download (2.1 MB)' }))
    await waitFor(() => expect(mockDownload).toHaveBeenCalledWith('job-ready'))
    expect(mockUseJob.mock.calls.every(([id]) => id === null), 'a finished export is not polled').toBe(true)
  })

  it('a refused download reads its sentence OUT OF THE BLOB, never "Request failed with status code 410"', async () => {
    const body = JSON.stringify({ detail: { code: 'export_expired', message: ERROR_SENTENCES.export_expired } })
    mockDownload.mockRejectedValue(
      Object.assign(new Error('Request failed with status code 410'), { isAxiosError: true,
        response: { status: 410, data: new Blob([body], { type: 'application/json' }) },
      }),
    )
    renderCard({ latest: readyJob() as never })
    fireEvent.click(screen.getByRole('button', { name: 'Download (2.1 MB)' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('This export was removed after 72 hours; start a new one.')
    expect(screen.queryByText(/Request failed/)).toBeNull()
    expect(onChanged).toHaveBeenCalled()
  })

  it('a reload mid-export resumes polling the newest job while it runs', () => {
    const running = { id: 'job-old', kind: 'export', status: 'running', progress: { done: 7, total: 30 } }
    jobs['job-old'] = running
    renderCard({ latest: running as never })
    expect(mockUseJob).toHaveBeenLastCalledWith('job-old')
    expect(screen.getByText('Exporting… 7 of 30 tables')).toBeInTheDocument()
  })

  it("failed: the job's sentence and Export enabled; expired: the 72-hour sentence", () => {
    const sentence = 'The export could not be built (OperationalError).'
    const { rerender } = renderCard({ latest: { id: 'f', kind: 'export', status: 'failed', error: { code: 'export_build', message: sentence } } as never })
    expect(screen.getByText(sentence)).toBeInTheDocument()
    expect(exportButton()).toBeEnabled()
    rerender(<ProjectExportCard projectId="proj-1" latest={{ id: 'e', kind: 'export', status: 'expired' } as never} onChanged={onChanged} />)
    expect(screen.getByText(ERROR_SENTENCES.export_expired)).toBeInTheDocument()
    expect(exportButton()).toBeEnabled()
  })

  it('names and sentences are text, never markup', () => {
    const hostile = '<img src=x onerror=alert(1)>'
    renderCard({ latest: { id: 'f', kind: 'export', status: 'failed', error: { code: 'x', message: hostile } } as never })
    expect(screen.getByText(hostile)).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
  })
})
