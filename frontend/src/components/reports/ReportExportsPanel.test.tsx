import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import type { ReactNode } from 'react'
import type { ReportExport } from '@/services/summaryReportService'

const mockList = vi.fn()
const mockRetry = vi.fn()
const mockDownload = vi.fn()
vi.mock('@/services/summaryReportService', () => ({
  summaryReportService: {
    listExports: (...args: unknown[]) => mockList(...args),
    retryExport: (...args: unknown[]) => mockRetry(...args),
    downloadExport: (...args: unknown[]) => mockDownload(...args),
  },
}))

const mockDownloadBlob = vi.fn()
vi.mock('@/utils/download', () => ({
  downloadBlob: (...args: unknown[]) => mockDownloadBlob(...args),
}))

const toastSuccess = vi.fn()
const toastError = vi.fn()
vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), {
    success: (...args: unknown[]) => toastSuccess(...args),
    error: (...args: unknown[]) => toastError(...args),
    dismiss: vi.fn(),
  }),
}))

import ReportExportsPanel from './ReportExportsPanel'

function exp(overrides: Partial<ReportExport> = {}): ReportExport {
  return {
    id: 'e1',
    project_id: 'p1',
    format: 'pdf',
    status: 'running',
    attempts: 1,
    params: { mode: 'window', days: 30, release_ids: [], suite_names: ['PaymentSuite'] },
    filename: null,
    size_bytes: null,
    error: null,
    requested_at: '2026-10-03T10:00:00Z',
    started_at: '2026-10-03T10:00:01Z',
    finished_at: null,
    expires_at: '2026-10-10T10:00:00Z',
    retryable: false,
    download_url: null,
    ...overrides,
  }
}

function wrap(ui: ReactNode) {
  return render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{ui}</SWRConfig>)
}

beforeEach(() => {
  mockList.mockReset()
  mockRetry.mockReset()
  mockDownload.mockReset()
  mockDownloadBlob.mockReset()
  toastSuccess.mockReset()
  toastError.mockReset()
})

describe('ReportExportsPanel (VIZ-607)', () => {
  it('renders nothing while the reader has no exports', async () => {
    mockList.mockResolvedValue([])
    const { container } = wrap(<ReportExportsPanel projectId="p1" refreshToken={0} />)
    await waitFor(() => expect(mockList).toHaveBeenCalledWith('p1'))
    expect(container.querySelector('[data-report-exports]')).toBeNull()
  })

  it('lists each export with its status and scope, and downloads a ready one by its name', async () => {
    mockList.mockResolvedValue([
      exp({ id: 'a', status: 'completed', filename: 'summary-pay-30d-window.pdf', size_bytes: 48_000 }),
      exp({ id: 'b', status: 'failed', error: 'The export could not be generated: boom', retryable: true }),
    ])
    const blob = new Blob(['%PDF'])
    mockDownload.mockResolvedValue(blob)
    wrap(<ReportExportsPanel projectId="p1" refreshToken={0} />)

    expect(await screen.findByText('Ready')).toBeInTheDocument()
    expect(screen.getByText('Failed')).toBeInTheDocument()
    expect(screen.getAllByText(/PDF · last 30 days · all runs · 1 suite/)).toHaveLength(2)
    expect(screen.getByText('The export could not be generated: boom')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Download/ }))
    await waitFor(() => expect(mockDownloadBlob).toHaveBeenCalledWith(blob, 'summary-pay-30d-window.pdf'))
    expect(mockDownload).toHaveBeenCalledWith('a')
  })

  it('retries a failed export and re-reads the list', async () => {
    mockList.mockResolvedValueOnce([exp({ id: 'b', status: 'failed', error: 'x', retryable: true })])
    mockList.mockResolvedValue([exp({ id: 'b', status: 'queued' })])
    mockRetry.mockResolvedValue(exp({ id: 'b', status: 'queued' }))
    wrap(<ReportExportsPanel projectId="p1" refreshToken={0} />)

    fireEvent.click(await screen.findByRole('button', { name: /Retry/ }))
    await waitFor(() => expect(mockRetry).toHaveBeenCalledWith('b'))
    expect(await screen.findByText('Queued')).toBeInTheDocument()
  })

  it('tells the reader when a running export becomes ready', async () => {
    mockList.mockResolvedValueOnce([exp({ id: 'a', status: 'running' })])
    mockList.mockResolvedValue([exp({ id: 'a', status: 'completed', filename: 'f.pdf' })])
    const { rerender } = wrap(<ReportExportsPanel projectId="p1" refreshToken={0} />)
    expect(await screen.findByText('Generating…')).toBeInTheDocument()
    expect(toastSuccess).not.toHaveBeenCalled()

    // The page bumps the token when it queues an export: the list re-reads.
    rerender(
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
        <ReportExportsPanel projectId="p1" refreshToken={1} />
      </SWRConfig>,
    )
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledTimes(1))
    expect(toastSuccess.mock.calls[0][1]).toMatchObject({ id: 'report-export-a' })
  })
})
