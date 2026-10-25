/**
 * W14 AGT-06: the generic canvas (contract §2.2 test 3) — the investigation's
 * seven nodes, the truncated line, the rail's tokens / cost, text only.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import GenericComputeCanvas from './GenericComputeCanvas'
import { truncatedLine, type RuntimeInput, type RuntimeTaskInput } from './dagLayout'

const PIPE = 'pipe-inv'
const ROOT = `pipeline:${PIPE}`
const HYPOTHESES = ['hypothesis_infra', 'hypothesis_commit', 'hypothesis_environment', 'hypothesis_known_flaky', 'hypothesis_regression']

function task(stage: string, over: Partial<RuntimeTaskInput> = {}): RuntimeTaskInput {
  return {
    task_id: `${ROOT}:stage:${stage}:attempt:1`,
    parent_task_id: ROOT,
    source_pipeline_run_id: PIPE,
    stage_name: stage,
    status: 'completed',
    selected: true,
    dependencies: [],
    attempt: 1,
    usage: { total_tokens: 0, cost_usd: 0, duration_ms: 0 },
    ...over,
  }
}

function investigation(over: Partial<RuntimeInput> = {}): RuntimeInput {
  return {
    root_task_id: ROOT,
    source_pipeline_run_id: PIPE,
    tasks: [
      { task_id: ROOT, parent_task_id: null, source_pipeline_run_id: PIPE, stage_name: 'pipeline', status: 'completed' },
      task('investigator_plan', { started_at: '2026-10-20T10:00:00Z' }),
      ...HYPOTHESES.map((h, i) => task(h, {
        parent_task_id: `${ROOT}:stage:investigator_plan:attempt:1`,
        dependencies: ['investigator_plan'],
        started_at: `2026-10-20T10:00:0${i + 1}Z`,
        status: i === 0 ? 'running' : 'completed',
        usage: i === 1 ? { total_tokens: 1530, cost_usd: 0.0123, duration_ms: 4000 } : { total_tokens: 0, cost_usd: 0, duration_ms: 0 },
        error: i === 4 ? '<img src=x onerror=alert(1)>' : null,
      })),
      task('investigator_synthesis', { dependencies: HYPOTHESES, started_at: '2026-10-20T10:01:00Z', status: 'pending' }),
    ],
    observed_task_rows: 7,
    ...over,
  }
}

describe('GenericComputeCanvas', () => {
  it('draws one card per stage: seven for the investigation, coloured by status', () => {
    const { container } = render(<GenericComputeCanvas run={investigation()} />)
    const nodes = container.querySelectorAll('[data-stage-id]')
    expect(nodes).toHaveLength(7)
    expect(container.querySelector('[data-stage-id="investigator_plan"]')).toHaveAttribute('data-stage-status', 'done')
    expect(container.querySelector('[data-stage-id="hypothesis_infra"]')).toHaveAttribute('data-stage-status', 'running')
    expect(container.querySelector('[data-stage-id="investigator_synthesis"]')).toHaveAttribute('data-stage-status', 'pending')
    // Ten dependency edges: plan → five hypotheses → synthesis.
    expect(container.querySelectorAll('path[data-edge-kind="dependency"]')).toHaveLength(10)
    expect(container.querySelector('[data-graph-truncated]')).toBeNull()
  })

  it('says how many stages a truncated projection does not draw', () => {
    const run = investigation({ tasks_truncated: true, observed_task_rows: 260 })
    expect(truncatedLine(run)).toBe('Showing 7 of 260 stages: 253 more are not drawn.')
    const { container } = render(<GenericComputeCanvas run={run} />)
    expect(container.querySelector('[data-graph-truncated]')).toHaveTextContent('Showing 7 of 260 stages: 253 more are not drawn.')
  })

  it('opens on the running stage, and a click shows the picked stage\'s tokens and cost in the rail', () => {
    const { container } = render(<GenericComputeCanvas run={investigation()} />)
    const rail = container.querySelector('aside') as HTMLElement
    expect(within(rail).getByRole('heading', { name: 'Hypothesis infra' })).toBeInTheDocument()
    fireEvent.click(container.querySelector('[data-stage-id="hypothesis_commit"]') as HTMLElement)
    expect(within(rail).getByRole('heading', { name: 'Hypothesis commit' })).toBeInTheDocument()
    expect(within(rail).getByText('1.5k')).toBeInTheDocument()
    // mapping.ts's formatter: two decimals from one cent up.
    expect(within(rail).getByText('$0.01')).toBeInTheDocument()
  })

  it('renders a stage error as text, never as markup', () => {
    const { container } = render(<GenericComputeCanvas run={investigation()} />)
    fireEvent.click(container.querySelector('[data-stage-id="hypothesis_regression"]') as HTMLElement)
    expect(container.querySelector('img')).toBeNull()
  })

  it('lists an unresolvable dependency under the canvas instead of drawing it', () => {
    const run = investigation()
    const tasks = [...run.tasks, task('cluster_investigation_join', { dependencies: ['cluster_investigation'] })]
    render(<GenericComputeCanvas run={{ ...run, tasks }} />)
    expect(screen.getByText(/Not drawn \(no such stage in this pipeline\)/)).toHaveTextContent('cluster_investigation_join ← cluster_investigation')
  })
})
