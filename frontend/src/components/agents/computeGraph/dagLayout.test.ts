/**
 * W14 AGT-06: the generic compute graph's layout (contract §2.2 tests 1-2).
 *
 * The fixtures are built here, in the shape of `/agentic-runtime`
 * (`AgenticRunV1`): a root `pipeline` task, then one task per stage row.
 */
import { describe, expect, it } from 'vitest'

import {
  DAG_COLUMN_PITCH,
  DAG_PADDING,
  DAG_ROW_PITCH,
  genericStage,
  graphFromRuntime,
  layoutDag,
  taskStatus,
  type RuntimeInput,
  type RuntimeTaskInput,
} from './dagLayout'

const PIPE = 'pipe-1'
const ROOT = `pipeline:${PIPE}`

function task(stage: string, over: Partial<RuntimeTaskInput> = {}): RuntimeTaskInput {
  return {
    task_id: `${ROOT}:stage:${stage}:attempt:${over.attempt ?? 1}`,
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

function run(tasks: RuntimeTaskInput[], over: Partial<RuntimeInput> = {}): RuntimeInput {
  const root: RuntimeTaskInput = {
    task_id: ROOT, parent_task_id: null, source_pipeline_run_id: PIPE,
    stage_name: 'pipeline', status: 'completed', selected: true, dependencies: [],
  }
  return { root_task_id: ROOT, source_pipeline_run_id: PIPE, tasks: [root, ...tasks], ...over }
}

const HYPOTHESES = ['hypothesis_infra', 'hypothesis_commit', 'hypothesis_environment', 'hypothesis_known_flaky', 'hypothesis_regression']
const PLAN_ID = `${ROOT}:stage:investigator_plan:attempt:1`

function investigation(): RuntimeInput {
  return run([
    task('investigator_plan', { started_at: '2026-10-20T10:00:00Z' }),
    ...HYPOTHESES.map((h, i) => task(h, {
      parent_task_id: PLAN_ID,
      dependencies: ['investigator_plan'],
      started_at: `2026-10-20T10:00:0${i + 1}Z`,
      status: i === 2 ? 'failed' : 'completed',
    })),
    task('investigator_synthesis', { dependencies: HYPOTHESES, started_at: '2026-10-20T10:01:00Z' }),
  ])
}

/** The built-in deep workflow's 20 steps with the incoming lists `_create_pipeline_run` writes from its edges. */
const DEEP_INCOMING: Record<string, string[]> = {
  ingestion: [],
  anomaly_detection: ['ingestion'],
  root_cause_analysis: ['ingestion'],
  summary: ['anomaly_detection', 'root_cause_analysis'],
  triage: ['summary'],
  failure_clustering: ['summary', 'triage'],
  cluster_investigation_dispatch: ['failure_clustering'],
  cluster_investigation_join: ['cluster_investigation_dispatch'],
  contract_validation: ['cluster_investigation_join'],
  log_intelligence: ['cluster_investigation_join'],
  regression_watchman: ['cluster_investigation_join'],
  change_ownership: ['cluster_investigation_join'],
  defect_commander: ['cluster_investigation_join'],
  gap_detection: ['contract_validation', 'log_intelligence', 'regression_watchman', 'change_ownership', 'defect_commander'],
  report_refinement: ['gap_detection'],
  flaky_sentinel: ['report_refinement'],
  test_health: ['flaky_sentinel'],
  release_risk: ['test_health'],
  decision_report: ['release_risk'],
  decision_report_critic: ['decision_report'],
}

describe('layoutDag', () => {
  it('lays the investigation out as plan → five hypotheses in one column → synthesis', () => {
    const layout = layoutDag(graphFromRuntime(investigation()))
    expect(layout.layers.map(l => l.length)).toEqual([1, 5, 1])
    expect(layout.layers[0]).toEqual(['investigator_plan'])
    expect(layout.layers[2]).toEqual(['investigator_synthesis'])
    expect(new Set(layout.layers[1])).toEqual(new Set(HYPOTHESES))
    // The canvas size of the contract: 3 × 274 + 48 wide, 5 × 156 + 48 high.
    expect(layout.width).toBe(3 * DAG_COLUMN_PITCH + 2 * DAG_PADDING)
    expect(layout.height).toBe(5 * DAG_ROW_PITCH + 2 * DAG_PADDING)
    expect(layout.positions.investigator_plan).toEqual({ x: DAG_PADDING, y: DAG_PADDING })
    expect(layout.positions.investigator_synthesis.x).toBe(DAG_PADDING + 2 * DAG_COLUMN_PITCH)
  })

  it('gives the built-in deep workflow its longest-path layer count (15 columns, the specialists in one)', () => {
    const names = Object.keys(DEEP_INCOMING)
    expect(names).toHaveLength(20)
    const graph = graphFromRuntime(run(names.map(n => task(n, { dependencies: DEEP_INCOMING[n] }))))
    const layout = layoutDag(graph)
    expect(layout.layers).toHaveLength(15)
    const specialists = layout.layers.find(l => l.includes('contract_validation'))
    expect(specialists?.sort()).toEqual(['change_ownership', 'contract_validation', 'defect_commander', 'log_intelligence', 'regression_watchman'])
    expect(layout.layers[14]).toEqual(['decision_report_critic'])
    expect(graph.unresolved).toEqual([])
  })

  it('terminates on a cycle with exactly one loop edge, left out of the layering', () => {
    const graph = graphFromRuntime(run([
      task('a', { dependencies: ['b'], started_at: '2026-10-20T10:00:00Z' }),
      task('b', { dependencies: ['a'], started_at: '2026-10-20T10:00:01Z' }),
    ]))
    const layout = layoutDag(graph)
    expect(layout.edges.filter(e => e.kind === 'loop')).toHaveLength(1)
    expect(layout.layers.flat().sort()).toEqual(['a', 'b'])
    expect(layout.layers).toHaveLength(2)
  })

  it('is deterministic: a shuffled task list yields the same coordinates', () => {
    const base = investigation()
    const reversed: RuntimeInput = { ...base, tasks: [...base.tasks].reverse() }
    const rotated: RuntimeInput = { ...base, tasks: [...base.tasks.slice(3), ...base.tasks.slice(0, 3)] }
    const expected = layoutDag(graphFromRuntime(base))
    expect(layoutDag(graphFromRuntime(reversed))).toEqual(expected)
    expect(layoutDag(graphFromRuntime(rotated))).toEqual(expected)
  })

  it('lists a dependency that names no stage of the pipeline as unresolved, never as an edge', () => {
    const graph = graphFromRuntime(run([
      task('cluster_investigation_dispatch'),
      task('cluster_investigation_join', { dependencies: ['cluster_investigation_dispatch', 'cluster_investigation'] }),
    ]))
    expect(graph.unresolved).toEqual([{ node: 'cluster_investigation_join', dependency: 'cluster_investigation' }])
    expect(graph.edges).toEqual([{ from: 'cluster_investigation_dispatch', to: 'cluster_investigation_join', kind: 'dependency' }])
  })
})

describe('graphFromRuntime', () => {
  it('leaves the root task out: seven nodes for the investigation', () => {
    const graph = graphFromRuntime(investigation())
    expect(graph.nodes.map(n => n.id)).not.toContain('pipeline')
    expect(graph.nodes).toHaveLength(7)
    expect(graph.edges.filter(e => e.to === 'investigator_synthesis')).toHaveLength(5)
  })

  it('draws ONE node per stage — its latest attempt, which says so', () => {
    const graph = graphFromRuntime(run([
      task('summary', { attempt: 2, status: 'completed', usage: { total_tokens: 2400, cost_usd: 0.03, duration_ms: 65000 } }),
      task('summary', { attempt: 1, status: 'failed' }),
    ]))
    expect(graph.nodes).toHaveLength(1)
    const card = genericStage(graph.nodes[0])
    expect(card.status).toBe('done')
    expect(card.desc).toBe('attempt 2')
    expect(card.metrics).toEqual({ tokens: '2.4k', cost: '$0.03' })
    expect(card.dur).toBe('1m 5s')
    expect(card.name).toBe('Summary')
    expect(card.glyph).toBe('file')
  })

  it('maps the task statuses onto the card (cancelled and partial keep their word)', () => {
    expect(taskStatus('completed')).toEqual({ status: 'done', pill: null, note: null })
    expect(taskStatus('partial')).toEqual({ status: 'done', pill: null, note: 'partial' })
    expect(taskStatus('cancelled')).toEqual({ status: 'skipped', pill: 'cancelled', note: null })
    for (const s of ['pending', 'running', 'failed', 'skipped'] as const) {
      expect(taskStatus(s)).toEqual({ status: s, pill: null, note: null })
    }
  })

  it('attaches a child investigation under the stage that spawned it (a dashed parent edge)', () => {
    const child = 'child-pipe'
    const childRoot = `pipeline:${child}`
    const dispatchId = `${ROOT}:stage:cluster_investigation_dispatch:attempt:1`
    const graph = graphFromRuntime(run([
      task('cluster_investigation_dispatch'),
      { task_id: childRoot, parent_task_id: dispatchId, source_pipeline_run_id: child, stage_name: 'pipeline', status: 'completed' },
      task('investigator_plan', { task_id: `${childRoot}:stage:investigator_plan:attempt:1`, parent_task_id: childRoot, source_pipeline_run_id: child }),
      task('hypothesis_infra', {
        task_id: `${childRoot}:stage:hypothesis_infra:attempt:1`,
        parent_task_id: `${childRoot}:stage:investigator_plan:attempt:1`,
        source_pipeline_run_id: child,
        dependencies: ['investigator_plan'],
      }),
    ]))
    const plan = `${child}/investigator_plan`
    expect(graph.nodes.map(n => n.id).sort()).toEqual(['child-pipe/hypothesis_infra', plan, 'cluster_investigation_dispatch'].sort())
    expect(graph.nodes.find(n => n.id === plan)?.child).toBe(true)
    expect(graph.edges).toContainEqual({ from: 'cluster_investigation_dispatch', to: plan, kind: 'parent' })
    // The child's own dependency resolves inside the CHILD pipeline, not the parent.
    expect(graph.edges).toContainEqual({ from: plan, to: 'child-pipe/hypothesis_infra', kind: 'dependency' })
  })

  it('a legacy hypothesis row without dependencies hangs off its parent plan', () => {
    const graph = graphFromRuntime(run([
      task('investigator_plan'),
      task('hypothesis_commit', { parent_task_id: PLAN_ID, dependencies: [] }),
    ]))
    expect(graph.edges).toEqual([{ from: 'investigator_plan', to: 'hypothesis_commit', kind: 'parent' }])
    const hypothesis = graph.nodes.find(n => n.id === 'hypothesis_commit')
    expect(hypothesis && genericStage(hypothesis).glyph).toBe('bot')
  })
})
