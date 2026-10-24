/**
 * FAIL-06: the Run Compare picker asks the SERVER — a search over every run
 * the reader can see, 20 a page with "Load older" — and checks a pasted run
 * id with the server, saying the same thing for a run that does not exist
 * and one the reader may not see.
 */
import { createElement, type ReactNode } from 'react'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { TestRun } from '@/types/runs'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'

const svc = vi.hoisted(() => ({ searchRuns: vi.fn(), getRun: vi.fn() }))
vi.mock('@/services/runListService', () => ({ runListService: svc }))

import RunPicker from './RunPicker'

const RUN_UUID = '0f8fad5b-d9cb-469f-a165-70867728950e'

function run(i: number, over: Partial<TestRun> = {}): TestRun {
  return {
    id: `00000000-0000-4000-8000-${String(i).padStart(12, '0')}`, build_number: 200 + i, run_seq: i,
    status: 'PASSED', branch: 'main', passed_tests: 1, failed_tests: 0, skipped_tests: 0, total_tests: 1, pass_rate: 100,
    created_at: '2026-09-01T00:00:00Z', ...over,
  }
}

/** The fake `/runs`: `total` runs, newest (highest number) first, 20 a page. */
function serveRuns(total: number) {
  svc.searchRuns.mockImplementation(async (_projectId: string | null, params: { page: number; size: number }) => {
    const all = Array.from({ length: total }, (_, i) => run(total - i))
    const items = all.slice((params.page - 1) * params.size, params.page * params.size)
    return { items, total, page: params.page, size: params.size, pages: Math.ceil(total / params.size) }
  })
}

const wrapper = ({ children }: { children: ReactNode }) =>
  createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0 } }, children)

function renderPicker(props: Partial<Parameters<typeof RunPicker>[0]> = {}) {
  const onChange = vi.fn()
  const utils = render(
    <RunPicker label="Left run (baseline)" value="" onChange={onChange} testId="compare-left-picker" {...props} />,
    { wrapper },
  )
  return { ...utils, onChange }
}

const openPicker = () => fireEvent.click(screen.getByTestId('compare-left-picker'))
const dialog = () => screen.getByRole('dialog', { name: 'Left run (baseline)' })
const searchCalls = () => svc.searchRuns.mock.calls.map(([projectId, params]) => ({ projectId, ...params }))
const options = () => dialog().querySelectorAll('[data-run-option]')

beforeEach(() => {
  svc.searchRuns.mockReset()
  svc.getRun.mockReset()
  useProjectStore.setState({ activeProjectId: 'p1' })
})
afterEach(() => {
  useProjectStore.setState({ activeProjectId: ALL_PROJECTS_ID })
})

