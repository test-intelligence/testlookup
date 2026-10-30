/**
 * W24 ADM-05 — the Import card: the file pre-check sends nothing, the client_token is minted once per chosen
 * file (two clicks / the same file again → the same token), success renders the Open button and the lists, a
 * failed job re-enables Import with a NEW token, the load error sentence. Hook, service and store are mocked.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ProjectImportCard from './ProjectImportCard'
import { ERROR_SENTENCES, LAST_IMPORT_HEADING, NAME_PLACEHOLDER, UI_SENTENCES } from './projectDataModel'

const jobs: Record<string, unknown> = {}
const mockUseJob = vi.fn((id: string | null) => ({ data: id ? jobs[id] : undefined }))
vi.mock('@/hooks/useProjectDataJobs', () => ({
  useProjectDataJob: (id: string | null) => mockUseJob(id),
}))

const mockStartImport = vi.fn()
// The request is mocked; the refusal reader (`projectDataErrorMessage`) stays real.
vi.mock('@/services/projectDataService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/projectDataService')>()),
  projectDataService: { startImport: (...args: unknown[]) => mockStartImport(...args) },
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
    mockUseJob.mockClear()
    mockStartImport.mockReset()
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

  it('a 60 MB file → "The file is larger than 50 MB." and NO request; a .txt → upload_empty', () => {
    renderCard()
    choose(zip('big.zip', 60 * MB))
    expect(screen.getByText('The file is larger than 50 MB.')).toBeInTheDocument()
    expect(importButton()).toBeDisabled()
    fireEvent.click(importButton())
    choose(new File(['x'], 'notes.txt', { type: 'text/plain' }))
    expect(screen.getByText(ERROR_SENTENCES.upload_empty)).toBeInTheDocument()
    expect(mockStartImport).not.toHaveBeenCalled()
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
