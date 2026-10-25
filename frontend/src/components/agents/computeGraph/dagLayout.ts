/**
 * W14 AGT-06: the compute graph of ANY pipeline topology, laid out in plain
 * TypeScript (no npm dependency).
 *
 * `graphFromRuntime` turns the `/agentic-runtime` projection (`AgenticRunV1`:
 * the run's tasks plus up to 20 child investigations' tasks) into nodes and
 * edges; `layoutDag` places them in layers (longest path), columns left to
 * right. The hand-placed canvas (`layout.ts`) stays for the built-in offline
 * pipeline; everything else — deep (20 stages), live, investigations,
 * single-agent invocations, published forks — is drawn from here.
 *
 * Pure and deterministic: a shuffled task list yields the same coordinates.
 * Positions are numbers, placed by `ComputeNode` through its `left` / `top`
 * style object and by SVG attributes — never a style string built from data.
 */
import { NODE_H, NODE_W } from './layout'
import { formatCost, formatTokens, glyphFor, knownStageName } from './mapping'
import type { GenericComputeStage, StageStatus } from './types'

export const DAG_NODE_W = NODE_W
export const DAG_NODE_H = NODE_H
export const DAG_COLUMN_PITCH = 274
export const DAG_ROW_PITCH = 156
export const DAG_PADDING = 24

/** The fields of one `AgentTaskV1` the graph reads (structural: G-A's `AgenticTask` fits). */
export interface RuntimeTaskInput {
  task_id: string
  parent_task_id?: string | null
  source_pipeline_run_id?: string | null
  stage_name: string
  status: string
  selected?: boolean
  selection_reason?: string | null
  dependencies?: readonly string[] | null
  attempt?: number | null
  usage?: {
    total_tokens?: number | null
    cost_usd?: number | null
    duration_ms?: number | null
    llm_calls?: number | null
  } | null
  stop_reason?: string | null
  started_at?: string | null
  completed_at?: string | null
  error?: string | null
}

/** The fields of one `AgenticRunV1` the graph reads. */
export interface RuntimeInput {
  root_task_id: string
  source_pipeline_run_id: string
  tasks: readonly RuntimeTaskInput[]
  tasks_truncated?: boolean
  children_truncated?: boolean
  observed_task_rows?: number
}

/** The root task of a pipeline (the run's, and each child investigation's). */
export const ROOT_STAGE_NAME = 'pipeline'

export interface DagNode {
  /** `stage_name` for the run's own stages; `<child pipeline id>/<stage_name>` for a child's. */
  id: string
  stageName: string
  sourcePipelineRunId: string | null
  /** True for a stage of a child investigation (another pipeline under this run). */
  child: boolean
  /** The latest attempt's task. */
  task: RuntimeTaskInput
  /** The latest attempt number (1 when not reported). */
  attempt: number
  /** Shuffle-proof rank: the stage rows' order (started_at, stage_name), then the id. */
  rank: number
}

export type DagEdgeKind = 'dependency' | 'parent' | 'loop'

export interface DagEdge {
  from: string
  to: string
  kind: DagEdgeKind
}

export interface DagGraph {
  nodes: DagNode[]
  edges: DagEdge[]
  /** A dependency naming no stage of the same pipeline: listed as text, never drawn. */
  unresolved: Array<{ node: string; dependency: string }>
}

function compareText(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0
}

function nodeIdFor(run: RuntimeInput, task: RuntimeTaskInput): string {
  const source = task.source_pipeline_run_id ?? run.source_pipeline_run_id
  return source === run.source_pipeline_run_id ? task.stage_name : `${source}/${task.stage_name}`
}

/** Later attempt wins; then the later start; then the task id (so the pick never depends on input order). */
function isLater(candidate: RuntimeTaskInput, current: RuntimeTaskInput): boolean {
  const ca = candidate.attempt ?? 1
  const cu = current.attempt ?? 1
  if (ca !== cu) return ca > cu
  const cs = candidate.started_at ?? ''
  const us = current.started_at ?? ''
  if (cs !== us) return cs > us
  return candidate.task_id > current.task_id
}

/**
 * Tasks → nodes (one per `(source pipeline, stage_name)`, its latest attempt;
 * the root `pipeline` tasks are not nodes) and edges (each dependency resolved
 * to the stage of that name in the SAME pipeline; a dashed parent → child edge
 * for a task with no resolved dependency whose parent is a node — a child
 * investigation under the stage that spawned it, a legacy hypothesis row).
 */
