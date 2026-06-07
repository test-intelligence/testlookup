/**
 * Tests for UploadReportModal — the manual report upload UI (MRU-4).
 *
 * Verifies the state machine and the review-driven fixes:
 * - Upload button is disabled until a valid file is chosen.
 * - An unsupported / oversized file is rejected inline AND clears the selection
 *   (so a stale valid file can't be submitted under a new file's error).
 * - Success swaps to the "Upload accepted" state and "View run" calls onSuccess
 *   with the run_id.
 * - A backend error detail (string) is rendered inline.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockUpload = vi.hoisted(() => vi.fn())
const mockGetStatus = vi.hoisted(() => vi.fn())

vi.mock('@/services/reportUploadService', async (importActual) => {
  const actual = await importActual<typeof import('@/services/reportUploadService')>()
  return { ...actual, reportUploadService: { upload: mockUpload, getStatus: mockGetStatus } }
})

vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
}))

import UploadReportModal from './UploadReportModal'
import { MAX_UPLOAD_BYTES } from '@/services/reportUploadService'

// jsdom's File lacks arrayBuffer() (standard in real browsers) — polyfill it so
// the multi-file client-zip path can read each File's bytes.
if (typeof File !== 'undefined' && !File.prototype.arrayBuffer) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ;(File.prototype as any).arrayBuffer = function () {
    return Promise.resolve(new ArrayBuffer(0))
  }
}

function setup() {
  const onClose = vi.fn()
  const onSuccess = vi.fn()
  const utils = render(<UploadReportModal projectId="p1" onClose={onClose} onSuccess={onSuccess} />)
  const input = utils.container.querySelector('input[type="file"]') as HTMLInputElement
  const uploadBtn = () => screen.getByRole('button', { name: /^upload$/i })
  return { ...utils, input, uploadBtn, onClose, onSuccess }
}

function selectFile(input: HTMLInputElement, file: File) {
  fireEvent.change(input, { target: { files: [file] } })
}

describe('UploadReportModal', () => {
  beforeEach(() => {
    mockUpload.mockReset()
    mockGetStatus.mockReset()
    mockUpload.mockResolvedValue({ run_id: 'run-123', task_id: 't1', total_results: 0 })
  })

  it('disables Upload until a valid file is selected', () => {
    const { uploadBtn, input } = setup()
    expect(uploadBtn()).toBeDisabled()
    selectFile(input, new File(['<testsuite/>'], 'junit.xml', { type: 'text/xml' }))
    expect(uploadBtn()).toBeEnabled()
  })

  it('rejects an unsupported file inline and keeps Upload disabled', () => {
    const { uploadBtn, input } = setup()
    selectFile(input, new File(['hi'], 'notes.txt', { type: 'text/plain' }))
    expect(screen.getByText(/unsupported file type/i)).toBeInTheDocument()
    expect(uploadBtn()).toBeDisabled()
  })

  it('rejects an oversized file and clears any prior valid selection', () => {
    const { uploadBtn, input } = setup()
    selectFile(input, new File(['<testsuite/>'], 'ok.xml', { type: 'text/xml' }))
    expect(uploadBtn()).toBeEnabled()

    const big = new File(['x'], 'big.xml', { type: 'text/xml' })
    Object.defineProperty(big, 'size', { value: MAX_UPLOAD_BYTES + 1 })
    selectFile(input, big)

    expect(screen.getByText(/the limit is/i)).toBeInTheDocument()
    expect(uploadBtn()).toBeDisabled() // stale valid file was cleared
  })

  it('uploads, polls to success with result counts, and View run calls onSuccess', async () => {
    mockGetStatus.mockResolvedValue({
      task_id: 't1', run_id: 'run-123', state: 'succeeded',
      result: { total: 3, passed: 2, failed: 1, skipped: 0, broken: 0 },
    })
    const { uploadBtn, input, onSuccess } = setup()
    selectFile(input, new File(['<testsuite/>'], 'junit.xml', { type: 'text/xml' }))
    fireEvent.click(uploadBtn())

    expect(await screen.findByText(/processed 3 tests/i, {}, { timeout: 4000 })).toBeInTheDocument()
    expect(screen.getByText(/2 passed/i)).toBeInTheDocument()
    expect(mockUpload).toHaveBeenCalledWith(expect.objectContaining({ projectId: 'p1' }))
    expect(mockGetStatus).toHaveBeenCalledWith('t1')

    fireEvent.click(screen.getByRole('button', { name: /view run/i }))
    expect(onSuccess).toHaveBeenCalledWith('run-123')
  })

  it('surfaces a parse failure from the status poll', async () => {
    mockGetStatus.mockResolvedValue({
      task_id: 't1', run_id: 'run-123', state: 'failed',
      error: { code: 'parse_error', message: 'Could not parse the junit report.' },
    })
    const { uploadBtn, input } = setup()
    selectFile(input, new File(['<bad/>'], 'junit.xml', { type: 'text/xml' }))
    fireEvent.click(uploadBtn())

    expect(await screen.findByText(/could not parse the junit report/i, {}, { timeout: 4000 })).toBeInTheDocument()
  })

  it('zips multiple selected files into one .zip before upload (MRU-13)', async () => {
    mockGetStatus.mockResolvedValue({
      task_id: 't1', run_id: 'run-123', state: 'succeeded',
      result: { total: 2, passed: 2, failed: 0 },
    })
    const { uploadBtn, input } = setup()
    fireEvent.change(input, { target: { files: [
      new File(['<testsuite/>'], 'a.xml', { type: 'text/xml' }),
      new File(['{}'], 'b.json', { type: 'application/json' }),
    ] } })
    expect(screen.getByText(/2 files selected/i)).toBeInTheDocument()
    expect(uploadBtn()).toBeEnabled()

    fireEvent.click(uploadBtn())
    await waitFor(() => expect(mockUpload).toHaveBeenCalled())
    const arg = mockUpload.mock.calls[0][0]
    expect(arg.file).toBeInstanceOf(File)
    expect(arg.file.name).toMatch(/\.zip$/)
    expect(arg.file.type).toBe('application/zip')
  })
})