describe('RunPicker — the server is searched, not the page\'s 100 runs', () => {
  it('a closed button shows "Select a run"; opening asks page 1 of all time, every release', async () => {
    serveRuns(3)
    renderPicker()
    const button = screen.getByTestId('compare-left-picker')
    expect(button).toHaveTextContent('Select a run')
    expect(button).toHaveAttribute('aria-haspopup', 'dialog')
    expect(button).toHaveAttribute('aria-expanded', 'false')
    openPicker()
    expect(button).toHaveAttribute('aria-expanded', 'true')
    await waitFor(() => expect(options()).toHaveLength(3))
    expect(searchCalls()[0]).toEqual({ projectId: 'p1', q: '', suite_name: null, page: 1, size: 20, days: 0 })
    expect(within(dialog()).getByText('Showing 3 of 3 · all releases')).toBeInTheDocument()
  })

  it('typing asks with q ONCE, after the pause', async () => {
    serveRuns(3)
    renderPicker()
    openPicker()
    const box = within(dialog()).getByRole('searchbox', { name: 'Search runs' })
    expect(box).toHaveAttribute('placeholder', 'Build, branch, job or commit…')
    for (const text of ['2', '22', '228']) fireEvent.change(box, { target: { value: text } })
    await waitFor(() => expect(searchCalls().some((c) => c.q === '228')).toBe(true))
    expect(searchCalls().map((c) => c.q).filter((q) => q !== '')).toEqual(['228'])
  })

  it('Load older asks page 2 and appends; the count follows; it goes when everything is shown', async () => {
    serveRuns(25)
    renderPicker()
    openPicker()
    await waitFor(() => expect(options()).toHaveLength(20))
    expect(within(dialog()).getByText('Showing 20 of 25 · all releases')).toBeInTheDocument()
    fireEvent.click(within(dialog()).getByRole('button', { name: 'Load older' }))
    await waitFor(() => expect(options()).toHaveLength(25))
    expect(searchCalls().map((c) => c.page)).toContain(2)
    expect(within(dialog()).getByText('Showing 25 of 25 · all releases')).toBeInTheDocument()
    expect(within(dialog()).queryByRole('button', { name: 'Load older' })).toBeNull()
  })

  it('narrows to the suite it is given', async () => {
    serveRuns(1)
    renderPicker({ suiteName: 'Auth' })
    openPicker()
    await waitFor(() => expect(searchCalls()[0]).toMatchObject({ suite_name: 'Auth' }))
  })

  it('picking a run calls onChange with its id and closes', async () => {
    serveRuns(2)
    const { onChange } = renderPicker()
    openPicker()
    await waitFor(() => expect(options()).toHaveLength(2))
    fireEvent.click(options()[1])
    expect(onChange).toHaveBeenCalledWith(run(1).id)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('Escape and a click outside close it', async () => {
    serveRuns(1)
    renderPicker()
    openPicker()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    openPicker()
    fireEvent.mouseDown(document.body)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('shows the selected run in today\'s words (read once, by id)', async () => {
    svc.getRun.mockResolvedValue(run(7, { id: RUN_UUID, status: 'FAILED', branch: 'release/2.4' }))
    renderPicker({ value: RUN_UUID })
    await waitFor(() => expect(screen.getByTestId('compare-left-picker')).toHaveTextContent(/^Run #7 · FAILED · release\/2\.4 · /))
    expect(svc.getRun).toHaveBeenCalledWith(RUN_UUID, { suppressToast: true })
  })
})

describe('RunPicker — Paste run ID is checked by the server', () => {
  const paste = (text: string) => {
    fireEvent.change(within(dialog()).getByRole('textbox', { name: 'Paste run ID' }), { target: { value: text } })
    fireEvent.click(within(dialog()).getByRole('button', { name: 'Use' }))
  }
  const error = () => dialog().querySelector('[data-run-picker-error]')

  beforeEach(() => serveRuns(0))

  it('not a UUID: says so, and asks nothing', () => {
    renderPicker()
    openPicker()
    paste('abc')
    expect(error()).toHaveTextContent('Not a run ID (expected a UUID).')
    expect(svc.getRun).not.toHaveBeenCalled()
  })

  it('404 and 403 read the SAME words: nothing is said about runs the reader cannot see', async () => {
    renderPicker()
    openPicker()
    svc.getRun.mockRejectedValueOnce(Object.assign(new Error('Run not found'), { response: { status: 404, data: { detail: 'Run not found' } } }))
    paste(RUN_UUID)
    await waitFor(() => expect(error()).not.toBeNull())
    const notFound = error()?.textContent
    svc.getRun.mockRejectedValueOnce(Object.assign(new Error('Not a member of this project'), { response: { status: 403, data: { detail: 'Not a member of this project' } } }))
    paste(RUN_UUID.toUpperCase())
    await waitFor(() => expect(svc.getRun).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(error()).not.toBeNull())
    expect(error()?.textContent).toBe(notFound)
    expect(notFound).toBe('Run not found or not accessible.')
    expect(svc.getRun).toHaveBeenCalledWith(RUN_UUID, { suppressToast: true })
  })

  it('200: selects the run and closes', async () => {
    svc.getRun.mockResolvedValue(run(9, { id: RUN_UUID }))
    const { onChange } = renderPicker()
    openPicker()
    paste(` ${RUN_UUID} `)
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(RUN_UUID))
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

// ── W11 GATE-02: a picker bound to one project (cases ADDED) ─────────────
describe('RunPicker — bound to a project (the policy simulator)', () => {
  it('a projectId reaches the request, whatever the top bar shows', async () => {
    serveRuns(2)
    useProjectStore.setState({ activeProjectId: ALL_PROJECTS_ID })
    renderPicker({ projectId: 'policy-project' })
    openPicker()
    await waitFor(() => expect(options()).toHaveLength(2))
    expect(searchCalls()).toEqual([{ projectId: 'policy-project', q: '', suite_name: null, page: 1, size: 20, days: 0 }])
  })

  it('the bound project is in the search key: another project asks again', async () => {
    serveRuns(2)
    const { rerender } = renderPicker({ projectId: 'project-a' })
    openPicker()
    await waitFor(() => expect(options()).toHaveLength(2))
    rerender(
      <RunPicker label="Left run (baseline)" value="" onChange={vi.fn()} testId="compare-left-picker" projectId="project-b" />,
    )
    await waitFor(() => expect(searchCalls().map((call) => call.projectId)).toEqual(['project-a', 'project-b']))
  })

  it('absent (or null), the picker searches the top bar scope, as before', async () => {
    serveRuns(1)
    renderPicker({ projectId: null })
    openPicker()
    await waitFor(() => expect(options()).toHaveLength(1))
    expect(searchCalls()).toEqual([{ projectId: 'p1', q: '', suite_name: null, page: 1, size: 20, days: 0 }])
  })
})
