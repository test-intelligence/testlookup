/**
 * W14 AGT-06: the compute graph of ANY pipeline topology — built-in deep and
 * live, investigations, single-agent invocations, published forks and a deep
 * run's child investigations — from the `/agentic-runtime` projection.
 *
 * The same cards (`ComputeNode`, placed by the layout's numbers), the same
 * cubic edges and the same 360 px rail as the hand canvas. A partial answer is
 * never shown as the whole: a truncated projection says how many stages are
 * not drawn, and a dependency that resolves to no stage is listed as text.
 */
import { useMemo, useState } from 'react'
import ComputeNode from './ComputeNode'
import RightRail from './RightRail'
import { dagEdgePath, genericStage, graphFromRuntime, layoutDag, truncatedLine, type RuntimeInput } from './dagLayout'
import type { AnySelectedId } from './types'

const EDGE_STYLE = {
  dependency: { stroke: 'var(--border-strong)', dash: '0', marker: 'url(#g-arrow)', opacity: 1 },
  parent: { stroke: 'var(--border-strong)', dash: '5 5', marker: 'url(#g-arrow)', opacity: 1 },
  loop: { stroke: 'var(--color-accent)', dash: '5 5', marker: 'url(#g-arrow-blue)', opacity: 1 },
  notchosen: { stroke: 'var(--fg-faint)', dash: '5 5', marker: 'url(#g-arrow-faint)', opacity: 0.55 },
} as const

function Marker({ id, fill }: { id: string; fill: string }) {
  return (
    <marker id={id} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z" fill={fill} />
    </marker>
  )
}

export default function GenericComputeCanvas({ run }: { run: RuntimeInput }) {
  const { graph, layout, stages } = useMemo(() => {
    const g = graphFromRuntime(run)
    return { graph: g, layout: layoutDag(g), stages: g.nodes.map(genericStage) }
  }, [run])
  const [selection, setSelection] = useState<AnySelectedId>(null)
  const initial =
    (stages.find(s => s.status === 'running') ?? stages.find(s => s.status === 'failed') ?? stages[0])?.id ?? null
  const selectedId = selection ?? initial
  const notSelected = new Set(graph.nodes.filter(n => n.task.selected === false).map(n => n.id))
  const note = truncatedLine(run)

  if (stages.length === 0) {
    return (
      <p className="px-6 py-5 text-sm text-[var(--color-text-muted)]">
        No stage of this pipeline has been recorded yet.
      </p>
    )
  }

  return (
    <div data-generic-graph="">
      {note && (
        <p data-graph-truncated="" className="m-0 border-b border-[var(--color-border)] px-4 py-2 text-xs text-[var(--color-text-muted)]">
          {note}
        </p>
      )}
      <div className="flex" style={{ height: 600 }}>
        <div
          className="flex-1 min-h-0 overflow-auto"
          style={{ background: 'var(--pattern-compute-grid), var(--color-bg)' }}
        >
          <div className="relative" style={{ width: layout.width, height: layout.height }}>
            <svg className="absolute inset-0 pointer-events-none" width={layout.width} height={layout.height} aria-hidden>
              <defs>
                <Marker id="g-arrow" fill="var(--border-strong)" />
                <Marker id="g-arrow-blue" fill="var(--color-accent)" />
                <Marker id="g-arrow-faint" fill="var(--fg-faint)" />
              </defs>
              {layout.edges.map(edge => {
                const from = layout.positions[edge.from]
                const to = layout.positions[edge.to]
                if (!from || !to) return null
                const look = EDGE_STYLE[edge.kind !== 'loop' && notSelected.has(edge.to) ? 'notchosen' : edge.kind]
                return (
                  <path
                    key={`${edge.from}->${edge.to}`}
                    data-edge-kind={edge.kind}
                    d={dagEdgePath(from, to)}
                    fill="none"
                    stroke={look.stroke}
                    strokeWidth={2}
                    strokeDasharray={look.dash}
                    opacity={look.opacity}
                    markerEnd={look.marker}
                  />
                )
              })}
            </svg>
            {stages.map(stage => (
              <ComputeNode
                key={stage.id}
                stage={stage}
                position={layout.positions[stage.id]}
                selected={selectedId === stage.id}
                onSelect={() => setSelection(stage.id)}
              />
            ))}
          </div>
        </div>
        <RightRail selectedId={selectedId} stages={stages} decision={null} />
      </div>
      {graph.unresolved.length > 0 && (
        <p data-graph-unresolved="" className="m-0 border-t border-[var(--color-border)] px-4 py-2 text-xs text-[var(--color-text-muted)]">
          Not drawn (no such stage in this pipeline):{' '}
          {graph.unresolved.map(u => `${u.node} ← ${u.dependency}`).join(', ')}
        </p>
      )}
    </div>
  )
}
