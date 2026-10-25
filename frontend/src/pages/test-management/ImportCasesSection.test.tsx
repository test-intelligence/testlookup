/**
 * W13 TCL-04: the Import section — the dry run on file choice, the preview's
 * chips and counts, the server's sentences inline, Import N only when N > 0,
 * the result line, React text for a formula title, the template's headers.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useInlineSlot } from '@/components/ui/inPage/useInlineSlot'
import type { ImportRowResult, TestCaseImportResponse } from '@/types/test-library'

const importCases = vi.fn()
const toastSuccess = vi.fn()
const downloadBlob = vi.fn()

vi.mock('@/services/testLibraryService', () => ({
  testLibraryService: { importCases: (...args: unknown[]) => importCases(...args) },
}))
vi.mock('@/utils/download', () => ({ downloadBlob: (...args: unknown[]) => downloadBlob(...args) }))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: (...a: unknown[]) => toastSuccess(...a), error: vi.fn() }) }))

import ImportCasesSection from './ImportCasesSection'
import { IMPORT_TEMPLATE_HEADERS, UTF8_BOM } from './importCasesModel'

const FORMULA_TITLE = '=HYPERLINK("http://x.test","click")'
const SCRIPT_TITLE = '<img src=x onerror=alert(1)>'

function row(n: number, outcome: string, over: Partial<ImportRowResult> = {}): ImportRowResult {
  return { row: n, outcome, title: `Case ${n}`, reasons: [], warnings: [], ...over }
}

function dryRun(): TestCaseImportResponse {
  const rows: ImportRowResult[] = []
  for (let n = 2; n <= 18; n += 1) rows.push(row(n, 'create'))
  rows[0] = row(2, 'create', { title: FORMULA_TITLE, warnings: ["Status 'approved' is ignored: imported cases start as Draft."] })
  rows[1] = row(3, 'create', { title: SCRIPT_TITLE })
  rows.push(row(19, 'invalid', { title: 'ab', reasons: ['Title is required (3 to 500 characters).'] }))
  rows.push(row(20, 'invalid', { reasons: ['Priority must be critical, high, medium or low (or P0 to P3).'] }))
  rows.push(row(21, 'invalid', { reasons: ['Automated must be Yes or No.'] }))
  return {
    project_id: 'p-1', dry_run: true, file_name: 'cases.csv', format: 'csv',
    headers: { recognised: ['Title', 'Priority'], ignored: ['Status', 'Version'], unknown: ['Owner'] },
    summary: { rows: 20, create: 17, duplicate: 0, invalid: 3, created: 0 },
    rows,
  }
}

function applied(): TestCaseImportResponse {
  const base = dryRun()
  return {
    ...base,
    dry_run: false,
    summary: { ...base.summary, created: 17 },
    rows: base.rows.map((r) => (r.outcome === 'create' ? { ...r, outcome: 'created', case_id: `case-${r.row}-0000` } : r)),
  }
}

const onClose = vi.fn()
const onImported = vi.fn()
const onOpenCase = vi.fn()

function Harness() {
  const slot = useInlineSlot<'create' | 'ai' | 'import'>()
  return <ImportCasesSection projectId="p-1" slot={slot} onClose={onClose} onImported={onImported} onOpenCase={onOpenCase} />
}

const FILE = new File(['Title\nLogin works\n'], 'cases.csv', { type: 'text/csv' })

function chooseFile(file: File = FILE) {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(input, { target: { files: [file] } })
}

function httpError(status: number, detail: unknown) {
  return Object.assign(new Error(`HTTP ${status}`), { response: { status, data: { detail } } })
}

beforeEach(() => {
  importCases.mockReset()
  toastSuccess.mockReset()
  downloadBlob.mockReset()
  onClose.mockReset()
  onImported.mockReset()
  onOpenCase.mockReset()
})

describe('ImportCasesSection', () => {
  it('is an in-page section named "Import test cases", never a dialog', () => {
    render(<Harness />)
    const section = screen.getByRole('region', { name: 'Import test cases' })
    expect(section.querySelector('#tm-import-title')).not.toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByText('CSV (UTF-8) or Excel, up to 5 MB and 2,000 rows. Every case is imported as a Draft.')).toBeInTheDocument()
    expect((document.querySelector('input[type="file"]') as HTMLInputElement).accept).toBe('.csv,.xlsx')
    // Nothing is asked before a file is chosen.
    expect(importCases).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: /^Import \d/ })).toBeNull()
  })

  it('runs the dry run as soon as a file is chosen and shows the preview', async () => {
    let resolve: (value: TestCaseImportResponse) => void = () => {}
    importCases.mockReturnValueOnce(new Promise<TestCaseImportResponse>((r) => { resolve = r }))
    render(<Harness />)
    chooseFile()
    expect(importCases).toHaveBeenCalledWith('p-1', FILE, true)
    expect(screen.getByText('Checking the file…')).toBeInTheDocument()
    resolve(dryRun())
    expect(await screen.findByText('20 rows: 17 to create · 0 duplicates · 3 invalid')).toBeInTheDocument()
    expect(screen.getByText('Ignored columns: Status, Version')).toBeInTheDocument()
    expect(screen.getByText('Unknown columns: Owner')).toBeInTheDocument()
    expect(document.querySelectorAll('[data-import-row]')).toHaveLength(20)
    expect(screen.getByText('Title is required (3 to 500 characters).')).toBeInTheDocument()
    expect(screen.getByText("Status 'approved' is ignored: imported cases start as Draft.")).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Import 17 cases' })).toBeEnabled()
  })

  it('filters the preview by outcome with counted chips', async () => {
    importCases.mockResolvedValueOnce(dryRun())
    render(<Harness />)
    chooseFile()
    await screen.findByText('20 rows: 17 to create · 0 duplicates · 3 invalid')
    const chips = within(screen.getByRole('group', { name: 'Show rows' })).getAllByRole('button')
    expect(chips.map((chip) => chip.textContent)).toEqual(['All 20', 'Create 17', 'Duplicate 0', 'Invalid 3'])
    fireEvent.click(screen.getByRole('button', { name: 'Invalid 3' }))
    const shown = Array.from(document.querySelectorAll('[data-import-row]'))
    expect(shown.map((tr) => tr.getAttribute('data-outcome'))).toEqual(['invalid', 'invalid', 'invalid'])
    expect(screen.getByRole('button', { name: 'Invalid 3' })).toHaveAttribute('aria-pressed', 'true')
  })

  it.each([
    [413, 'The file is larger than 5 MB.'],
    [422, 'The file has more than 2,000 rows: split it and import each part.'],
    [400, 'Upload a .csv or .xlsx file.'],
  ])('renders the server sentence of a %i in the section', async (status, sentence) => {
    importCases.mockRejectedValueOnce(httpError(status, sentence))
    render(<Harness />)
    chooseFile()
    expect(await screen.findByRole('alert')).toHaveTextContent(sentence)
    expect(screen.queryByRole('button', { name: /^Import \d/ })).toBeNull()
  })

  it('keeps Import disabled when nothing would be created', async () => {
    importCases.mockResolvedValueOnce({
      ...dryRun(),
      summary: { rows: 2, create: 0, duplicate: 1, invalid: 1, created: 0 },
      rows: [
        row(2, 'duplicate', { duplicate_of: { case_id: '1a2b3c4d-0000-0000-0000-000000000000', title: 'Login works' }, reasons: ['Same title, objective, steps and expected result as an existing case.'] }),
        row(3, 'duplicate', { duplicate_of: { row: 2 } }),
      ],
    })
    render(<Harness />)
    chooseFile()
    expect(await screen.findByRole('button', { name: 'Import 0 cases' })).toBeDisabled()
    expect(screen.getByText('Duplicate of TC-1a2b3c Login works')).toBeInTheDocument()
    expect(screen.getByText('Duplicate of row 2')).toBeInTheDocument()
  })

  it('applies the same file, then shows the result line and opens a created case', async () => {
    importCases.mockResolvedValueOnce(dryRun()).mockResolvedValueOnce(applied())
    render(<Harness />)
    chooseFile()
    fireEvent.click(await screen.findByRole('button', { name: 'Import 17 cases' }))
    expect(await screen.findByText('17 cases imported as Draft. 3 rows were not imported.')).toBeInTheDocument()
    expect(importCases).toHaveBeenLastCalledWith('p-1', FILE, false)
    expect(importCases).toHaveBeenCalledTimes(2)
    expect(toastSuccess).toHaveBeenCalledWith('17 test cases imported')
    expect(onImported).toHaveBeenCalledWith(17)
    expect(within(screen.getByRole('group', { name: 'Show rows' })).getByRole('button', { name: 'Created 17' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Case 4' }))
    expect(onOpenCase).toHaveBeenCalledWith('case-4-0000')
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    expect(onClose).toHaveBeenCalled()
  })

  it('shows imported text as text: a formula and markup are characters, not a link or an element', async () => {
    importCases.mockResolvedValueOnce(dryRun())
    const { container } = render(<Harness />)
    chooseFile()
    expect(await screen.findByText(FORMULA_TITLE)).toBeInTheDocument()
    expect(screen.getByText(SCRIPT_TITLE)).toBeInTheDocument()
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('a[href]')).toBeNull()
  })

  it('downloads a CSV template holding exactly the server headers', async () => {
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: 'Download the CSV template' }))
    expect(downloadBlob).toHaveBeenCalledTimes(1)
    const [blob, name] = downloadBlob.mock.calls[0] as [Blob, string]
    expect(name).toBe('test-cases-template.csv')
    // Blob.text() decodes UTF-8 and drops a leading BOM; strip it anyway in case the runtime keeps it.
    const text = (await blob.text()).replace(UTF8_BOM, '')
    expect(blob.type).toBe('text/csv;charset=utf-8')
    expect(text.trim().split(',')).toEqual([...IMPORT_TEMPLATE_HEADERS])
    expect(IMPORT_TEMPLATE_HEADERS).toEqual([
      'Title', 'Type', 'Priority', 'Severity', 'Feature Area', 'Objective', 'Preconditions', 'Steps',
      'Expected Result', 'Automated', 'Automation Status', 'Tags', 'Suite', 'Description', 'Test Data',
    ])
    expect(importCases).not.toHaveBeenCalled()
  })

  it('answers only the latest file when a second is chosen during a check', async () => {
    let first: (value: TestCaseImportResponse) => void = () => {}
    importCases
      .mockReturnValueOnce(new Promise<TestCaseImportResponse>((r) => { first = r }))
      .mockResolvedValueOnce({ ...dryRun(), summary: { rows: 1, create: 1, duplicate: 0, invalid: 0 }, rows: [row(2, 'create')] })
    render(<Harness />)
    chooseFile()
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    // The input is disabled mid-check; a programmatic change still reaches the handler.
    fireEvent.change(input, { target: { files: [new File(['Title\nx\n'], 'b.csv')] } })
    await waitFor(() => expect(screen.getByText('1 row: 1 to create · 0 duplicates · 0 invalid')).toBeInTheDocument())
    first(dryRun())
    await Promise.resolve()
    expect(screen.queryByText('20 rows: 17 to create · 0 duplicates · 3 invalid')).toBeNull()
  })
})
