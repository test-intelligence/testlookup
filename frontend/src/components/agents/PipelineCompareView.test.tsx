/**
 * W14 AGT-05: the Compare tab (contract §2.1 test 6) — only the run's other
 * pipelines, the default partner rule (§0.4), changed cells marked, the three
 * output lines, `—` for a missing release verdict.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PipelineCompareView from './PipelineCompareView'
import { compareCandidates, defaultCandidate, outputLine, type ComparePipelineLike } from './pipelineViewsModel'
import { usePipelineCompare } from '@/hooks/useAgentOps'
import { COMPARE_FIXTURE, idleSwr } from '@/test/agentOpsMocks'

vi.mock('@/hooks/useAgentOps', async () => (await import('@/test/agentOpsMocks')).agentOpsHookMocks())

const compareHook = vi.mocked(usePipelineCompare)

function pipe(id: string, over: Partial<ComparePipelineLike> = {}): ComparePipelineLike {
  return { id, test_run_id: 'run-1', workflow_type: 'offline', status: 'completed', created_at: '2026-10-20T09:00:00Z', ...over }
}

const SELECTED = pipe('p-sel', { created_at: '2026-10-20T12:00:00Z', attempt: 2 })
const PIPELINES = [
  SELECTED,
  pipe('p-new-deep', { workflow_type: 'deep', created_at: '2026-10-20T13:00:00Z' }),
  pipe('p-old-offline', { created_at: '2026-10-20T10:00:00Z' }),
  pipe('p-older-offline', { created_at: '2026-10-20T08:00:00Z' }),
  pipe('p-other-run', { test_run_id: 'run-2', created_at: '2026-10-20T11:00:00Z' }),
]

function renderView(selected = SELECTED, pipelines = PIPELINES) {
  const candidates = compareCandidates(pipelines, selected)
  return render(<MemoryRouter><PipelineCompareView selected={selected} candidates={candidates} /></MemoryRouter>)
}

beforeEach(() => {
  compareHook.mockReset()
  compareHook.mockReturnValue(idleSwr() as never)
})

describe('PipelineCompareView', () => {
  it('offers the same run\'s OTHER pipelines only, newest first', () => {
    renderView()
    const options = within(screen.getByLabelText('Compare with')).getAllByRole('option')
    expect(options.map(o => o.getAttribute('value'))).toEqual(['p-new-deep', 'p-old-offline', 'p-older-offline'])
    expect(options[0].textContent).toMatch(/^deep · completed · /)
  })

  it('defaults to rerun_of, else the newest OLDER pipeline of the same type, else the newest other one', () => {
    const candidates = compareCandidates(PIPELINES, SELECTED)
    expect(defaultCandidate(candidates, SELECTED)?.id).toBe('p-old-offline')
    expect(defaultCandidate(candidates, { ...SELECTED, rerun_of: 'p-older-offline' })?.id).toBe('p-older-offline')
    // A rerun_of outside the listed candidates falls through to the type rule.
    expect(defaultCandidate(candidates, { ...SELECTED, rerun_of: 'p-gone' })?.id).toBe('p-old-offline')
    const firstDeep = pipe('p-first-deep', { workflow_type: 'deep', created_at: '2026-10-20T07:00:00Z' })
    expect(defaultCandidate(compareCandidates(PIPELINES, firstDeep), firstDeep)?.id).toBe('p-new-deep')
    expect(defaultCandidate([], SELECTED)).toBeNull()
  })

  it('asks for the default pair, and the picker changes the partner', () => {
    renderView()
    expect(compareHook).toHaveBeenLastCalledWith('p-sel', 'p-old-offline')
    fireEvent.change(screen.getByLabelText('Compare with'), { target: { value: 'p-new-deep' } })
    expect(compareHook).toHaveBeenLastCalledWith('p-sel', 'p-new-deep')
  })

  it('marks the changed cells, says the output line, and shows — for a missing release verdict', () => {
    compareHook.mockReturnValue(idleSwr({ data: COMPARE_FIXTURE }) as never)
    const { container } = renderView()
    expect(container.querySelector('[data-compare-output]')).toHaveTextContent('Output changed in 1 stage')
    const rca = container.querySelector('[data-compare-row="root_cause_analysis"]') as HTMLElement
    const changed = rca.querySelectorAll('[data-changed]')
    // Duration, tokens, cost and the output column.
    expect(changed).toHaveLength(4)
    expect(rca.textContent).toContain('1m 0s')
    expect(rca.textContent).toContain('→ 45s')
    expect(rca.textContent).toContain('changed')
    const ingestion = container.querySelector('[data-compare-row="ingestion"]') as HTMLElement
    expect(ingestion.querySelectorAll('[data-changed]')).toHaveLength(0)
    expect(ingestion.textContent).toContain('same')
    for (const verdict of container.querySelectorAll('[data-release-verdict]')) expect(verdict).toHaveTextContent('—')
  })

  it('says a stage is missing on one side', () => {
    compareHook.mockReturnValue(idleSwr({
      data: { ...COMPARE_FIXTURE, stages: [{ ...COMPARE_FIXTURE.stages[0], a: null, changed: ['presence'], output_checksum_equal: null }] },
    }) as never)
    const { container } = renderView()
    const row = container.querySelector('[data-compare-row="ingestion"]') as HTMLElement
    expect(row.textContent).toContain('not in this pipeline')
    expect(row.textContent).toContain('unknown')
  })

  it('the three output lines', () => {
    const stages = COMPARE_FIXTURE.stages
    expect(outputLine({ output: 'identical', changed_stages: 0, stages })).toBe('Output identical')
    expect(outputLine({ output: 'changed', changed_stages: 1, stages })).toBe('Output changed in 1 stage')
    expect(outputLine({ output: 'unknown', changed_stages: 0, stages })).toBe('Output unknown: no stage recorded a checksum')
  })

  it('a failed read says so and offers Retry', () => {
    const mutate = vi.fn()
    compareHook.mockReturnValue(idleSwr({ error: new Error('403'), mutate }) as never)
    renderView()
    expect(screen.getByText('Could not load the comparison.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(mutate).toHaveBeenCalledTimes(1)
  })
})
