/**
 * W24 ADM-05 — the Import card: the file pre-check sends nothing, the client_token is minted once per chosen
 * file (two clicks / the same file again → the same token), success renders the Open button and the lists, a
 * failed job re-enables Import with a NEW token, the load error sentence. Hook, service and store are mocked.
 *
 * W30 (ADM-05 run history): a 60 MB file goes through `importArchive` (the parted upload) with "Checking the file…"
 * and "Uploading… n of m parts"; a 2.1 GB file reads the 2 GB sentence and sends nothing; a proxy's HTML 413
 * reads `A part is larger than 32 MB.`; after a reload an `uploading` import asks for the same file and the same
 * file resumes with the SAME token; the history block of a finished history import.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ProjectImportCard from './ProjectImportCard'
import {
  ADD_MEMBERS_LABEL,
  ERROR_SENTENCES,
  KEEP_REVIEWS_LABEL,
  LAST_IMPORT_HEADING,
  NAME_PLACEHOLDER,
  UI_SENTENCES,
} from './projectDataModel'

const jobs: Record<string, unknown> = {}
const mockUseJob = vi.fn((id: string | null) => ({ data: id ? jobs[id] : undefined }))
vi.mock('@/hooks/useProjectDataJobs', () => ({
  useProjectDataJob: (id: string | null) => mockUseJob(id),
}))

const mockStartImport = vi.fn()
const mockImportArchive = vi.fn()
// The requests are mocked; the refusal reader (`projectDataErrorMessage`) stays real.
vi.mock('@/services/projectDataService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/projectDataService')>()),
  projectDataService: {
    startImport: (...args: unknown[]) => mockStartImport(...args),
    importArchive: (...args: unknown[]) => mockImportArchive(...args),
  },
}))

const NEW_PROJECT = { id: 'proj-new', name: 'Payment Service (imported)' }
const mockRefresh = vi.fn()
const mockSetActive = vi.fn()
vi.mock('@/store/projectStore', () => ({
  useProjectStore: (selector: (s: unknown) => unknown) =>
    selector({ refreshProjects: mockRefresh, setActiveProject: mockSetActive }),
}))

const MB = 1024 * 1024
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const onChanged = vi.fn()

function zip(name = 'payment.zip', size = 3 * 1024, lastModified = 1_700_000_000_000) {
  const file = new File([new Uint8Array(16)], name, { type: 'application/zip', lastModified })
  Object.defineProperty(file, 'size', { value: size })
  return file
}

const REPORT = {
  project_id: NEW_PROJECT.id,
  project_name: NEW_PROJECT.name,
  project_slug: 'payment-service-imported',
  inserted: { test_plans: 3, managed_test_cases: 12, releases: 1 },
  switched_off: { webhook_subscriptions: 1 },
  skipped: [{ entity: 'test_plan_items', reason: 'row_unresolved', count: 1 }],
  cleared: [],
  signature: 'valid',
}

function renderCard(props: Partial<Parameters<typeof ProjectImportCard>[0]> = {}) {
  return render(
    <MemoryRouter initialEntries={['/settings/project-data']}>
      <Routes>
        <Route path="/settings/project-data" element={<ProjectImportCard latest={null} onChanged={onChanged} {...props} />} />
        <Route path="/test-management" element={<h1>Test Management</h1>} />
      </Routes>
    </MemoryRouter>,
  )
}

const fileInput = () => screen.getByLabelText('Export file (.zip)')
const importButton = () => screen.getByRole('button', { name: 'Import' })
const choose = (file: File) => fireEvent.change(fileInput(), { target: { files: [file] } })
const tokenOf = (call: unknown[]) => (call[1] as { clientToken: string }).clientToken

describe('ProjectImportCard', () => {
  beforeEach(() => {
    for (const key of Object.keys(jobs)) delete jobs[key]
    window.sessionStorage.clear()
    mockUseJob.mockClear()
    mockStartImport.mockReset()
    mockImportArchive.mockReset()
    mockRefresh.mockReset()
    mockSetActive.mockReset()
    onChanged.mockReset()
  })

  it('the intro, the inert note, the two fields and a disabled Import until a file is chosen', () => {
    renderCard()
    expect(screen.getByRole('heading', { name: UI_SENTENCES.import_heading })).toBeInTheDocument()
    expect(screen.getByText(UI_SENTENCES.import_intro)).toBeInTheDocument()
    expect(screen.getByText(UI_SENTENCES.inert_note)).toBeInTheDocument()
    expect(screen.getByLabelText('Project name (optional)')).toHaveAttribute('placeholder', NAME_PLACEHOLDER)
    expect(fileInput()).toHaveAttribute('accept', '.zip,application/zip')
    expect(importButton()).toBeDisabled()
    expect(mockUseJob.mock.calls.every(([id]) => id === null)).toBe(true)
  })

  it('a 2.1 GB file → "The file is larger than 2 GB." and NO request; a .txt → upload_empty (W30)', () => {
    renderCard()
    choose(zip('huge.zip', Math.round(2.1 * 1024 * MB)))
    expect(screen.getByText('The file is larger than 2 GB.')).toBeInTheDocument()
    expect(importButton()).toBeDisabled()
    fireEvent.click(importButton())
    choose(new File(['x'], 'notes.txt', { type: 'text/plain' }))
    expect(screen.getByText(ERROR_SENTENCES.upload_empty)).toBeInTheDocument()
    expect(mockStartImport).not.toHaveBeenCalled()
    expect(mockImportArchive).not.toHaveBeenCalled()
  })

  it('one token per chosen file: two clicks and the SAME file again send the same token; another file a new one', async () => {
    mockStartImport.mockResolvedValue({ id: 'job-1', kind: 'import', status: 'queued' })
    renderCard()
    const file = zip()
    choose(file)
    expect(screen.getByText('payment.zip · 3 KB')).toBeInTheDocument()
    fireEvent.click(importButton())
    await waitFor(() => expect(mockStartImport).toHaveBeenCalledTimes(1))
    const [sentFile, opts] = mockStartImport.mock.calls[0]
    expect(sentFile).toBe(file)
    expect(opts).toEqual({ clientToken: expect.stringMatching(UUID) })
    expect(opts).not.toHaveProperty('name')

    jobs['job-1'] = { id: 'job-1', kind: 'import', status: 'succeeded', import_report: REPORT }
    choose(zip())
    await waitFor(() => expect(importButton()).toBeEnabled())
    fireEvent.click(importButton())
    await waitFor(() => expect(mockStartImport).toHaveBeenCalledTimes(2))
    expect(tokenOf(mockStartImport.mock.calls[1])).toBe(tokenOf(mockStartImport.mock.calls[0]))

    choose(zip('other.zip'))
    fireEvent.click(importButton())
    await waitFor(() => expect(mockStartImport).toHaveBeenCalledTimes(3))
    expect(tokenOf(mockStartImport.mock.calls[2])).not.toBe(tokenOf(mockStartImport.mock.calls[0]))
  })

  it('a supplied name is sent trimmed; a one-letter name is refused before any request', async () => {
    mockStartImport.mockResolvedValue({ id: 'job-2', kind: 'import', status: 'queued' })
    renderCard()
    choose(zip())
    fireEvent.change(screen.getByLabelText('Project name (optional)'), { target: { value: 'x' } })
    expect(screen.getByText(ERROR_SENTENCES.name_invalid)).toBeInTheDocument()
    expect(importButton()).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Project name (optional)'), { target: { value: '  QA copy  ' } })
    fireEvent.click(importButton())
    await waitFor(() => expect(mockStartImport).toHaveBeenCalledTimes(1))
    expect(mockStartImport.mock.calls[0][1]).toMatchObject({ name: 'QA copy' })
  })

  it('progress, then "Created …" + Open (selects the new project, lands on Test Management) + the lists', async () => {
    mockStartImport.mockResolvedValue({ id: 'job-3', kind: 'import', status: 'queued' })
    mockRefresh.mockResolvedValue([{ id: 'proj-other', name: 'Checkout' }, NEW_PROJECT])
    jobs['job-3'] = { id: 'job-3', kind: 'import', status: 'running', progress: { done: 5, total: 30 } }
    renderCard()
    choose(zip())
    fireEvent.click(importButton())
    expect(await screen.findByText('Importing… 5 of 30 tables')).toBeInTheDocument()
    expect(importButton(), 'Import waits while the job runs').toBeDisabled()

    jobs['job-3'] = { id: 'job-3', kind: 'import', status: 'succeeded', import_report: REPORT }
    choose(zip())
    expect(await screen.findByText('Created Payment Service (imported).')).toBeInTheDocument()
    expect(onChanged).toHaveBeenCalled()
    expect(screen.getByText('1 row was skipped: it pointed at a row that is not in the export')).toBeInTheDocument()
    expect(screen.getByText('1 row switched off — secret required')).toBeInTheDocument()
    expect(screen.getByText('12 rows')).toBeInTheDocument()
    expect(screen.getByText('1 row')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Open Payment Service (imported)' }))
    expect(await screen.findByRole('heading', { name: 'Test Management' })).toBeInTheDocument()
    expect(mockRefresh).toHaveBeenCalledTimes(1)
    expect(mockSetActive).toHaveBeenCalledWith(NEW_PROJECT)
  })

  it('a failed job shows its sentence and re-enables Import with a NEW token', async () => {
    mockStartImport.mockResolvedValue({ id: 'job-4', kind: 'import', status: 'queued' })
    renderCard()
    choose(zip())
    fireEvent.click(importButton())
    await waitFor(() => expect(mockStartImport).toHaveBeenCalledTimes(1))
    const sentence = 'The archive does not match its manifest (part-00001.jsonl); nothing was imported.'
    jobs['job-4'] = { id: 'job-4', kind: 'import', status: 'failed', error: { code: 'tampered', message: sentence } }
    choose(zip())
    expect(await screen.findByText(sentence)).toBeInTheDocument()
    expect(importButton()).toBeEnabled()
    fireEvent.click(importButton())
    await waitFor(() => expect(mockStartImport).toHaveBeenCalledTimes(2))
    expect(tokenOf(mockStartImport.mock.calls[1])).not.toBe(tokenOf(mockStartImport.mock.calls[0]))
    expect(tokenOf(mockStartImport.mock.calls[1])).toMatch(UUID)
  })

  it("a refused POST reads as the server's sentence", async () => {
    mockStartImport.mockRejectedValue(
      Object.assign(new Error('Request failed with status code 409'), { isAxiosError: true,
        response: { status: 409, data: { detail: { code: 'name_taken', message: 'A project called QA copy already exists; choose another name.' } } },
      }),
    )
    renderCard()
    choose(zip())
    fireEvent.click(importButton())
    expect(await screen.findByRole('alert')).toHaveTextContent('A project called QA copy already exists; choose another name.')
    expect(screen.queryByText(/Request failed/)).toBeNull()
  })

  it('on load: a running import resumes; a finished one is collapsed under "Last import"; a failed read says so', () => {
    const running = { id: 'job-old', kind: 'import', status: 'running', progress: { done: 9, total: 30 } }
    jobs['job-old'] = running
    const { unmount } = renderCard({ latest: running as never })
    expect(mockUseJob).toHaveBeenLastCalledWith('job-old')
    expect(screen.getByText('Importing… 9 of 30 tables')).toBeInTheDocument()
    unmount()

    renderCard({ latest: { id: 'job-done', kind: 'import', status: 'succeeded', import_report: REPORT } as never, loadError: undefined })
    expect(screen.getByRole('heading', { name: LAST_IMPORT_HEADING })).toBeInTheDocument()
    expect(screen.getByText('Created Payment Service (imported). 16 rows in 3 tables.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open Payment Service (imported)' })).toBeInTheDocument()
  })

  it('the load error sentence', () => {
    renderCard({ latest: undefined, loadError: new Error('x') })
    expect(screen.getByText('Could not load your imports.')).toBeInTheDocument()
  })
})

// ── W30: the parted upload and the history report ───────────────────────────

/** A promise the test resolves / rejects by hand, to look at the card mid-upload. */
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}
type ArchiveOptions = { clientToken: string; name?: string; onProgress: (progress: unknown) => void }
const archiveOptions = (i = 0) => mockImportArchive.mock.calls[i][1] as ArchiveOptions

