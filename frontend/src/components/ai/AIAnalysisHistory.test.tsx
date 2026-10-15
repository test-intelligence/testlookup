/**
 * W03 AIH-04: the top of the Analysis tab. The picker writes the URL (`ai_version` + the cited `report_version`;
 * `ai_compare`), the diff drawer reads oldest -> newest whatever order the versions were picked in, "vs previous
 * run" is shown on the live view only, an old `?report_version=` link selects the version that published it, and a
 * failed history read is a note with Retry.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useEffect } from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AIAnalysisHistory from './AIAnalysisHistory'
import { diffSides } from './aiDigestDiff.model'
import { historyList, identicalDiff, versionsDiff, versionSummary } from './aiHistory.fixtures'

const state = vi.hoisted(() => ({
  list: undefined as unknown,
  listError: undefined as unknown,
  listMutate: vi.fn(),
  diffCalls: [] as unknown[][],
  diff: undefined as unknown,
}))

vi.mock('@/hooks/useAIHistory', () => ({
  useAIHistory: () => ({ data: state.list, error: state.listError, isLoading: !state.list && !state.listError, mutate: state.listMutate }),
  useAIVersionDiff: (runId: string, fromId: string | null, to: string | null) => {
    state.diffCalls.push([runId, fromId, to])
    return { data: fromId && to ? state.diff : undefined, error: undefined, isLoading: false, mutate: vi.fn() }
  },
}))
// The card has its own tests; here it only has to be there (or not).
vi.mock('./PreviousRunAICard', () => ({
  default: ({ baselineRunId }: { baselineRunId: string | null }) => (
    <section data-previous-run-card="" data-baseline={baselineRunId ?? ''} />
  ),
}))

const V1 = versionSummary(1)
const V2 = versionSummary(2)
const V3 = versionSummary(3, { decision_report_ref: null })

beforeEach(() => {
  state.list = historyList([V1, V2, V3])
  state.listError = undefined
  state.listMutate.mockReset()
  state.diffCalls = []
  state.diff = { run_id: 'run-1', from: { ref: V1.version_id }, to: { ref: V2.version_id }, diff: versionsDiff() }
})

let location = ''
function LocationProbe() {
  const { pathname, search } = useLocation()
  useEffect(() => {
    location = `${pathname}${search}`
  })
  return null
}

function renderAt(search: string) {
  return render(
    <MemoryRouter initialEntries={[`/runs/run-1${search}`]}>
      <Routes>
        <Route path="/runs/:runId" element={<><AIAnalysisHistory runId="run-1" /><LocationProbe /></>} />
      </Routes>
    </MemoryRouter>,
  )
}

const params = () => new URLSearchParams(location.split('?')[1] ?? '')
const versionSelect = () => screen.getByRole('combobox', { name: 'AI analysis version' })
const compareSelect = () => screen.getByRole('combobox', { name: 'Compare with' })

describe('AIAnalysisHistory — the picker drives the URL', () => {
  it('picking v1 sets ai_version and the report it cited, keeping tab and the Tests filters', async () => {
    renderAt('?tab=analysis&status=FAILED')
    fireEvent.change(versionSelect(), { target: { value: '1' } })
    await waitFor(() => expect(params().get('ai_version')).toBe('1'))
    expect(params().get('report_version')).toBe('1')
    expect(params().get('tab')).toBe('analysis')
    expect(params().get('status')).toBe('FAILED')
  })

  it('Current clears the version and its report', async () => {
    renderAt('?tab=analysis&ai_version=1&report_version=1')
    expect(versionSelect()).toHaveValue('1')
    fireEvent.change(versionSelect(), { target: { value: '' } })
    await waitFor(() => expect(params().get('ai_version')).toBeNull())
    expect(params().get('report_version')).toBeNull()
  })

  it('an old ?report_version= link selects the version that published that report', () => {
    renderAt('?tab=analysis&report_version=2')
    expect(versionSelect()).toHaveValue('2')
  })

  it('"vs previous run" sits on the live view, not on a stored snapshot', () => {
    const { container, unmount } = renderAt('?tab=analysis&ai_baseline=44444444-4444-4444-8444-100000000002')
    expect(container.querySelector('[data-previous-run-card]')).toHaveAttribute('data-baseline', '44444444-4444-4444-8444-100000000002')
    unmount()
    const snapshot = renderAt('?tab=analysis&ai_version=1')
    expect(snapshot.container.querySelector('[data-previous-run-card]')).toBeNull()
    // The latest version IS the live view.
    snapshot.unmount()
    const latest = renderAt('?tab=analysis&ai_version=3')
    expect(latest.container.querySelector('[data-previous-run-card]')).not.toBeNull()
  })
})

describe('AIAnalysisHistory — the diff drawer', () => {
  it('"Compare with v2" while viewing v1 diffs v1 -> v2 (oldest first) and shows the verdict change', async () => {
    const { container } = renderAt('?tab=analysis&ai_version=1')
    fireEvent.change(compareSelect(), { target: { value: '2' } })
    await waitFor(() => expect(params().get('ai_compare')).toBe('2'))
    expect(state.diffCalls).toContainEqual(['run-1', V1.version_id, V2.version_id])
    const drawer = container.querySelector('[data-ai-diff-drawer]') as HTMLElement
    expect(drawer.querySelector('[data-ai-diff]')).toHaveAttribute('data-ai-diff-mode', 'versions')
    expect(drawer).toHaveTextContent('Verdict CONDITIONAL_GO -> NO_GO')
  })

  it('viewing Current, a version is diffed against the current data (`to=live`)', () => {
    renderAt('?tab=analysis&ai_compare=1')
    expect(state.diffCalls).toContainEqual(['run-1', V1.version_id, 'live'])
  })

  it('closing the drawer clears ai_compare only', async () => {
    const { container } = renderAt('?tab=analysis&ai_version=1&ai_compare=2')
    fireEvent.click(screen.getByRole('button', { name: 'Close the comparison' }))
    await waitFor(() => expect(params().get('ai_compare')).toBeNull())
    expect(params().get('ai_version')).toBe('1')
    expect(container.querySelector('[data-ai-diff-drawer]')).toBeNull()
  })

  it('identical versions: "No differences"', () => {
    state.diff = { run_id: 'run-1', from: {}, to: {}, diff: identicalDiff() }
    const { container } = renderAt('?tab=analysis&ai_version=2&ai_compare=3')
    expect(container.querySelector('[data-ai-diff-empty]')).toHaveTextContent(/^No differences/)
  })

  it('a version that is gone says so and asks for nothing', () => {
    renderAt('?tab=analysis&ai_version=1&ai_compare=9')
    expect(screen.getByText(/That version no longer exists/)).toBeInTheDocument()
    expect(state.diffCalls.every(([, from]) => from === null)).toBe(true)
  })
})

describe('AIAnalysisHistory — no history', () => {
  it('a failed list read is a note with Retry', () => {
    state.list = undefined
    state.listError = new Error('503')
    const { container } = renderAt('?tab=analysis')
    expect(container.querySelector('[data-ai-history-unavailable]')).toHaveTextContent('The AI history of this run is unavailable right now.')
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }))
    expect(state.listMutate).toHaveBeenCalled()
  })

  it('an empty list is the first analysis of the run', () => {
    state.list = historyList([])
    const { container } = renderAt('?tab=analysis')
    expect(container.querySelector('[data-ai-version-empty]')).toHaveTextContent(/^First analysis of this run/)
  })
})

describe('diffSides', () => {
  const versions = [V3, V2, V1]
  it('orders two stored versions oldest -> newest', () => {
    expect(diffSides(versions, 3, 1)).toMatchObject({ fromId: V1.version_id, to: V3.version_id, problem: null })
    expect(diffSides(versions, 1, 3)).toMatchObject({ fromId: V1.version_id, to: V3.version_id, problem: null })
  })
  it('Current / live is always the newer side', () => {
    expect(diffSides(versions, null, 2)).toMatchObject({ fromId: V2.version_id, to: 'live', toLabel: 'Current data' })
    expect(diffSides(versions, 1, 'live')).toMatchObject({ fromId: V1.version_id, to: 'live' })
  })
  it('nothing to compare: the same version, Current with itself, an unknown version', () => {
    expect(diffSides(versions, 2, 2).problem).toBe('nothing')
    expect(diffSides(versions, null, 'live').problem).toBe('nothing')
    expect(diffSides(versions, 1, 8).problem).toBe('missing')
    expect(diffSides(versions, 8, 1).problem).toBe('missing')
  })
})
