/**
 * The Tiers tab (W16 AGT-13): the run form's POST body (`pairs: []`, live labels and Record the result ON by
 * default), the result card's floor / smoke lines and its Wilson-bounded metrics, the recorded history, and the
 * All Projects prompt with no read at all.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useShadowPairs, useTierComparisonRuns } from '@/hooks/useAiEval'
import { runTierComparison } from '@/services/aiEvalService'
import { PROJECT_FIXTURE_ID, SHADOW_PAIRS_FIXTURE, TIER_RESULT_FIXTURE } from '@/test/aiEvalMocks'
import type { GateRun } from '@/types/aiEval'
import TierComparisonTab from './TierComparisonTab'

vi.mock('@/hooks/useAiEval', async () => (await import('@/test/aiEvalMocks')).aiEvalHookMocks())
vi.mock('@/services/aiEvalService', () => ({ runTierComparison: vi.fn() }))

const runG2 = vi.mocked(runTierComparison)
const historyHook = vi.mocked(useTierComparisonRuns)

const G2_ROW: GateRun = {
  id: 'grC3d4E5f6',
  change_id: `tier:${PROJECT_FIXTURE_ID}:agent.root_cause_analysis.v1:slm`,
  status: 'insufficient_samples',
  manifest_checksum_sha256: 'beef'.repeat(16),
  manifest: {},
  gate_results: [],
  blocking_gates: [{ gate: 'G2' }],
  version_changes: [],
  gate_type: 'tier_comparison',
  project_id: PROJECT_FIXTURE_ID,
  agent_id: 'agent.root_cause_analysis.v1',
  baseline_tier: 'llm',
  candidate_tier: 'slm',
  sample_count: 3,
  evaluated_by: 'usA1b2C3d4',
  evaluated_at: '2026-10-25T09:00:00+00:00',
}

function history(data: unknown, extra: { error?: unknown; mutate?: () => void } = {}) {
  const mutate = extra.mutate ?? vi.fn()
  historyHook.mockReturnValue({ data, error: extra.error, isLoading: false, mutate } as unknown as ReturnType<typeof useTierComparisonRuns>)
  return mutate
}

function renderTab(projectId: string | null = PROJECT_FIXTURE_ID) {
  return render(
    <MemoryRouter>
      <TierComparisonTab projectId={projectId} />
    </MemoryRouter>,
  )
}

const runButton = () => screen.getByRole('button', { name: 'Run comparison' })

beforeEach(() => {
  vi.clearAllMocks()
  history({ items: [G2_ROW], total: 1 })
  vi.mocked(useShadowPairs).mockReturnValue({
    data: SHADOW_PAIRS_FIXTURE, error: undefined, isLoading: false, mutate: vi.fn(),
  } as unknown as ReturnType<typeof useShadowPairs>)
  runG2.mockResolvedValue(TIER_RESULT_FIXTURE)
})

describe('All Projects', () => {
  it('asks for one project and reads nothing', () => {
    renderTab(null)
    expect(screen.getByText('Select a project')).toBeTruthy()
    expect(historyHook).not.toHaveBeenCalled()
    expect(useShadowPairs).not.toHaveBeenCalled()
  })
})

describe('run tier comparison (G2)', () => {
  it('posts pairs: [] with live labels and Record the result ON by default', async () => {
    const mutate = history({ items: [G2_ROW], total: 1 })
    renderTab()
    expect((screen.getByRole('checkbox', { name: 'Include live labels' }) as HTMLInputElement).checked).toBe(true)
    expect((screen.getByRole('checkbox', { name: 'Record the result' }) as HTMLInputElement).checked).toBe(true)
    fireEvent.click(runButton())
    await waitFor(() => expect(runG2).toHaveBeenCalledWith({
      project_id: PROJECT_FIXTURE_ID,
      agent_id: 'agent.root_cause_analysis.v1',
      capability: 'root_cause_analysis',
      incumbent_tier: 'llm',
      candidate_tier: 'slm',
      delta: 0.05,
      pairs: [],
      include_live_labels: true,
      persist: true,
    }))
    await waitFor(() => expect(mutate).toHaveBeenCalled())
  })

  it('sends the chosen agent, tiers and delta, and records nothing when unticked', async () => {
    const mutate = history({ items: [], total: 0 })
    renderTab()
    fireEvent.change(screen.getByRole('combobox', { name: 'Agent' }), { target: { value: 'agent.summary.v1' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Candidate tier' }), { target: { value: 'deterministic' } })
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Delta' }), { target: { value: '0.1' } })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Include live labels' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Record the result' }))
    fireEvent.click(runButton())
    await waitFor(() => expect(runG2).toHaveBeenCalledWith(expect.objectContaining({
      agent_id: 'agent.summary.v1', capability: 'summary', candidate_tier: 'deterministic', delta: 0.1,
      include_live_labels: false, persist: false, pairs: [],
    })))
    await screen.findByText('Not enough evidence')
    expect(mutate).not.toHaveBeenCalled()
  })

  it('refuses a delta outside 0..1 before any request', () => {
    renderTab()
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Delta' }), { target: { value: '2' } })
    expect((runButton() as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText('Delta must be a number from 0 to 1.')).toBeTruthy()
  })
})

describe('the result card', () => {
  it('root cause below its floor: the verdict, the reason, the counts, the floor line and no smoke line', async () => {
    renderTab()
    fireEvent.click(runButton())
    const card = await waitFor(() => {
      const found = document.querySelector('[data-tier-result]')
      expect(found).not.toBeNull()
      return found as HTMLElement
    })
    const result = within(card)
    expect(result.getByText('Not enough evidence')).toBeTruthy()
    expect(result.getByText('need at least 100 paired samples; got 3')).toBeTruthy()
    expect(result.getByText('3 paired samples · 3 live labels')).toBeTruthy()
    expect(result.getByText('Not enough evidence yet: G2 needs 100 paired samples for this agent.')).toBeTruthy()
    expect(result.queryByText('Smoke check: both tiers must be valid and their 95 % intervals must overlap.')).toBeNull()

    const row = card.querySelector('[data-tier-metric="incumbent_accuracy"]') as HTMLElement
    expect(Array.from(row.querySelectorAll('td')).map((td) => td.textContent)).toEqual([
      'Incumbent accuracy', '66.7%', '21.0%', '94.0%', '3',
    ])
    const delta = card.querySelector('[data-tier-metric="paired_accuracy_delta"]') as HTMLElement
    expect(delta.querySelectorAll('td')[1].textContent).toBe('−33.3%')
  })

  it('a summary run under 100 samples explains the smoke check too', async () => {
    runG2.mockResolvedValue({ ...TIER_RESULT_FIXTURE, reason: 'need at least 20 paired samples; got 3' })
    renderTab()
    fireEvent.change(screen.getByRole('combobox', { name: 'Agent' }), { target: { value: 'agent.summary.v1' } })
    fireEvent.click(runButton())
    expect(await screen.findByText('Not enough evidence yet: G2 needs 20 paired samples for this agent.')).toBeTruthy()
    expect(screen.getByText('Smoke check: both tiers must be valid and their 95 % intervals must overlap.')).toBeTruthy()
  })

  it("an answer without the live count reads 0 live labels; a refusal shows the server's sentence", async () => {
    const { live_labelled_pairs: _omitted, ...withoutLive } = TIER_RESULT_FIXTURE
    runG2.mockResolvedValueOnce({ ...withoutLive, sample_count: 18 })
    renderTab()
    fireEvent.click(runButton())
    expect(await screen.findByText('18 paired samples · 0 live labels')).toBeTruthy()

    runG2.mockRejectedValueOnce(Object.assign(new Error('422'), {
      response: { status: 422, data: { detail: 'agent_id does not match the capability registry entry' } },
    }))
    fireEvent.click(runButton())
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'agent_id does not match the capability registry entry')
    expect(document.querySelector('[data-tier-result]')).toBeNull()
  })

  it('renders a markup reason as text', async () => {
    runG2.mockResolvedValue({ ...TIER_RESULT_FIXTURE, reason: '<img src=x onerror=alert(1)>' })
    const { container } = renderTab()
    fireEvent.click(runButton())
    expect(await screen.findByText('<img src=x onerror=alert(1)>')).toBeTruthy()
    expect(container.querySelector('img')).toBeNull()
  })
})

describe('recorded comparisons and the queue', () => {
  it("lists the project's G2 history and mounts the labelling queue", () => {
    renderTab()
    expect(historyHook).toHaveBeenCalledWith(PROJECT_FIXTURE_ID)
    const row = document.querySelector('[data-tier-run="grC3d4E5f6"]') as HTMLElement
    const cells = Array.from(row.querySelectorAll('td')).map((td) => td.textContent)
    expect(cells.slice(1)).toEqual(['Root-cause analysis', 'llm → slm', 'Not enough evidence', '3'])
    expect(screen.getByRole('heading', { name: 'Label live pairs (2)' })).toBeTruthy()
  })

  it('a failed history read says so and retries; an empty one says none yet', () => {
    const mutate = history(undefined, { error: new Error('boom') })
    const first = renderTab()
    expect(screen.getByText('Could not load the tier comparisons.')).toBeTruthy()
    fireEvent.click(within(screen.getByText('Could not load the tier comparisons.')).getByRole('button', { name: 'Retry' }))
    expect(mutate).toHaveBeenCalled()
    first.unmount()

    history({ total: 0 })
    renderTab()
    expect(screen.getByText('No recorded comparisons for this project yet.')).toBeTruthy()
  })
})