export function graphFromRuntime(run: RuntimeInput): DagGraph {
  const byTaskId = new Map<string, RuntimeTaskInput>()
  for (const task of run.tasks) byTaskId.set(task.task_id, task)

  const latest = new Map<string, RuntimeTaskInput>()
  for (const task of run.tasks) {
    if (task.stage_name === ROOT_STAGE_NAME) continue
    const id = nodeIdFor(run, task)
    const current = latest.get(id)
    if (!current || isLater(task, current)) latest.set(id, task)
  }
  // Every attempt's task id → its node, so a parent link to an older attempt still lands.
  const nodeOfTask = new Map<string, string>()
  for (const task of run.tasks) {
    if (task.stage_name !== ROOT_STAGE_NAME) nodeOfTask.set(task.task_id, nodeIdFor(run, task))
  }

  const ordered = [...latest.entries()].sort(([ia, a], [ib, b]) =>
    compareText(a.started_at ?? '', b.started_at ?? '')
    || compareText(a.stage_name, b.stage_name)
    || compareText(ia, ib))
  const nodes: DagNode[] = ordered.map(([id, task], rank) => {
    const source = task.source_pipeline_run_id ?? run.source_pipeline_run_id
    return {
      id,
      stageName: task.stage_name,
      sourcePipelineRunId: source,
      child: source !== run.source_pipeline_run_id,
      task,
      attempt: Math.max(1, task.attempt ?? 1),
      rank,
    }
  })
  const nodeIds = new Set(nodes.map(n => n.id))

  const edges: DagEdge[] = []
  const seen = new Set<string>()
  const addEdge = (from: string, to: string, kind: DagEdgeKind) => {
    const key = `${from} -> ${to}`
    if (from === to || seen.has(key)) return
    seen.add(key)
    edges.push({ from, to, kind })
  }
  const unresolved: DagGraph['unresolved'] = []

  // The node a parent link points at: through child-pipeline roots (not nodes)
  // up to the stage that spawned them; the run's own root is no node.
  const parentNode = (task: RuntimeTaskInput): string | null => {
    let parentId = task.parent_task_id ?? null
    const visited = new Set<string>()
    while (parentId && !visited.has(parentId)) {
      visited.add(parentId)
      if (parentId === run.root_task_id) return null
      const node = nodeOfTask.get(parentId)
      if (node) return node
      const parent = byTaskId.get(parentId)
      if (!parent || parent.stage_name !== ROOT_STAGE_NAME) return null
      parentId = parent.parent_task_id ?? null
    }
    return null
  }

  for (const node of nodes) {
    const prefix = node.child ? `${node.sourcePipelineRunId}/` : ''
    let resolvedAny = false
    for (const dependency of [...(node.task.dependencies ?? [])].sort(compareText)) {
      const target = `${prefix}${dependency}`
      if (nodeIds.has(target)) {
        addEdge(target, node.id, 'dependency')
        resolvedAny = true
      } else {
        unresolved.push({ node: node.id, dependency })
      }
    }
    if (!resolvedAny) {
      const parent = parentNode(node.task)
      if (parent && parent !== node.id) addEdge(parent, node.id, 'parent')
    }
  }
  edges.sort((a, b) => compareText(a.from, b.from) || compareText(a.to, b.to))
  return { nodes, edges, unresolved }
}

export interface DagLayout {
  /** Each node's top-left, in pixels inside the canvas. */
  positions: Record<string, { x: number; y: number }>
  /** Node ids per layer (column), top to bottom. */
  layers: string[][]
  /** The graph's edges, back edges re-kinded `loop` (dashed, never layered). */
  edges: DagEdge[]
  width: number
  height: number
}

/**
 * Longest-path layering. A DFS (in rank order) marks every edge that closes a
 * cycle as `loop`: drawn dashed and left out of the layering, so a cycle never
 * hangs the page. Inside a layer, nodes sit by the barycentre of their
 * predecessors' rows (one top-down pass), ties by rank.
 */
