/**
 * W24 ADM-05 — the Export card: each state, Export → POST then polling, a 409 `export_running` resumes that
 * job, the download's refusal read OUT OF THE BLOB, the load error sentence. The job hook and the service are
 * mocked (the unit-test network guard).
 *
 * W30 (ADM-05 run history): unticked → W24's body-less POST and the estimate never asked for; ticked → the window
 * select, the estimate line (re-asked per window), over 2 GB disables Export, an unmeasured store reads "not
 * measured" (never 0); the phase line; a finished history export's counts line and the CLI hint above 512 MB.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ProjectExportCard from './ProjectExportCard'
import {
  ERROR_SENTENCES,
  EXPORT_QUEUED,
  INCLUDE_MEMBERS_LABEL,
  INCLUDE_REVIEWS_LABEL,
  SIGNED_HERE,
  UI_SENTENCES,
} from './projectDataModel'

const jobs: Record<string, unknown> = {}
const mockUseJob = vi.fn((id: string | null) => ({ data: id ? jobs[id] : undefined }))
type EstimateOptions = { historyDays: number | null }
const estimateRead: { data?: unknown; error?: unknown } = {}
const mockUseEstimate = vi.fn((_projectId: string, _options: EstimateOptions, enabled: boolean) =>
  enabled ? { data: estimateRead.data, error: estimateRead.error } : { data: undefined, error: undefined },
)
vi.mock('@/hooks/useProjectDataJobs', () => ({
  useProjectDataJob: (id: string | null) => mockUseJob(id),
  useExportEstimate: (projectId: string, options: EstimateOptions, enabled: boolean) => mockUseEstimate(projectId, options, enabled),
}))

// W37: the top bar's cached release list (`useReleases(undefined, {cached: true})`).
const releasesRead: { data?: unknown } = {}
const mockUseReleases = vi.fn((..._args: unknown[]) => ({ data: releasesRead.data, isLoading: false, isValidating: false }))
vi.mock('@/hooks/useReleases', () => ({
  useReleases: (...args: unknown[]) => mockUseReleases(...args),
}))

const mockStart = vi.fn()
const mockDownload = vi.fn()
const mockDownloadLarge = vi.fn()
// The requests are mocked; the refusal readers (`projectDataErrorMessage`, `runningExportJobId`) stay real.
vi.mock('@/services/projectDataService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/projectDataService')>()),
  projectDataService: {
    startExport: (...args: unknown[]) => mockStart(...args),
    downloadExport: (...args: unknown[]) => mockDownload(...args),
    downloadLargeExport: (...args: unknown[]) => mockDownloadLarge(...args),
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
    mockUseEstimate.mockClear()
    estimateRead.data = undefined
    estimateRead.error = undefined
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

// ── W30: run history ─────────────────────────────────────────────────────────

const measured = { postgres: true, documents: true, objects: true }
function estimate(overrides: Record<string, unknown> = {}) {
  return {
    include_history: true, history_days: 90, runs: 412, runs_in_progress: 1, runs_over_cap: 0, test_cases: 9000,
    documents: { raw_allure_json: 9000, run_summaries: 120 }, objects: 1830, object_bytes: 380 * MB,
    estimated_bytes: Math.round(412.3 * MB), over_limit: false, measured, ...overrides,
  }
}
const historyBox = () => screen.getByRole('checkbox', { name: UI_SENTENCES.include_history })
const windowSelect = () => screen.getByRole('combobox')

describe('ProjectExportCard — run history (W30)', () => {
  beforeEach(() => {
    for (const key of Object.keys(jobs)) delete jobs[key]
    mockUseJob.mockClear()
    mockUseEstimate.mockClear()
    estimateRead.data = estimate()
    estimateRead.error = undefined
    mockStart.mockReset()
    onChanged.mockReset()
  })

  it('unticked: the box and the intro, no window, the estimate never asked for; Export → W24\'s body-less POST', async () => {
    mockStart.mockResolvedValue({ id: 'job-new', kind: 'export', status: 'queued' })
    renderCard()
    expect(historyBox()).not.toBeChecked()
    expect(screen.getByText(UI_SENTENCES.history_intro)).toBeInTheDocument()
    expect(screen.queryByRole('combobox')).toBeNull()
    fireEvent.click(exportButton())
    await waitFor(() => expect(mockStart).toHaveBeenCalledTimes(1))
    expect(mockStart.mock.calls[0], 'ONE argument: no body').toStrictEqual(['proj-1'])
    expect(mockUseEstimate.mock.calls.every(([, , enabled]) => enabled === false), 'no estimate while unticked').toBe(true)
  })

  it('ticked: 90 days by default and the estimate line; 365 days re-asks; Export sends the window', async () => {
    mockStart.mockResolvedValue({ id: 'job-history', kind: 'export', status: 'queued' })
    renderCard()
    fireEvent.click(historyBox())
    expect(windowSelect()).toHaveValue('90')
    // W37 (contract §3, deliberate): + "One release".
    expect([...windowSelect().querySelectorAll('option')].map((o) => o.textContent)).toEqual(['30 days', '90 days', '365 days', 'All history', 'One release'])
    expect(mockUseEstimate).toHaveBeenLastCalledWith('proj-1', { historyDays: 90 }, true)
    expect(screen.getByText('About 412.3 MB · 412 runs · 9,120 documents · 1,830 attachments')).toBeInTheDocument()

    fireEvent.change(windowSelect(), { target: { value: '365' } })
    expect(mockUseEstimate).toHaveBeenLastCalledWith('proj-1', { historyDays: 365 }, true)
    fireEvent.click(exportButton())
    await waitFor(() => expect(mockStart).toHaveBeenCalledTimes(1))
    expect(mockStart).toHaveBeenCalledWith('proj-1', { include_history: true, history_days: 365, max_runs: 2000 })

    fireEvent.change(windowSelect(), { target: { value: 'all' } })
    expect(mockUseEstimate).toHaveBeenLastCalledWith('proj-1', { historyDays: null }, true)
  })

  it('over 2 GB: the over sentence and Export disabled; unticking enables it again', () => {
    estimateRead.data = estimate({ estimated_bytes: 3 * 1024 * MB, over_limit: true })
    renderCard()
    fireEvent.click(historyBox())
    expect(screen.getByText('Larger than 2 GB — choose a shorter window.')).toBeInTheDocument()
    expect(exportButton()).toBeDisabled()
    fireEvent.click(historyBox())
    expect(exportButton()).toBeEnabled()
  })

  it('an unmeasured store reads "not measured", never 0, and Export stays enabled', () => {
    estimateRead.data = estimate({ documents: null, estimated_bytes: null, over_limit: null, measured: { ...measured, documents: false } })
    renderCard()
    fireEvent.click(historyBox())
    expect(screen.getByText('Size unknown: the document store could not be read.')).toBeInTheDocument()
    expect(screen.getByText('412 runs · documents not measured · 1,830 attachments')).toBeInTheDocument()
    expect(document.querySelector('[data-export-estimate]')?.textContent).not.toMatch(/\b0 documents/)
    expect(exportButton()).toBeEnabled()
  })

  it('while the estimate loads: "Estimating…"; a failed estimate says so and does not block Export', () => {
    estimateRead.data = undefined
    const { rerender } = renderCard()
    fireEvent.click(historyBox())
    expect(screen.getByText('Estimating…')).toBeInTheDocument()
    estimateRead.error = new Error('x')
    rerender(<ProjectExportCard projectId="proj-1" latest={null} onChanged={onChanged} />)
    expect(screen.getByText('Could not load the size estimate.')).toBeInTheDocument()
    expect(exportButton()).toBeEnabled()
  })

  it('a running history export reads its phase: "Runs and results… 120 of 412", then "Stored documents… 5,000 of 9,120"', () => {
    const running = { id: 'h', kind: 'export', status: 'running', options: { include_history: true },
      progress: { done: 29, total: 56, phase: 'runs', phase_done: 120, phase_total: 412 } }
    jobs.h = running
    const { rerender } = renderCard({ latest: running as never })
    expect(screen.getByText('Runs and results… 120 of 412')).toBeInTheDocument()
    jobs.h = { ...running, progress: { done: 56, total: 56, phase: 'documents', phase_done: 5000, phase_total: 9120 } }
    rerender(<ProjectExportCard projectId="proj-1" latest={running as never} onChanged={onChanged} />)
    expect(screen.getByText('Stored documents… 5,000 of 9,120')).toBeInTheDocument()
    expect(exportButton()).toBeDisabled()
  })

  it('a finished history export: the counts line, Download (412.3 MB), the CLI hint only above 512 MB', () => {
    const ready = { ...readyJob('h'), options: { include_history: true }, bytes: Math.round(412.3 * MB),
      counts: { projects: 1, test_runs: 412, test_cases: 9000, 'documents.raw_allure_json': 9000, 'documents.run_summaries': 120, objects: 1830 } }
    const { rerender } = renderCard({ latest: ready as never })
    expect(screen.getByText('9,413 rows in 3 tables')).toBeInTheDocument()
    expect(screen.getByText('412 runs · 9,120 documents · 1,830 attachments')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download (412.3 MB)' })).toBeEnabled()
    // W37 (contract §3, deliberate): the hint's new words.
    expect(screen.queryByText(/download straight to your disk/)).toBeNull()
    rerender(<ProjectExportCard projectId="proj-1" latest={{ ...ready, bytes: 600 * MB } as never} onChanged={onChanged} />)
    expect(screen.getByText('Large archives download straight to your disk; the CLI also works: testlookup projects export proj-1 --history')).toBeInTheDocument()
  })
})

// ── W37: one release, the include boxes, the large download ──────────────────

const RELEASES = [
  { id: 'rel-old', name: 'Winter 2026', status: 'released', is_active: false },
  { id: 'rel-next', name: 'Summer 2027', status: 'in_progress', is_active: false },
  { id: 'rel-now', name: 'Spring 2027', status: 'planned', is_active: true },
]
const membersBox = () => screen.getByRole('checkbox', { name: INCLUDE_MEMBERS_LABEL })
const reviewsBox = () => screen.getByRole('checkbox', { name: INCLUDE_REVIEWS_LABEL })
const releaseSelect = () => screen.getByRole('combobox', { name: 'Release' })

describe('ProjectExportCard — one release, members and reviews, the large download (W37)', () => {
  beforeEach(() => {
    for (const key of Object.keys(jobs)) delete jobs[key]
    mockUseJob.mockClear()
    mockUseEstimate.mockClear()
    mockUseReleases.mockClear()
    releasesRead.data = { items: RELEASES, total: RELEASES.length }
    estimateRead.data = estimate({ history_days: null, runs: 12 })
    estimateRead.error = undefined
    mockStart.mockReset()
    mockDownload.mockReset()
    mockDownloadLarge.mockReset()
    onChanged.mockReset()
  })

  it('"One release" shows a SECOND select of the cached releases (active first); no estimate and Export waits until one is chosen', async () => {
    mockStart.mockResolvedValue({ id: 'job-rel', kind: 'export', status: 'queued' })
    renderCard()
    fireEvent.click(historyBox())
    expect(screen.getAllByRole('combobox'), 'the default window keeps ONE combobox').toHaveLength(1)
    expect(mockUseReleases, 'no release read before "One release"').not.toHaveBeenCalled()

    fireEvent.change(windowSelect(), { target: { value: 'release' } })
    expect(screen.getAllByRole('combobox')).toHaveLength(2)
    expect(mockUseReleases).toHaveBeenLastCalledWith(undefined, { cached: true })
    expect([...releaseSelect().querySelectorAll('option')].map((o) => o.textContent)).toEqual([
      'Choose a release…', 'Spring 2027', 'Summer 2027', 'Winter 2026',
    ])
    expect(releaseSelect()).toHaveValue('')
    expect(mockUseEstimate, 'no estimate without a release').toHaveBeenLastCalledWith('proj-1', { historyDays: null, historyReleaseId: '' }, false)
    expect(document.querySelector('[data-export-estimate]')).toBeNull()
    expect(exportButton()).toBeDisabled()

    fireEvent.change(releaseSelect(), { target: { value: 'rel-next' } })
    expect(mockUseEstimate).toHaveBeenLastCalledWith('proj-1', { historyDays: null, historyReleaseId: 'rel-next' }, true)
    expect(screen.getByText(/^About 412\.3 MB · 12 runs/)).toBeInTheDocument()
    expect(exportButton()).toBeEnabled()
    fireEvent.click(exportButton())
    await waitFor(() => expect(mockStart).toHaveBeenCalledTimes(1))
    expect(mockStart.mock.calls[0]).toStrictEqual(['proj-1', { include_history: true, max_runs: 2000, history_release_id: 'rel-next' }])
  })

  it('a project with no releases says so; "Export again" also waits for a release', () => {
    releasesRead.data = { items: [], total: 0 }
    renderCard({ latest: readyJob() as never })
    fireEvent.click(historyBox())
    fireEvent.change(windowSelect(), { target: { value: 'release' } })
    expect([...releaseSelect().querySelectorAll('option')].map((o) => o.textContent)).toEqual(['No releases in this project'])
    expect(screen.getByRole('button', { name: 'Export again' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Download (2.1 MB)' }), 'a Download is never blocked').toBeEnabled()
  })

  it('the include boxes are independent of history and add a key only when ticked', async () => {
    mockStart.mockResolvedValue({ id: 'job-inc', kind: 'export', status: 'queued' })
    renderCard()
    expect(membersBox()).not.toBeChecked()
    expect(reviewsBox()).not.toBeChecked()
    fireEvent.click(membersBox())
    fireEvent.click(exportButton())
    await waitFor(() => expect(mockStart).toHaveBeenCalledTimes(1))
    expect(mockStart.mock.calls[0]).toStrictEqual(['proj-1', { include_members: true }])
    expect(mockUseEstimate.mock.calls.every(([, , enabled]) => enabled === false), 'no history, no estimate').toBe(true)
  })

  it('both boxes with a day window: the W30 body plus both keys', async () => {
    mockStart.mockResolvedValue({ id: 'job-all', kind: 'export', status: 'queued' })
    renderCard()
    fireEvent.click(historyBox())
    fireEvent.click(membersBox())
    fireEvent.click(reviewsBox())
    fireEvent.click(exportButton())
    await waitFor(() => expect(mockStart).toHaveBeenCalledTimes(1))
    expect(mockStart.mock.calls[0]).toStrictEqual([
      'proj-1', { include_history: true, history_days: 90, max_runs: 2000, include_members: true, include_reviews: true },
    ])
  })

  it('Download above 512 MB takes the ticket; at 512 MB and below, the Blob download (unchanged)', async () => {
    const big = { ...readyJob('big'), options: { include_history: true }, bytes: 600 * MB, counts: { test_runs: 3 } }
    mockDownloadLarge.mockResolvedValue('/api/v1/project-data-downloads/big?ticket=t')
    const { unmount } = renderCard({ latest: big as never })
    fireEvent.click(screen.getByRole('button', { name: 'Download (600.0 MB)' }))
    await waitFor(() => expect(mockDownloadLarge).toHaveBeenCalledWith('big'))
    expect(mockDownload).not.toHaveBeenCalled()
    unmount()

    const edge = { ...readyJob('edge'), options: { include_history: true }, bytes: 512 * MB, counts: { test_runs: 3 } }
    mockDownload.mockResolvedValue('edge.zip')
    renderCard({ latest: edge as never })
    fireEvent.click(screen.getByRole('button', { name: 'Download (512.0 MB)' }))
    await waitFor(() => expect(mockDownload).toHaveBeenCalledWith('edge'))
    expect(mockDownloadLarge).toHaveBeenCalledTimes(1)
  })

  it("a refused ticket reads its sentence like today's download errors and re-reads the list", async () => {
    mockDownloadLarge.mockRejectedValue(
      Object.assign(new Error('Request failed with status code 503'), { isAxiosError: true,
        response: { status: 503, data: { detail: { code: 'ticket_unavailable', message: ERROR_SENTENCES.ticket_unavailable } } },
      }),
    )
    renderCard({ latest: { ...readyJob('big'), bytes: 600 * MB } as never })
    fireEvent.click(screen.getByRole('button', { name: 'Download (600.0 MB)' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Downloads through the browser are unavailable right now; try again or use the CLI.',
    )
    expect(screen.queryByText(/Request failed/)).toBeNull()
    expect(onChanged).toHaveBeenCalled()
  })
})
