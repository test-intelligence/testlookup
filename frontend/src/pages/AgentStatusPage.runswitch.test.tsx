/**
 * Choosing a run in the dropdown must show THAT run's pipeline.
 *
 * Two user reports, one control, and the first fix caused the second report.
 *
 * TL-2026-09-18-01-009 (2026-09-18): the dropdown navigates to
 * `/agents/run/:runId`, so `usePipelines(runId)` refetched correctly, but
 * `selectedPipeline` was component state cleared only on a project change or a
 * card click. Every detail panel is keyed on it — `usePipelineStages`,
 * `usePipelineTimeline`, the compute graph, the AI report — so the header moved
 * to the new run while the panels kept rendering the previous one.
 *
 * BUG-011 (2026-09-19): that fix cleared the id and stopped. The panels then
 * showed "Select a pipeline run to see agent stages" for EVERY run picked —
 * measured against the homelab as four runs, four different left-hand lists,
 * one identical empty panel. Wrong content became no content, which reads the
 * same way from the user's chair ("it is static values") and added a mandatory
 * second click. A run on the route now also opens its newest pipeline.
 *
 * The assertions are on the id the detail hooks are CALLED with, not on
 * rendered text: the panels are what went stale, and that argument is what
 * drives them. Asserting on text would pass as soon as any panel rendered
 * anything, including the stale one.
 *
 * `usePipelines` is mocked PER RUN. A mock that ignores its argument cannot
 * tell "kept the old pipeline" apart from "opened the new one" — the two
 * differ only in which run the id belongs to, so a run-blind fixture would
 * pass either way.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AgentStatusPage from './AgentStatusPage'

const { mockProjectState } = vi.hoisted(() => ({
  mockProjectState: {
    activeProjectId: 'proj-1',
    activeProject: { id: 'proj-1', name: 'Project One' },
  },
}))

vi.mock('@/hooks/useAgentRuns', () => ({
  usePipelines: vi.fn(),
  usePipelineStages: vi.fn(),
  usePipelineTimeline: vi.fn(),
  useRunSummary: vi.fn(),
  useActiveLiveRuns: vi.fn(),
}))

vi.mock('@/hooks/useAIConfig', () => ({
  useAIConfig: vi.fn(() => ({ data: { analysis_mode: 'auto', deep_investigation_enabled: true } })),
}))

vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(() => ({
    data: {
      items: [
        { id: 'run-1', build_number: '41', primary_suite_name: 'SuiteA' },
        { id: 'run-2', build_number: '42', primary_suite_name: 'SuiteB' },
      ],
    },
    isLoading: false,
  })),
}))

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: vi.fn(() => ({ isQaEngineer: false })),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: typeof mockProjectState) => unknown) =>
    selector(mockProjectState)),
}))

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}))

function pipeline(id: string, runId: string, createdAt: string) {
  return {
    id,
    test_run_id: runId,
    workflow_type: 'offline',
    status: 'completed',
    started_at: '2026-03-31T10:00:00Z',
    completed_at: '2026-03-31T10:01:00Z',
    error: null,
    created_at: createdAt,
  }
}

const pipelineForRun1 = pipeline('pipe-1', 'run-1', '2026-03-31T10:00:00Z')
// run-2 carries two, deliberately out of order: homelab runs hold 0, 2 and 13,
// so "the run's pipeline" is not a thing — the NEWEST one is what opens.
const pipeRun2Older = pipeline('pipe-2-old', 'run-2', '2026-03-31T11:00:00Z')
const pipeRun2Newer = pipeline('pipe-2-new', 'run-2', '2026-03-31T12:00:00Z')

const PIPELINES_BY_RUN: Record<string, ReturnType<typeof pipeline>[]> = {
  'run-1': [pipelineForRun1],
  'run-2': [pipeRun2Older, pipeRun2Newer],
  // A run whose pipelines never fired. The placeholder is correct here.
  'run-3': [],
}

/** Mock `usePipelines` so it answers for the run it is actually asked about. */
function mockPipelinesPerRun(usePipelines: ReturnType<typeof vi.fn>) {
  usePipelines.mockImplementation((runId?: string) => ({
    data: runId ? PIPELINES_BY_RUN[runId] ?? [] : [pipelineForRun1, pipeRun2Newer],
    isLoading: false,
  }))
}

const lastArgOf = (fn: ReturnType<typeof vi.fn>): unknown => {
  // `.at(-1)` is not in this tsconfig's lib target, and `npm run type-check`
  // is a CI gate.
  const { calls } = fn.mock
  return calls.length ? calls[calls.length - 1][0] : undefined
}