describe('ProjectImportCard — parted upload and history (W30)', () => {
  beforeEach(() => {
    for (const key of Object.keys(jobs)) delete jobs[key]
    window.sessionStorage.clear()
    mockUseJob.mockClear()
    mockStartImport.mockReset()
    mockImportArchive.mockReset()
    onChanged.mockReset()
  })

  it('a 60 MB file → importArchive (not the single POST): "Checking the file…", "Uploading… 1 of 2 parts", then the job polls', async () => {
    const upload = deferred<unknown>()
    mockImportArchive.mockReturnValue(upload.promise)
    renderCard()
    const file = zip('history.zip', 60 * MB)
    choose(file)
    expect(screen.getByText('history.zip · 60.0 MB')).toBeInTheDocument()
    fireEvent.click(importButton())
    await waitFor(() => expect(mockImportArchive).toHaveBeenCalledTimes(1))
    expect(mockStartImport).not.toHaveBeenCalled()
    expect(mockImportArchive.mock.calls[0][0]).toBe(file)
    expect(archiveOptions().clientToken).toMatch(UUID)
    expect(archiveOptions()).not.toHaveProperty('name')
    expect(screen.getByText('Checking the file…')).toBeInTheDocument()
    expect(importButton(), 'Import waits while the file is sent').toBeDisabled()

    act(() => archiveOptions().onProgress({ phase: 'uploading', done: 1, total: 2 }))
    expect(screen.getByText('Uploading… 1 of 2 parts')).toBeInTheDocument()

    jobs['job-parted'] = { id: 'job-parted', kind: 'import', status: 'running', progress: { done: 56, total: 56, phase: 'documents', phase_done: 5000, phase_total: 9120 } }
    await act(async () => upload.resolve({ id: 'job-parted', kind: 'import', status: 'queued' }))
    expect(await screen.findByText('Stored documents… 5,000 of 9,120')).toBeInTheDocument()
    expect(mockUseJob).toHaveBeenLastCalledWith('job-parted')
    expect(onChanged).toHaveBeenCalled()
  })

  it("a proxy's HTML 413 on a part reads \"A part is larger than 32 MB.\", never \"Request failed …\"", async () => {
    mockImportArchive.mockRejectedValue(
      Object.assign(new Error('Request failed with status code 413'), { isAxiosError: true,
        response: { status: 413, data: '<html><body><h1>413 Request Entity Too Large</h1></body></html>' },
      }),
    )
    renderCard()
    choose(zip('history.zip', 60 * MB))
    fireEvent.click(importButton())
    expect(await screen.findByRole('alert')).toHaveTextContent('A part is larger than 32 MB.')
    expect(screen.queryByText(/Request failed/)).toBeNull()
    expect(importButton(), 'the same file can be tried again').toBeEnabled()
  })

  it("a structured refusal on the parted path still reads as the server's sentence", async () => {
    mockImportArchive.mockRejectedValue(
      Object.assign(new Error('Request failed with status code 409'), { isAxiosError: true,
        response: { status: 409, data: { detail: { code: 'part_conflict', message: 'Part 2 was already received with different content.' } } },
      }),
    )
    renderCard()
    choose(zip('history.zip', 60 * MB))
    fireEvent.click(importButton())
    expect(await screen.findByRole('alert')).toHaveTextContent('Part 2 was already received with different content.')
  })

  it('after a reload an `uploading` import asks for the same file (not polled); the same file resumes with the SAME token', async () => {
    mockImportArchive.mockReturnValue(new Promise(() => undefined))
    const first = renderCard()
    choose(zip('history.zip', 60 * MB))
    fireEvent.click(importButton())
    await waitFor(() => expect(mockImportArchive).toHaveBeenCalledTimes(1))
    const token = archiveOptions(0).clientToken
    first.unmount()

    mockUseJob.mockClear()
    const unfinished = { id: 'job-up', kind: 'import', status: 'uploading', upload: { parts: 2, bytes: 60 * MB, received: [1], received_bytes: 32 * MB } }
    renderCard({ latest: unfinished as never })
    expect(screen.getByText('Uploading… 1 of 2 parts')).toBeInTheDocument()
    expect(screen.getByText('Choose the same file to resume the upload.')).toBeInTheDocument()
    expect(mockUseJob.mock.calls.every(([id]) => id === null), 'an upload only this file can finish is not polled').toBe(true)

    choose(zip('history.zip', 60 * MB))
    fireEvent.click(importButton())
    await waitFor(() => expect(mockImportArchive).toHaveBeenCalledTimes(2))
    expect(archiveOptions(1).clientToken).toBe(token)
  })

  it('a finished history import (a small 12 KB archive, the single POST): the History block, one skip, the notes', async () => {
    // Numbers a 12 KB archive can carry (the W30 baseline reading); the big-number formatting is the model test's.
    const history = {
      runs: 2, test_cases: 6,
      oldest_run_at: new Date(2026, 8, 14, 12).toISOString(), newest_run_at: new Date(2026, 8, 16, 12).toISOString(),
      documents: { raw_allure_json: 6, run_summaries: 2, decision_reports: 0 }, objects: 1, object_bytes: 2 * 1024,
      skipped: [{ entity: 'objects', reason: 'too_large', count: 1 }],
      retention_paused_until: new Date(2026, 11, 17, 12).toISOString(), runs_past_retention: 0, retention_days: 365,
      recent_runs: 2, search_reindex: 'queued',
    }
    renderCard({ latest: null })
    jobs['job-h'] = { id: 'job-h', kind: 'import', status: 'succeeded', import_report: { ...REPORT, history } }
    mockStartImport.mockResolvedValue({ id: 'job-h', kind: 'import', status: 'queued' })
    choose(zip('history.zip', 12 * 1024))
    fireEvent.click(importButton())
    expect(await screen.findByText('2 runs from Sep 14, 2026 to Sep 16, 2026')).toBeInTheDocument()
    expect(mockImportArchive, 'a small archive takes the single POST').not.toHaveBeenCalled()
    const block = within(document.querySelector('[data-import-history]') as HTMLElement)
    expect(block.getByRole('heading', { name: 'Stored documents' })).toBeInTheDocument()
    expect(block.getByText('6 documents')).toBeInTheDocument()
    expect(block.getByText('2 documents')).toBeInTheDocument()
    expect(block.queryByText('decision_reports'), 'a zero count is not listed').toBeNull()
    expect(block.getByText('1 attachment · 2 KB')).toBeInTheDocument()
    expect(block.getByText('1 attachment was skipped: it is larger than 256 MB')).toBeInTheDocument()
    expect(block.getByText('The retention purge of the new project is paused until Dec 17, 2026.')).toBeInTheDocument()
    expect(block.queryByText(/retention window|Retention is off/), '0 runs past a 365-day window: no retention note').toBeNull()
    expect(block.getByText('2 imported runs are from the last 7 days; an all-projects digest can list them.')).toBeInTheDocument()
    expect(block.getByText('Search is being rebuilt for the new project.')).toBeInTheDocument()
  })

  it('the retention notes follow `retention_days`: runs past the window name it; null reads "retention is off"', async () => {
    const base = {
      runs: 2, test_cases: 6, oldest_run_at: new Date(2025, 0, 10, 12).toISOString(), newest_run_at: new Date(2026, 8, 16, 12).toISOString(),
      documents: { run_summaries: 2 }, objects: 0, object_bytes: 0, skipped: [], recent_runs: 1, search_reindex: 'queued',
    }
    jobs['job-r'] = { id: 'job-r', kind: 'import', status: 'succeeded', import_report: { ...REPORT, history: { ...base, runs_past_retention: 1, retention_days: 365 } } }
    mockStartImport.mockResolvedValue({ id: 'job-r', kind: 'import', status: 'queued' })
    const first = renderCard({ latest: null })
    choose(zip('old-history.zip', 12 * 1024))
    fireEvent.click(importButton())
    expect(await screen.findByText("Runs older than this project's retention window (365 days) will be purged on the next nightly purge.")).toBeInTheDocument()
    expect(screen.getByText('1 imported run is from the last 7 days; an all-projects digest can list it.')).toBeInTheDocument()
    first.unmount()

    jobs['job-r'] = { id: 'job-r', kind: 'import', status: 'succeeded', import_report: { ...REPORT, history: { ...base, runs_past_retention: 0, retention_days: null } } }
    renderCard({ latest: null })
    choose(zip('old-history.zip', 12 * 1024))
    fireEvent.click(importButton())
    expect(await screen.findByText('Retention is off for this project: imported runs are kept.')).toBeInTheDocument()
  })
})

