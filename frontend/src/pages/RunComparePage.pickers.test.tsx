/**
 * FAIL-06 on `/runs/compare`: the Left and Right controls are server-backed
 * pickers; the latest-suite section states "N runs in suite · latest Run #… ·
 * branch" as text (no disabled `<select>` anywhere), with "Pick a different
 * baseline"; a deep link's two runs are labelled by reading each once.
 */
import { createElement, type ReactNode } from 'react'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { TestRun } from '@/types/runs'

vi.mock('@/hooks/useRunCompare', () => ({
  useRunCompare: () => ({ compare: undefined, isLoading: false, isError: false, error: undefined }),
  useLatestSuiteCompare: () => ({ compare: undefined, isLoading: false, isError: false, error: undefined }),
  extractCompareErrorMessage: () => 'Failed to load compare',
}))
const SUITE_RUNS = { data: { items: [{ id: 'x', primary_suite_name: 'Auth', suite_names: ['Auth'] }] }, isLoading: false }
vi.mock('@/hooks/useRuns', () => ({ useRuns: () => SUITE_RUNS }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: 'all',
  useProjectStore: (selector: (s: unknown) => unknown) => selector({ activeProjectId: 'p1', activeProject: { id: 'p1', name: 'Checkout' } }),
}))
const svc = vi.hoisted(() => ({ searchRuns: vi.fn(), getRun: vi.fn() }))
vi.mock('@/services/runListService', () => ({ runListService: svc }))

import RunComparePage from './RunComparePage'

const LEFT = '11111111-1111-4111-8111-111111111111'
const RIGHT = '22222222-2222-4222-8222-222222222222'

function run(id: string, seq: number, over: Partial<TestRun> = {}): TestRun {
  return {
    id, build_number: 400 + seq, run_seq: seq, status: 'PASSED', branch: 'main',
    passed_tests: 1, failed_tests: 0, skipped_tests: 0, total_tests: 1, pass_rate: 100,
    created_at: '2026-10-01T00:00:00Z', primary_suite_name: 'Auth', ...over,
  }
}

function Probe() {
  const { search } = useLocation()
  return <output data-testid="where">{search}</output>
}
const url = () => new URLSearchParams(screen.getByTestId('where').textContent ?? '')

const wrapper = ({ children }: { children: ReactNode }) =>
  createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0 } }, children)

function renderPage(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="*" element={<><RunComparePage /><Probe /></>} /></Routes>
    </MemoryRouter>,
    { wrapper },
  )
}

beforeEach(() => {
  svc.searchRuns.mockReset().mockImplementation(async (_p: string | null, params: { suite_name?: string | null }) => (
    params.suite_name === 'Auth'
      ? { items: [run(LEFT, 431, { branch: 'main' })], total: 12, page: 1, size: 20, pages: 1 }
      : { items: [], total: 0, page: 1, size: 20, pages: 0 }
  ))
  svc.getRun.mockReset().mockImplementation(async (id: string) => (id === LEFT ? run(LEFT, 7) : run(RIGHT, 9, { status: 'FAILED' })))
})

describe('RunComparePage — the run pickers (FAIL-06)', () => {
  it('"Runs in suite" is text from the server — no disabled select anywhere on the page', async () => {
    const { container } = renderPage('/runs/compare?mode=latest&suite=Auth')
    await waitFor(() => expect(container.querySelector('[data-runs-in-suite-text]')).toHaveTextContent('12 runs in suite · latest Run #431 · main'))
    expect(container.querySelector('select[disabled]')).toBeNull()
    expect(svc.searchRuns).toHaveBeenCalledWith('p1', { q: '', suite_name: 'Auth', page: 1, size: 20, days: 0 })
  })

  it('no suite chosen: says so, and asks nothing', async () => {
    const { container } = renderPage('/runs/compare')
    expect(container.querySelector('[data-runs-in-suite-text]')).toHaveTextContent('Select a suite to view runs')
    expect(screen.queryByRole('button', { name: 'Pick a different baseline' })).toBeNull()
    await new Promise((r) => setTimeout(r, 0))
    expect(svc.searchRuns).not.toHaveBeenCalled()
  })

  it('"Pick a different baseline" switches to manual mode with the suite and opens the Left picker', async () => {
    renderPage('/runs/compare?mode=latest&suite=Auth')
    fireEvent.click(await screen.findByRole('button', { name: 'Pick a different baseline' }))
    expect(url().get('mode')).toBe('manual')
    expect(url().get('suite')).toBe('Auth')
    const dialog = await screen.findByRole('dialog', { name: 'Left run (baseline)' })
    expect(within(dialog).getByRole('searchbox', { name: 'Search runs' })).toBeInTheDocument()
    expect(screen.getByTestId('compare-left-picker')).toHaveAttribute('aria-expanded', 'true')
    // The picker is narrowed to that suite.
    await waitFor(() => expect(within(dialog).getByText('Showing 1 of 12 · all releases')).toBeInTheDocument())
  })

  it('a deep link\'s two runs are labelled from one read each', async () => {
    renderPage(`/runs/compare?mode=manual&left=${LEFT}&right=${RIGHT}`)
    await waitFor(() => expect(screen.getByTestId('compare-left-picker')).toHaveTextContent(/^Run #7 · PASSED · main · /))
    await waitFor(() => expect(screen.getByTestId('compare-right-picker')).toHaveTextContent(/^Run #9 · FAILED · main · /))
    expect(svc.getRun).toHaveBeenCalledWith(LEFT, { suppressToast: true })
    expect(svc.getRun).toHaveBeenCalledWith(RIGHT, { suppressToast: true })
  })

  it('a picked baseline and target go to the URL on Compare (the contract is unchanged)', async () => {
    svc.searchRuns.mockImplementation(async () => ({ items: [run(LEFT, 7), run(RIGHT, 9)], total: 2, page: 1, size: 20, pages: 1 }))
    renderPage('/runs/compare')
    fireEvent.click(screen.getByTestId('compare-left-picker'))
    const left = await screen.findByRole('dialog', { name: 'Left run (baseline)' })
    await waitFor(() => expect(left.querySelectorAll('[data-run-option]')).toHaveLength(2))
    fireEvent.click(left.querySelector(`[data-run-option="${LEFT}"]`) as HTMLElement)
    fireEvent.click(screen.getByTestId('compare-right-picker'))
    const right = await screen.findByRole('dialog', { name: 'Right run (target)' })
    await waitFor(() => expect(right.querySelectorAll('[data-run-option]')).toHaveLength(2))
    fireEvent.click(right.querySelector(`[data-run-option="${RIGHT}"]`) as HTMLElement)
    fireEvent.click(screen.getByRole('button', { name: /^Compare$/ }))
    expect(url().get('mode')).toBe('manual')
    expect(url().get('left')).toBe(LEFT)
    expect(url().get('right')).toBe(RIGHT)
  })

  it('the empty state names the new way in', () => {
    renderPage('/runs/compare')
    expect(screen.getByText('Use the suite-first default above, or pick (or paste) a baseline run and a target run for an explicit historical comparison.')).toBeInTheDocument()
  })
})
