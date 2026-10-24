/**
 * W12 SRCH-01 (contract §2.1, test 12): the Searchable data card, moved out of
 * SearchPage with its test ids and sentences, plus Re-index now — for a QA lead
 * or an admin with one project pinned, never for a QA engineer or in All
 * Projects; the queued line; the server's refusal inline; the 10 s poll that
 * stops once the index was written after the queue time.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import toast from 'react-hot-toast'
import SearchableDataCard from './SearchableDataCard'
import { useAuthStore } from '@/store/authStore'
import type { IndexStatus } from '@/types/search'

const mockGetIndexStatus = vi.fn()
const mockTriggerReindex = vi.fn()
vi.mock('@/services/searchService', () => ({
  searchService: {
    getIndexStatus: (...args: unknown[]) => mockGetIndexStatus(...args),
    triggerReindex: (...args: unknown[]) => mockTriggerReindex(...args),
  },
}))
vi.mock('react-hot-toast', () => ({ default: { error: vi.fn(), success: vi.fn() } }))

const TOTALS = { test_case: 84, test_run: 32, suite: 12, defect: 5, flaky_test: 3, release: 4 }
const OLD: IndexStatus = { status: 'healthy', document_count: 140, last_indexed_at: '2026-10-24T09:00:00Z' }

function signInAs(role: string) {
  useAuthStore.setState({ user: { id: 'u1', username: 'u', email: 'u@example.com', role } as never })
}

function renderCard(projectId: string | null = 'p1') {
  return render(
    <SWRConfig value={{ provider: () => new Map() }}>
      <SearchableDataCard totals={TOTALS} indexStatus={OLD} projectId={projectId} />
    </SWRConfig>,
  )
}

const line = () => screen.getByTestId('semantic-index-line')

beforeEach(() => {
  mockGetIndexStatus.mockReset()
  mockTriggerReindex.mockReset()
  vi.mocked(toast.success).mockClear()
  mockGetIndexStatus.mockResolvedValue(OLD)
  mockTriggerReindex.mockResolvedValue({ task_id: 't1', status: 'queued' })
})

afterEach(() => {
  vi.useRealTimers()
  useAuthStore.setState({ user: null })
})

describe('SearchableDataCard — the rail (moved)', () => {
  it('six keyword rows with their counts, and the semantic line', async () => {
    renderCard()
    expect(screen.getByTestId('searchable-data-summary')).toHaveTextContent('6 entity types · semantic: ready')
    for (const label of ['Tests', 'Runs', 'Suites', 'Defects', 'Flaky', 'Releases']) {
      expect(screen.getByLabelText(`${label}: keyword search`)).toHaveTextContent('Keyword')
    }
    expect(screen.getByText('84 rows · keyword match')).toBeInTheDocument()
    expect(within(line()).getByLabelText('Semantic index: Ready')).toBeInTheDocument()
    expect(line()).toHaveTextContent(/140 docs · last indexed/)
  })
})

describe('SearchableDataCard — Re-index now', () => {
  it.each(['QA_LEAD', 'ADMIN'])('a %s with a project pinned has the button', (role) => {
    signInAs(role)
    renderCard('p1')
    expect(within(line()).getByRole('button', { name: 'Re-index now' })).toHaveClass('btn-secondary', 'btn-sm')
  })

  it.each(['QA_ENGINEER', 'TESTER', 'VIEWER'])('a %s never sees it', (role) => {
    signInAs(role)
    renderCard('p1')
    expect(screen.queryByRole('button', { name: /Re-index/ })).toBeNull()
  })

  it('nobody sees it in All Projects', () => {
    signInAs('ADMIN')
    renderCard(null)
    expect(screen.queryByRole('button', { name: /Re-index/ })).toBeNull()
  })

  it('queues a full re-index of the pinned project, then polls every 10 s until the index was written after it', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.setSystemTime(new Date('2026-10-24T14:05:00Z'))
    signInAs('QA_LEAD')
    renderCard('p1')
    await waitFor(() => expect(mockGetIndexStatus).toHaveBeenCalledTimes(1))

    fireEvent.click(screen.getByRole('button', { name: 'Re-index now' }))
    await waitFor(() => expect(mockTriggerReindex).toHaveBeenCalledWith('p1', true))
    expect(await within(line()).findByRole('button', { name: 'Queued' })).toBeDisabled()
    expect(toast.success).toHaveBeenCalledWith('Re-index queued.')
    expect(line()).toHaveTextContent('Re-index queued at 14:05 UTC — this line updates when it finishes.')

    // Still the old index after 10 s: asked again, still queued.
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    await waitFor(() => expect(mockGetIndexStatus).toHaveBeenCalledTimes(2))
    expect(within(line()).getByRole('button', { name: 'Queued' })).toBeInTheDocument()

    // The index was written after the queue time: the line updates and the poll stops.
    mockGetIndexStatus.mockResolvedValue({ ...OLD, document_count: 146, last_indexed_at: new Date(Date.now() + 1_000).toISOString() })
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    await waitFor(() => expect(line()).toHaveTextContent('146 docs'))
    expect(line()).not.toHaveTextContent('Re-index queued')
    expect(within(line()).getByRole('button', { name: 'Re-index now' })).toBeEnabled()
    const asked = mockGetIndexStatus.mock.calls.length
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
    expect(mockGetIndexStatus.mock.calls.length, 'no poll after it finished').toBe(asked)
  })

  it('after 10 minutes it stops polling and says the re-index has not finished yet', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.setSystemTime(new Date('2026-10-24T14:05:00Z'))
    signInAs('ADMIN')
    renderCard('p1')
    fireEvent.click(screen.getByRole('button', { name: 'Re-index now' }))
    await within(line()).findByRole('button', { name: 'Queued' })
    await act(async () => { await vi.advanceTimersByTimeAsync(10 * 60_000 + 10_000) })
    await waitFor(() => expect(line()).toHaveTextContent('Re-index queued at 14:05 UTC has not finished yet.'))
    const asked = mockGetIndexStatus.mock.calls.length
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
    expect(mockGetIndexStatus.mock.calls.length).toBe(asked)
  })

  it('a refusal is the server\'s sentence, under the line, and nothing is queued', async () => {
    signInAs('QA_LEAD')
    mockTriggerReindex.mockRejectedValue({ response: { status: 403, data: { detail: 'Triggering a reindex requires QA_LEAD or higher' } } })
    renderCard('p1')
    fireEvent.click(screen.getByRole('button', { name: 'Re-index now' }))
    const refusal = await within(line()).findByRole('alert')
    expect(refusal).toHaveTextContent('Triggering a reindex requires QA_LEAD or higher')
    expect(refusal.className).toContain('--ui-danger-ink')
    expect(line()).not.toHaveTextContent('Re-index queued')
    expect(toast.success).not.toHaveBeenCalled()
  })
})
