/**
 * W14 AGT-12 (`W14-contract.md` §2.4 test 6): the bulk-job panel over the REAL `useBulkJob` (SWR) and a mocked
 * `agentService`: the two lines, the expired-record note, Cancel only for a QA engineer while the job is
 * active (through an `InlineConfirm`), polling every 5 s only while active (fake timers), ONE runs-list
 * revalidation at the finish, the not-started note, and a gone job (404) that stops asking.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { BulkJob } from '@/types/agentOps'
import BulkJobProgress, { CANCEL_QUESTION } from './BulkJobProgress'
import { BULK_JOB_GONE, BULK_RECORD_EXPIRED } from './bulkJobModel'

const service = vi.hoisted(() => ({ getBulkJob: vi.fn(), cancelBulkJob: vi.fn() }))
vi.mock('@/services/agentService', () => ({ default: service }))

const JOB_ID = 'b0000000-0000-4000-8000-000000000001'

function job(over: Partial<BulkJob> = {}): BulkJob {
  return {
    bulk_job_id: JOB_ID,
    workflow_type: 'offline',
    source: 'filter',
    state: 'active',
    counters_source: 'redis',
    requested: 37,
    queued: 35,
    skipped_in_progress: 2,
    not_found: 0,
    matched: 37,
    truncated: false,
    progress: { started: 10, in_progress: 4, completed: 6, passed: 5, failed: 1, not_started: 25 },
    note: null,
    ...over,
  }
}

function renderPanel(props: Partial<React.ComponentProps<typeof BulkJobProgress>> = {}) {
  const onSettled = vi.fn()
  const onClose = vi.fn()
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false }}>
      <MemoryRouter>
        <BulkJobProgress jobId={JOB_ID} canCancel onSettled={onSettled} onClose={onClose} {...props} />
      </MemoryRouter>
    </SWRConfig>,
  )
  return { onSettled, onClose }
}

const panel = () => screen.getByRole('region', { name: 'Bulk AI analysis' })

beforeEach(() => {
  service.getBulkJob.mockReset()
  service.cancelBulkJob.mockReset()
})
afterEach(() => {
  vi.useRealTimers()
})

describe('BulkJobProgress', () => {
  it('shows the request line, the progress line and the started-of-queued gauge', async () => {
    service.getBulkJob.mockResolvedValue(job())
    renderPanel()
    expect(await screen.findByText('37 requested · 35 queued · 2 already running')).toBeInTheDocument()
    expect(screen.getByText('6 completed · 5 passed · 1 failed · 4 running · 25 not started')).toBeInTheDocument()
    expect(screen.getByRole('meter', { name: 'Pipelines started of those queued' })).toHaveAttribute('aria-valuenow', '10')
    expect(panel()).toHaveTextContent('Running')
    expect(screen.getByRole('link', { name: 'Open Pipeline runs' })).toHaveAttribute('href', '/agents')
    expect(service.getBulkJob).toHaveBeenCalledWith(JOB_ID)
  })

  it('an expired request record is said, never shown as zeros', async () => {
    service.getBulkJob.mockResolvedValue(job({
      counters_source: 'postgres', requested: null, queued: null, skipped_in_progress: null, not_found: null, matched: null,
      progress: { started: 3, in_progress: 0, completed: 3, passed: 2, failed: 1, not_started: null },
      state: 'finished',
    }))
    renderPanel()
    expect(await screen.findByText(BULK_RECORD_EXPIRED)).toBeInTheDocument()
    expect(panel()).not.toHaveTextContent('requested')
    expect(panel()).not.toHaveTextContent('0 queued')
    expect(screen.queryByRole('meter')).toBeNull()
  })

  it('offers Cancel only to a QA engineer, only while the job is active', async () => {
    service.getBulkJob.mockResolvedValue(job())
    renderPanel({ canCancel: false })
    await screen.findByText('37 requested · 35 queued · 2 already running')
    expect(screen.queryByRole('button', { name: 'Cancel' })).toBeNull()
  })

  it('no Cancel once the job finished; the not-started note says none of them failed', async () => {
    const note = '25 queued pipelines never started (a duplicate request, the cancelled job or a worker restart dropped them). None of them is counted as failed.'
    service.getBulkJob.mockResolvedValue(job({ state: 'finished', note, progress: { started: 10, in_progress: 0, completed: 10, passed: 9, failed: 1, not_started: 25 } }))
    renderPanel()
    expect(await screen.findByText(note)).toHaveAttribute('role', 'note')
    expect(screen.queryByRole('button', { name: 'Cancel' })).toBeNull()
    expect(panel()).toHaveTextContent('Finished')
  })

  it('Cancel asks first, then cancels; the settled job revalidates the runs list once', async () => {
    service.getBulkJob.mockResolvedValue(job())
    service.cancelBulkJob.mockResolvedValue(job({ state: 'cancelled', cancelled_at: '2026-10-25T10:00:00Z' }))
    const { onSettled } = renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }))
    expect(service.cancelBulkJob).not.toHaveBeenCalled()
    expect(screen.getByText(CANCEL_QUESTION)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel job' }))
    await waitFor(() => expect(service.cancelBulkJob).toHaveBeenCalledWith(JOB_ID))
    await waitFor(() => expect(panel()).toHaveTextContent('Cancelled'))
    expect(onSettled).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: 'Cancel' })).toBeNull()
  })

  it('polls every 5 s while active and stops at the finish (one revalidation)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    service.getBulkJob
      .mockResolvedValueOnce(job())
      .mockResolvedValueOnce(job({ progress: { started: 30, in_progress: 2, completed: 28, passed: 26, failed: 2, not_started: 5 } }))
      .mockResolvedValue(job({ state: 'finished', progress: { started: 35, in_progress: 0, completed: 35, passed: 33, failed: 2, not_started: 0 } }))
    const { onSettled } = renderPanel()
    await screen.findByText('6 completed · 5 passed · 1 failed · 4 running · 25 not started')
    await act(async () => { await vi.advanceTimersByTimeAsync(5_100) })
    await screen.findByText('28 completed · 26 passed · 2 failed · 2 running · 5 not started')
    await act(async () => { await vi.advanceTimersByTimeAsync(5_100) })
    await screen.findByText('35 completed · 33 passed · 2 failed · 0 running · 0 not started')
    const asked = service.getBulkJob.mock.calls.length
    expect(asked).toBe(3)
    expect(onSettled).toHaveBeenCalledTimes(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000) })
    expect(service.getBulkJob).toHaveBeenCalledTimes(asked)
    expect(onSettled).toHaveBeenCalledTimes(1)
  })

  it('a gone job (404: expired, or not this reader\'s) is said once and never asked again', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    service.getBulkJob.mockRejectedValue(Object.assign(new Error('404'), { response: { status: 404, data: { detail: 'Bulk job not found' } } }))
    renderPanel()
    expect(await screen.findByText(BULK_JOB_GONE)).toBeInTheDocument()
    const asked = service.getBulkJob.mock.calls.length
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000) })
    expect(service.getBulkJob).toHaveBeenCalledTimes(asked)
  })

  it('Close hands back to the page (which clears ?bulk_job=)', async () => {
    service.getBulkJob.mockResolvedValue(job())
    const { onClose } = renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: 'Close' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