describe('AgentStatusPage — switching the selected run', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockProjectState.activeProjectId = 'proj-1'
    mockProjectState.activeProject = { id: 'proj-1', name: 'Project One' }
  })

  async function setup(entry: string) {
    const { useActiveLiveRuns, usePipelineStages, usePipelineTimeline, usePipelines, useRunSummary } =
      await import('@/hooks/useAgentRuns')

    ;(useActiveLiveRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: [] })
    mockPipelinesPerRun(usePipelines as ReturnType<typeof vi.fn>)
    ;(usePipelineStages as ReturnType<typeof vi.fn>).mockReturnValue({ data: [], isLoading: false })
    ;(usePipelineTimeline as ReturnType<typeof vi.fn>).mockReturnValue({ data: undefined })
    ;(useRunSummary as ReturnType<typeof vi.fn>).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: undefined,
    })

    render(
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/agents" element={<AgentStatusPage />} />
          <Route path="/agents/run/:runId" element={<AgentStatusPage />} />
        </Routes>
      </MemoryRouter>,
    )

    return { usePipelineStages, usePipelineTimeline, useRunSummary }
  }

  /** The dropdown is labelled 'Test Suite & Build:'; the user calls it the
   *  Pipeline Runs dropdown, after the section heading above it. */
  const switchRunTo = (runId: string) =>
    fireEvent.change(screen.getByLabelText(/test suite/i), { target: { value: runId } })

  it('switching runs drives the panels with the NEW run pipeline, never the old one', async () => {
    const { usePipelineStages, usePipelineTimeline } = await setup('/agents/run/run-1')

    await waitFor(() => {
      expect(lastArgOf(usePipelineStages as ReturnType<typeof vi.fn>)).toBe('pipe-1')
    })

    switchRunTo('run-2')

    // TL-009: 'pipe-1' here means the panels still describe the run the user
    // navigated away from. BUG-011: null here means they describe nothing.
    await waitFor(() => {
      expect(
        lastArgOf(usePipelineStages as ReturnType<typeof vi.fn>),
        'the stage panel is not showing the newly selected run pipeline',
      ).toBe('pipe-2-new')
    })

    expect(
      lastArgOf(usePipelineTimeline as ReturnType<typeof vi.fn>),
      'the timeline is not showing the newly selected run pipeline',
    ).toBe('pipe-2-new')
  })

  it('opens the NEWEST pipeline when the run has several', async () => {
    // Guards the sort, not just "something was selected": 'pipe-2-old' is
    // first in the fixture array, so taking [0] unsorted picks the wrong one.
    const { usePipelineStages } = await setup('/agents/run/run-2')

    await waitFor(() => {
      expect(lastArgOf(usePipelineStages as ReturnType<typeof vi.fn>)).toBe('pipe-2-new')
    })
  })

  it('opens the run pipeline on a direct load, with no click', async () => {
    // The reported symptom in its simplest form: landing on a run must not
    // require hunting for a card before anything appears.
    const { usePipelineStages, useRunSummary } = await setup('/agents/run/run-1')

    await waitFor(() => {
      expect(lastArgOf(usePipelineStages as ReturnType<typeof vi.fn>)).toBe('pipe-1')
    })
    // The AI report is the headline panel and is keyed on the run, not the
    // pipeline — it has to follow the same selection or the page is half-filled.
    expect(lastArgOf(useRunSummary as ReturnType<typeof vi.fn>)).toBe('run-1')
  })

  it('leaves the panels empty for a run that has no pipelines', async () => {
    // Auto-selecting must not invent a pipeline. The left column explains the
    // absence, so the placeholder is honest here rather than a dead end.
    const { usePipelineStages } = await setup('/agents/run/run-3')

    await waitFor(() => {
      expect(lastArgOf(usePipelineStages as ReturnType<typeof vi.fn>)).toBeNull()
    })
  })

  it('shows no pipeline on /agents, where the list spans many runs', async () => {
    // Without a run there is no "this run's pipeline" to open, and picking one
    // arbitrarily would claim a run the user never chose.
    const { usePipelineStages } = await setup('/agents')

    await waitFor(() => {
      expect(lastArgOf(usePipelineStages as ReturnType<typeof vi.fn>)).toBeNull()
    })
  })

  it('keeps an explicitly clicked pipeline selected over the auto-selected one', async () => {
    // Auto-selection must yield to the user. run-2 opens 'pipe-2-new'; clicking
    // the older card has to stick rather than being pulled back on re-render.
    const { usePipelineStages } = await setup('/agents/run/run-2')

    await waitFor(() => {
      expect(lastArgOf(usePipelineStages as ReturnType<typeof vi.fn>)).toBe('pipe-2-new')
    })

    const cards = screen.getAllByRole('button', { name: /offline pipeline/i })
    expect(cards.length, 'both of run-2 pipelines should be listed').toBe(2)
    // Cards render newest-first, so the second card is the older pipeline.
    fireEvent.click(cards[1])

    await waitFor(() => {
      expect(
        lastArgOf(usePipelineStages as ReturnType<typeof vi.fn>),
        'the click was overridden by the auto-selection',
      ).toBe('pipe-2-old')
    })
  })
})