// ── W37: Add members / Keep reviews and the report's new lines ──────────────

const addMembersBox = () => screen.getByRole('checkbox', { name: ADD_MEMBERS_LABEL })
const keepReviewsBox = () => screen.getByRole('checkbox', { name: KEEP_REVIEWS_LABEL })

describe('ProjectImportCard — Add members and Keep reviews (W37)', () => {
  beforeEach(() => {
    for (const key of Object.keys(jobs)) delete jobs[key]
    window.sessionStorage.clear()
    mockUseJob.mockClear()
    mockStartImport.mockReset()
    mockImportArchive.mockReset()
    onChanged.mockReset()
  })

  it('both boxes start unticked (the default POST stays file + token); ticked, the single POST carries both', async () => {
    mockStartImport.mockResolvedValue({ id: 'job-opt', kind: 'import', status: 'queued' })
    renderCard()
    expect(addMembersBox()).not.toBeChecked()
    expect(keepReviewsBox()).not.toBeChecked()
    choose(zip())
    fireEvent.click(addMembersBox())
    fireEvent.click(keepReviewsBox())
    fireEvent.click(importButton())
    await waitFor(() => expect(mockStartImport).toHaveBeenCalledTimes(1))
    expect(mockStartImport.mock.calls[0][1]).toEqual({ clientToken: expect.stringMatching(UUID), addMembers: true, keepReviews: true })
  })

  it('one box only: only that option travels — on the parted path too', async () => {
    mockImportArchive.mockResolvedValue({ id: 'job-part', kind: 'import', status: 'queued' })
    renderCard()
    choose(zip('history.zip', 60 * MB))
    fireEvent.click(keepReviewsBox())
    fireEvent.click(importButton())
    await waitFor(() => expect(mockImportArchive).toHaveBeenCalledTimes(1))
    expect(archiveOptions()).toMatchObject({ keepReviews: true })
    expect(archiveOptions()).not.toHaveProperty('addMembers')
  })

  it('the report: members read through the lists, skips with their own reason, the unclaim line, one line per ignored option', async () => {
    const report = {
      ...REPORT,
      inserted: { ...REPORT.inserted, project_members: 2, test_case_reviews: 0 },
      skipped: [{ entity: 'project_members', reason: 'email_not_found', count: 1 }],
      switched_off: { 'managed_test_cases.review_unclaimed': 1 },
      options_ignored: ['keep_reviews'],
    }
    jobs['job-rep'] = { id: 'job-rep', kind: 'import', status: 'succeeded', import_report: report }
    mockStartImport.mockResolvedValue({ id: 'job-rep', kind: 'import', status: 'queued' })
    renderCard()
    choose(zip())
    fireEvent.click(addMembersBox())
    fireEvent.click(keepReviewsBox())
    fireEvent.click(importButton())
    expect(await screen.findByText('Keep reviews: this file has no reviews — export it again with Include reviews.')).toBeInTheDocument()
    expect(screen.queryByText(/^Add members: this file lists no members/), 'only the ignored option is said').toBeNull()
    const inserted = within(document.querySelector('[data-report-list="inserted"]') as HTMLElement)
    expect(inserted.getByText('project_members')).toBeInTheDocument()
    expect(inserted.queryByText('test_case_reviews'), 'a zero count is not listed').toBeNull()
    expect(screen.getByText('1 row was skipped: no active account here has this e-mail address')).toBeInTheDocument()
    expect(screen.getByText('1 row returned to Review requested — the reviewer is not a member here')).toBeInTheDocument()
  })
})
