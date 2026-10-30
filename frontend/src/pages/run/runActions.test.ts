/**
 * W23c (§3.4): the run page's PDF action says WHY a download was refused, once.
 *
 * Once the review gate is enforced, `GET /api/v1/reports/runs/{id}/pdf` answers an unreviewed report with a 409
 * whose body (a Blob: the request asks for one) is `{detail: {code: "report_pending_review", message}}`. The
 * request is `suppressToast` (the interceptor cannot read a Blob and would say "Request failed with status code
 * 409"), and `downloadRunPdf` toasts the server's sentence through the existing `aiHistoryExportErrorMessage`.
 * Only the HTTP client (`services/api`) and the toast are mocked; both services run for real.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiGet, toastError } = vi.hoisted(() => ({ apiGet: vi.fn(), toastError: vi.fn() }))

vi.mock('@/services/api', () => ({ api: { get: (...a: unknown[]) => apiGet(...a) } }))
vi.mock('react-hot-toast', () => ({ default: { error: (...a: unknown[]) => toastError(...a), success: vi.fn() } }))

import { downloadRunPdf, PDF_EXPORT_FAILED } from './runActions'

const PENDING_MESSAGE =
  'This AI-generated report has not been accepted by a human reviewer, so it cannot be distributed.'

function axiosError(status: number | null, data?: unknown) {
  return Object.assign(new Error(status ? `Request failed with status code ${status}` : 'Network Error'), {
    isAxiosError: true,
    response: status ? { status, data } : undefined,
  })
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('downloadRunPdf', () => {
  it('asks for the PDF without the interceptor\'s toast', async () => {
    apiGet.mockRejectedValue(axiosError(null))
    await downloadRunPdf('run-1')
    expect(apiGet).toHaveBeenCalledWith('/api/v1/reports/runs/run-1/pdf', {
      params: { layout: 'executive' },
      responseType: 'blob',
      suppressToast: true,
    })
  })

  it('a 409 Blob body toasts the server\'s message once, and not "PDF export failed"', async () => {
    const body = new Blob(
      [JSON.stringify({ detail: { code: 'report_pending_review', message: PENDING_MESSAGE } })],
      { type: 'application/json' },
    )
    apiGet.mockRejectedValue(axiosError(409, body))
    await downloadRunPdf('run-1')
    expect(toastError).toHaveBeenCalledTimes(1)
    expect(toastError).toHaveBeenCalledWith(PENDING_MESSAGE)
  })

  it('a network error toasts "PDF export failed"', async () => {
    apiGet.mockRejectedValue(axiosError(null))
    await downloadRunPdf('run-1')
    expect(toastError).toHaveBeenCalledTimes(1)
    expect(toastError).toHaveBeenCalledWith(PDF_EXPORT_FAILED)
  })

  it('an unreadable body, or an error that is not the HTTP client\'s, toasts "PDF export failed"', async () => {
    apiGet.mockRejectedValue(axiosError(500, new Blob(['<html>bad gateway</html>'], { type: 'text/html' })))
    await downloadRunPdf('run-1')
    apiGet.mockRejectedValue(new TypeError('Failed to fetch dynamically imported module'))
    await downloadRunPdf('run-1')
    expect(toastError.mock.calls).toEqual([[PDF_EXPORT_FAILED], [PDF_EXPORT_FAILED]])
  })
})
