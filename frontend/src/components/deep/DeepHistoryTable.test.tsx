/**
 * W14 AGT-07 (`W14-contract.md` §2.5 test 5, D-14): "Recent runs" filled from the project's deep-investigation
 * history, and the "Investigated runs · N" mode paging through it. A run the history does not list keeps "—"
 * (never an invented number); page 2 onwards is asked only when opened; no project → no read, no chip.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useDeepHistory } from '@/hooks/useAgentOps'
import { HISTORY_FIXTURE, idleSwr } from '@/test/agentOpsMocks'
import type { DeepHistory } from '@/types/agentOps'
import DeepHistoryTable from './DeepHistoryTable'
import { HISTORY_LOAD_ERROR, type DeepRunRow } from './deepRunModel'

vi.mock('@/hooks/useAgentOps', async () => (await import('@/test/agentOpsMocks')).agentOpsHookMocks())

const row = (runId: string, runSeq: number, over: Partial<DeepRunRow> = {}): DeepRunRow => ({
  runId, runIdLabel: runId.slice(0, 8), runSeq, suiteLabel: 'e2e', suiteNames: ['e2e'], whenRel: '3d ago',
  whenAbs: 'Oct 19, 08:00', failures: 9, clusters: null, findings: null, avgConf: null, cost: null, status: null, ...over,
})
/** The focused run (live facts known), two investigated runs, one never investigated. */
const RECENT: DeepRunRow[] = [
  row('run-1', 39, { clusters: 5, findings: 6, cost: 0.42, status: 'running' }),
  row('run-2', 38),
  row('run-3', 37),
  row('run-9', 31),
]

function answer(pages: Record<number, DeepHistory | Error>) {
  vi.mocked(useDeepHistory).mockImplementation((projectId: string | null, page: number) => {
    if (!projectId) return idleSwr()
    const value = pages[page]
    return value instanceof Error ? idleSwr({ error: value }) : idleSwr({ data: value })
  })
}

function renderTable(projectId: string | null = 'project-1', onOpen = vi.fn()) {
  render(<MemoryRouter><DeepHistoryTable rows={RECENT} projectId={projectId} onOpen={onOpen} /></MemoryRouter>)
  return onOpen
}
const bodyRows = () => within(screen.getByRole('table')).getAllByRole('row').slice(1)
const cells = (tr: HTMLElement) => Array.from(tr.querySelectorAll('td'), (td) => td.textContent)

beforeEach(() => {
  vi.mocked(useDeepHistory).mockReset()
  answer({ 1: HISTORY_FIXTURE })
})

describe('DeepHistoryTable', () => {
  it('fills the recent runs the history lists; keeps the focused run\'s live facts; "—" for the rest', () => {
    renderTable()
    expect(screen.getByRole('heading', { level: 3, name: /^Recent runs/ })).toBeInTheDocument()
    const [focused, second, third, never] = bodyRows()
    // Live facts win; the one the page did not know (its average confidence) comes from the history.
    expect(cells(focused).slice(3)).toEqual(['5', '6', '0.82', '$0.42', 'Running'])
    expect(cells(second).slice(3)).toEqual(['2', '3', '0.91', '$0.00', 'Complete'])
    expect(cells(third).slice(3)).toEqual(['—', '0', '—', '$0.00', 'Failed'])
    expect(cells(never).slice(3)).toEqual(['—', '—', '—', '—', '—'])
    expect(useDeepHistory).toHaveBeenCalledWith('project-1', 1)
    // Page 2 is never asked on load.
    expect(vi.mocked(useDeepHistory).mock.calls.filter(([p, page]) => p && page !== 1)).toEqual([])
  })

  it('the columns are the same dense table as before', () => {
    renderTable()
    const table = screen.getByRole('table')
    expect(table).toHaveAttribute('data-density', 'dense')
    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent))
      .toEqual(['Test Suite', 'Run', 'Failures', 'Clusters', 'Findings', 'Avg conf', 'Cost', 'Status'])
  })

  it('"Investigated runs · 4" switches to the history and back; a row opens its run', () => {
    const onOpen = renderTable()
    fireEvent.click(screen.getByRole('button', { name: 'Investigated runs · 4' }))
    expect(screen.getByRole('heading', { level: 3, name: /^Investigated runs/ })).toBeInTheDocument()
    expect(screen.getByText('4 investigated runs')).toBeInTheDocument()
    expect(bodyRows()).toHaveLength(4)
    expect(bodyRows()[0]).toHaveTextContent('Run #39')
    // run-4's pipeline reported no cost: unknown, not $0.00.
    expect(cells(bodyRows()[3])[6]).toBe('—')
    fireEvent.click(bodyRows()[1])
    expect(onOpen).toHaveBeenCalledWith('run-2')
    fireEvent.click(screen.getByRole('button', { name: 'Investigated runs · 4' }))
    expect(screen.getByRole('heading', { level: 3, name: /^Recent runs/ })).toBeInTheDocument()
  })

  it('pages: Next asks page 2 only then; Prev goes back', () => {
    const second: DeepHistory = { ...HISTORY_FIXTURE, page: 2, total: 14, items: [HISTORY_FIXTURE.items[0]].map((i) => ({ ...i, run_id: 'run-11', run_seq: 29 })) }
    answer({ 1: { ...HISTORY_FIXTURE, total: 14 }, 2: second })
    renderTable()
    fireEvent.click(screen.getByRole('button', { name: 'Investigated runs · 14' }))
    expect(screen.getByText('Page 1 of 2')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Prev' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(useDeepHistory).toHaveBeenCalledWith('project-1', 2)
    expect(bodyRows()).toHaveLength(1)
    expect(bodyRows()[0]).toHaveTextContent('Run #29')
    fireEvent.click(screen.getByRole('button', { name: 'Prev' }))
    expect(screen.getByText('Page 1 of 2')).toBeInTheDocument()
  })

  it('no project: no read and no chip; a failed read says so', () => {
    const { rerender } = render(<MemoryRouter><DeepHistoryTable rows={RECENT} projectId={null} onOpen={vi.fn()} /></MemoryRouter>)
    expect(useDeepHistory).toHaveBeenCalledWith(null, 1)
    expect(screen.queryByRole('button', { name: /Investigated runs/ })).toBeNull()
    expect(cells(bodyRows()[1]).slice(3)).toEqual(['—', '—', '—', '—', '—'])
    answer({ 1: new Error('503') })
    rerender(<MemoryRouter><DeepHistoryTable rows={RECENT} projectId="project-1" onOpen={vi.fn()} /></MemoryRouter>)
    expect(screen.getByText(HISTORY_LOAD_ERROR)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Investigated runs/ })).toBeNull()
  })
})