export function layoutDag(graph: DagGraph): DagLayout {
  const rankOf = new Map(graph.nodes.map(n => [n.id, n.rank]))
  const byRank = (a: string, b: string) => (rankOf.get(a) ?? 0) - (rankOf.get(b) ?? 0)
  const out = new Map<string, DagEdge[]>()
  for (const edge of graph.edges) out.set(edge.from, [...(out.get(edge.from) ?? []), edge])
  for (const list of out.values()) list.sort((a, b) => byRank(a.to, b.to))

  const state = new Map<string, 'open' | 'done'>()
  const loops = new Set<DagEdge>()
  const topo: string[] = []
  const visit = (start: string) => {
    // Iterative DFS: a 200-stage chain must not overflow the stack.
    const stack: Array<{ id: string; next: number }> = [{ id: start, next: 0 }]
    state.set(start, 'open')
    while (stack.length > 0) {
      const frame = stack[stack.length - 1]
      const list = out.get(frame.id) ?? []
      if (frame.next < list.length) {
        const edge = list[frame.next]
        frame.next += 1
        const seenState = state.get(edge.to)
        if (seenState === 'open') loops.add(edge)
        else if (seenState === undefined) {
          state.set(edge.to, 'open')
          stack.push({ id: edge.to, next: 0 })
        }
      } else {
        state.set(frame.id, 'done')
        topo.push(frame.id)
        stack.pop()
      }
    }
  }
  for (const node of graph.nodes) if (!state.has(node.id)) visit(node.id)
  topo.reverse()

  const edges = graph.edges.map(e => (loops.has(e) ? { ...e, kind: 'loop' as const } : e))
  const preds = new Map<string, string[]>()
  for (const edge of edges) {
    if (edge.kind === 'loop') continue
    preds.set(edge.to, [...(preds.get(edge.to) ?? []), edge.from])
  }
  const layerOf = new Map<string, number>()
  for (const id of topo) {
    const p = preds.get(id) ?? []
    layerOf.set(id, p.length === 0 ? 0 : 1 + Math.max(...p.map(x => layerOf.get(x) ?? 0)))
  }

  const layerCount = graph.nodes.length === 0 ? 0 : 1 + Math.max(...[...layerOf.values()])
  const layers: string[][] = Array.from({ length: layerCount }, () => [])
  for (const node of graph.nodes) layers[layerOf.get(node.id) ?? 0].push(node.id)
  const rowOf = new Map<string, number>()
  layers.forEach((ids, index) => {
    if (index === 0) {
      ids.sort(byRank)
    } else {
      const centre = (id: string) => {
        const rows = (preds.get(id) ?? []).map(p => rowOf.get(p) ?? 0)
        return rows.length === 0 ? 0 : rows.reduce((a, b) => a + b, 0) / rows.length
      }
      ids.sort((a, b) => centre(a) - centre(b) || byRank(a, b))
    }
    ids.forEach((id, row) => rowOf.set(id, row))
  })

  const positions: DagLayout['positions'] = {}
  layers.forEach((ids, column) => ids.forEach((id, row) => {
    positions[id] = { x: DAG_PADDING + column * DAG_COLUMN_PITCH, y: DAG_PADDING + row * DAG_ROW_PITCH }
  }))
  const maxRows = Math.max(1, ...layers.map(ids => ids.length))
  return {
    positions,
    layers,
    edges,
    width: Math.max(1, layerCount) * DAG_COLUMN_PITCH + 2 * DAG_PADDING,
    height: maxRows * DAG_ROW_PITCH + 2 * DAG_PADDING,
  }
}

/** A task status as the card shows it (`StageStatus` + the pill / note the five values cannot say). */
export function taskStatus(status: string): { status: StageStatus; pill: string | null; note: string | null } {
  switch (status) {
    case 'completed': return { status: 'done', pill: null, note: null }
    case 'partial': return { status: 'done', pill: null, note: 'partial' }
    case 'cancelled': return { status: 'skipped', pill: 'cancelled', note: null }
    case 'pending':
    case 'running':
    case 'failed':
    case 'skipped':
      return { status, pill: null, note: null }
    default: return { status: 'pending', pill: status || null, note: null }
  }
}

function humanStageName(stageName: string): string {
  const known = knownStageName(stageName)
  if (known) return known
  const words = stageName.replace(/_/g, ' ').trim()
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : stageName
}

function durationLabel(ms: number | null | undefined): string | null {
  if (ms == null || !Number.isFinite(ms) || ms <= 0) return null
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
}

/** One node's card: text only (names, reasons and errors are React text, never HTML). */
export function genericStage(node: DagNode): GenericComputeStage {
  const { status, pill, note } = taskStatus(node.task.status)
  const usage = node.task.usage ?? {}
  const desc = [
    node.attempt > 1 ? `attempt ${node.attempt}` : null,
    node.child ? 'child investigation' : null,
    node.task.selected === false ? 'not selected' : null,
  ].filter(Boolean).join(' · ') || (node.task.selection_reason ?? '')
  return {
    id: node.id,
    name: humanStageName(node.stageName),
    desc,
    status,
    pill,
    note,
    dur: durationLabel(usage.duration_ms),
    metrics: {
      tokens: formatTokens(usage.total_tokens),
      cost: formatCost(usage.cost_usd),
    },
    glyph: glyphFor(node.stageName),
    skipReason: status === 'skipped' ? node.task.stop_reason ?? null : null,
    error: node.task.error ?? null,
  }
}

/** `Showing {shown} of {observed} stages: {n} more are not drawn.` — or null when nothing was cut. */
export function truncatedLine(run: RuntimeInput): string | null {
  if (!run.tasks_truncated && !run.children_truncated) return null
  const shown = run.tasks.filter(t => t.stage_name !== ROOT_STAGE_NAME).length
  const observed = Math.max(run.observed_task_rows ?? shown, shown)
  const more = observed - shown
  if (more <= 0) return `Showing ${shown} stages: more child investigations ran than are drawn.`
  return `Showing ${shown} of ${observed} stages: ${more} more are not drawn.`
}

/** The SVG `d` of an edge: the hand canvas's cubic, source right-middle → target left-middle. */
export function dagEdgePath(from: { x: number; y: number }, to: { x: number; y: number }): string {
  const fromX = from.x + DAG_NODE_W
  const fromY = from.y + DAG_NODE_H / 2
  const toX = to.x
  const toY = to.y + DAG_NODE_H / 2
  const cx = (fromX + toX) / 2
  return `M${fromX},${fromY} C ${cx},${fromY} ${cx},${toY} ${toX},${toY}`
}
