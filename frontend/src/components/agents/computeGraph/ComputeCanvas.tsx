/**
 * ComputeCanvas — the dotted-background, scroll-capable canvas that hosts the
 * stage nodes, decision diamond, and SVG edge layer for the /agents Direction-C
 * compute graph.
 *
 * The inner content is a fixed-size 1750×560 stage; the outer wrapper handles
 * horizontal/vertical scroll when the viewport is narrower. The handoff is
 * explicit that we should NOT auto-fit / zoom-to-fit — narrow viewports get a
 * scrollbar.
 */
import { clsx } from 'clsx'
import { Fragment } from 'react'
import type { ComputeStage, ComputeDecision, ComputeEdge, SelectedId } from './types'
import {
  CANVAS_H,
  CANVAS_W,
  edgeLabelPosition,
  edgePath,
} from './layout'
import ComputeNode from './ComputeNode'
import DecisionDiamond from './DecisionDiamond'

interface ComputeCanvasProps {
  stages: ComputeStage[]
  decision: ComputeDecision | null
  edges: ComputeEdge[]
  selectedId: SelectedId
  onSelect: (id: SelectedId) => void
  className?: string
}

export default function ComputeCanvas({
  stages,
  decision,
  edges,
  selectedId,
  onSelect,
  className,
}: ComputeCanvasProps) {
  return (
    <div
      className={clsx('flex-1 min-h-0 overflow-auto', className)}
      style={{
        // Dotted 24×24 grid on top of the page background. The token is
        // defined in index.css so the same look can be reused elsewhere.
        background: 'var(--pattern-compute-grid), var(--color-bg)',
      }}
    >
      <div
        className="relative"
        style={{
          width: CANVAS_W,
          height: CANVAS_H,
          padding: 24,
        }}
      >
        {/* SVG edge layer — covers the whole canvas, ignores pointer events
            so the absolutely-positioned nodes still receive clicks. */}
        <svg
          className="absolute inset-0 pointer-events-none"
          width={CANVAS_W}
          height={CANVAS_H}
          aria-hidden
        >
          <defs>
            <marker
              id="c-arrow"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="6"
              markerHeight="6"
              orient="auto-start-reverse"
            >
              <path d="M0,0 L10,5 L0,10 z" fill="var(--border-strong, #4a5564)" />
            </marker>
            <marker
              id="c-arrow-blue"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="6"
              markerHeight="6"
              orient="auto-start-reverse"
            >
              <path d="M0,0 L10,5 L0,10 z" fill="var(--color-accent, #b8f24a)" />
            </marker>
            <marker
              id="c-arrow-faint"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="6"
              markerHeight="6"
              orient="auto-start-reverse"
            >
              <path d="M0,0 L10,5 L0,10 z" fill="var(--fg-faint, #4a525c)" />
            </marker>
          </defs>

          {edges.map((edge, i) => {
            const isChosen = edge.kind === 'chosen'
            const isNot    = edge.kind === 'notchosen'
            const dashed   = edge.kind === 'dashed' || isNot
            const stroke = isChosen
              ? 'var(--color-accent, #b8f24a)'
              : isNot
                ? 'var(--fg-faint, #4a525c)'
                : 'var(--border-strong, #4a5564)'
            const marker = isChosen
              ? 'url(#c-arrow-blue)'
              : isNot
                ? 'url(#c-arrow-faint)'
                : 'url(#c-arrow)'
            return (
              <path
                key={`${edge.from}-${edge.to}-${i}`}
                d={edgePath(edge.from, edge.to)}
                fill="none"
                stroke={stroke}
                strokeWidth={2}
                strokeDasharray={dashed ? '5 5' : '0'}
                opacity={isNot ? 0.55 : 1}
                markerEnd={marker}
              />
            )
          })}
        </svg>

        {/* Edge labels — outside the SVG so they can use the codebase's
            font-mono and pick up var(--bg-1)/etc. without a foreignObject. */}
        {edges
          .filter(e => e.label)
          .map((edge, i) => {
            const { x, y } = edgeLabelPosition(edge.from, edge.to)
            const isChosen = edge.kind === 'chosen'
            const isNot    = edge.kind === 'notchosen'
            return (
              <div
                key={`label-${edge.from}-${edge.to}-${i}`}
                className="absolute font-mono text-[9.5px] px-1.5 py-0.5 rounded-sm border"
                style={{
                  left: x,
                  top: y,
                  transform: 'translate(-50%, -50%)',
                  color: isChosen
                    ? 'var(--color-accent, #b8f24a)'
                    : isNot
                      ? 'var(--fg-faint, #4a525c)'
                      : 'var(--color-text-muted)',
                  background: isChosen
                    ? 'rgba(68,147,248,0.06)'
                    : 'var(--color-bg-card)',
                  borderColor: isChosen
                    ? 'rgba(68,147,248,0.40)'
                    : 'var(--color-border)',
                }}
              >
                {edge.label}
              </div>
            )
          })}

        {/* Decision diamond (if any). Rendered before the stage nodes so
            its outline never paints over an overlapping node hit area. */}
        {decision && (
          <DecisionDiamond
            decision={decision}
            selected={selectedId === decision.id}
            onSelect={() => onSelect(decision.id)}
          />
        )}

        {/* Stage nodes — absolutely positioned per layout.POS. */}
        {stages.map(stage => (
          <Fragment key={stage.id}>
            <ComputeNode
              stage={stage}
              selected={selectedId === stage.id}
              onSelect={() => onSelect(stage.id)}
            />
          </Fragment>
        ))}
      </div>
    </div>
  )
}
