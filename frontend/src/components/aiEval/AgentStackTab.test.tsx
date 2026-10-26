/**
 * The Agent stack tab: the shipped release gate and its history had no page. An admin runs it; anyone on the page
 * reads the history and opens a row's manifest inline.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useAgentStackGateRuns, useEvalManifest } from '@/hooks/useAiEval'
import { runAgentStackGate } from '@/services/aiEvalService'
import { GATE_RUNS_FIXTURE, idleAiEvalSwr } from '@/test/aiEvalMocks'
import type { AgentStackGateResult } from '@/types/aiEval'
import AgentStackTab from './AgentStackTab'
import { defaultChangeId } from './aiEvalModel'

vi.mock('@/hooks/useAiEval', async () => (await import('@/test/aiEvalMocks')).aiEvalHookMocks())
vi.mock('@/services/aiEvalService', () => ({ runAgentStackGate: vi.fn() }))

const CHECKSUM = 'cafe'.repeat(16)

const RESULT: AgentStackGateResult = {
  status: 'fail',
  manifest: { change_id: 'manual-2026-10-25', manifest_checksum_sha256: CHECKSUM, required_gates: [] },
  gate_results: [
    { status: 'pass', task_type: 'classification', agent_name: 'AnalysisAgent', current_metrics: { accuracy: 0.9, f1_score: 0.88, total: 12 } },
    { status: 'insufficient_samples', task_type: 'root_cause', agent_name: 'AnalysisAgent', current_metrics: null },
    { status: 'fail', task_type: 'duplicate_detection', agent_name: 'DefectPromotionAgent', current_metrics: { accuracy: 0.5, f1_score: 0.4, total: 8 } },
  ],
  blocking_gates: [
    { task_type: 'root_cause', agent_name: 'AnalysisAgent', status: 'insufficient_samples' },
    { task_type: 'duplicate_detection', agent_name: 'DefectPromotionAgent', status: 'fail' },
  ],
  version_changes: [],
  evaluated_at: '2026-10-25T09:00:00+00:00',
  gate_run_id: 'grA1b2C3d4',
}

const historyMutate = vi.fn()

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(useAgentStackGateRuns).mockReturnValue(
    idleAiEvalSwr({ data: GATE_RUNS_FIXTURE, mutate: historyMutate }) as unknown as ReturnType<typeof useAgentStackGateRuns>,
  )
})

describe('an admin runs the agent-stack gate', () => {
  it('posts the change id and shows the verdict, the checksum, the blocking gates and one row per gate', async () => {
    vi.mocked(runAgentStackGate).mockResolvedValue(RESULT)
    render(<AgentStackTab isAdmin />)
    const input = screen.getByLabelText('Change id') as HTMLInputElement
    expect(input.value).toBe(defaultChangeId(new Date()))
    fireEvent.change(input, { target: { value: 'prompt-bump-7' } })
    fireEvent.click(screen.getByRole('button', { name: 'Run gate' }))
    await waitFor(() => expect(runAgentStackGate).toHaveBeenCalledWith({ change_id: 'prompt-bump-7' }))

    const result = await screen.findByTestId('agent-stack-result')
    expect(within(result).getByTitle(CHECKSUM)).toHaveTextContent(CHECKSUM.slice(0, 12))
    expect(result).toHaveTextContent('Blocking: Root Cause, Duplicate Detection')
    const rows = result.querySelectorAll('[data-gate-row]')
    expect(rows).toHaveLength(3)
    expect(within(rows[0] as HTMLElement).getByText('PASS')).toBeInTheDocument()
    expect(within(rows[0] as HTMLElement).getByText('90.0%')).toBeInTheDocument()
    expect(within(rows[1] as HTMLElement).getByText('Not enough evidence')).toBeInTheDocument()
    expect(within(rows[2] as HTMLElement).getByText('DefectPromotionAgent')).toBeInTheDocument()
    expect(historyMutate).toHaveBeenCalled()
  })

  it("shows a refused run's sentence in place", async () => {
    vi.mocked(runAgentStackGate).mockRejectedValue({ response: { data: { detail: 'Not enough permissions' } } })
    render(<AgentStackTab isAdmin />)
    fireEvent.click(screen.getByRole('button', { name: 'Run gate' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Not enough permissions')
  })
})

describe('the history', () => {
  it('lists the recorded runs and opens a manifest inline', () => {
    vi.mocked(useEvalManifest).mockReturnValue(idleAiEvalSwr({
      data: {
        eval_manifest_checksum: CHECKSUM,
        source: 'database_gate',
        status: 'fail',
        manifest: { prompt_versions: { AnalysisAgent: 'react_triage@v3' }, model_versions: {}, routing_versions: {}, required_gates: [{ task_type: 'duplicate_detection', agent_name: 'DefectPromotionAgent' }] },
      },
    }) as unknown as ReturnType<typeof useEvalManifest>)
    render(<AgentStackTab isAdmin={false} />)
    const row = document.querySelector('[data-history-row="grA1b2C3d4"]') as HTMLElement
    expect(row).toHaveTextContent('manual-2026-10-25')
    expect(within(row).getByText('FAIL')).toBeInTheDocument()
    expect(screen.queryByText('database_gate')).toBeNull()

    fireEvent.click(within(row).getByRole('button', { name: CHECKSUM.slice(0, 12) }))
    expect(vi.mocked(useEvalManifest)).toHaveBeenCalledWith(CHECKSUM)
    const manifest = document.querySelector(`[data-manifest="${CHECKSUM}"]`) as HTMLElement
    expect(manifest).toHaveTextContent('database_gate')
    expect(manifest).toHaveTextContent('AnalysisAgent: react_triage@v3')
    expect(manifest).toHaveTextContent('Duplicate Detection · DefectPromotionAgent')
  })

  it('a QA lead reads it, with Run disabled and the reason', () => {
    render(<AgentStackTab isAdmin={false} />)
    const run = screen.getByRole('button', { name: 'Run gate' })
    expect(run).toBeDisabled()
    expect(run).toHaveAttribute('title', 'Needs an administrator.')
    expect(document.querySelector('[data-history-row]')).not.toBeNull()
  })

  it('says it could not load, with Retry', () => {
    vi.mocked(useAgentStackGateRuns).mockReturnValue(
      idleAiEvalSwr({ error: new Error('boom'), mutate: historyMutate }) as unknown as ReturnType<typeof useAgentStackGateRuns>,
    )
    render(<AgentStackTab isAdmin />)
    expect(screen.getByText(/Could not load the agent-stack history\./)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(historyMutate).toHaveBeenCalled()
  })

  it('never crashes on an answer without items (an old server answered a bare list)', () => {
    vi.mocked(useAgentStackGateRuns).mockReturnValue(
      idleAiEvalSwr({ data: [] }) as unknown as ReturnType<typeof useAgentStackGateRuns>,
    )
    render(<AgentStackTab isAdmin />)
    expect(screen.getByText('No agent-stack gate has been run yet.')).toBeInTheDocument()
  })
})
