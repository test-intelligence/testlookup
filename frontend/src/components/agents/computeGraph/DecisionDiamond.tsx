/**
 * DecisionDiamond — 88×88 square rotated 45° to look like a diamond, used to
 * represent a runtime branch (e.g. ``route_analysis_mode``). The inner content
 * un-rotates so the eyebrow + key read horizontally.
 */
import { clsx } from 'clsx'
import type { ComputeDecision } from './types'
import { DECISION_SIZE, POS } from './layout'

interface DecisionDiamondProps {
  decision: ComputeDecision
  selected: boolean
  onSelect: () => void
}

export default function DecisionDiamond({ decision, selected, onSelect }: DecisionDiamondProps) {
  const pos = POS[decision.id]
  // Strip the ``route_`` prefix the design wants to hide; whole id is in the rail.
  const compactKey = decision.label.replace(/^route_/, '')
  return (
    <button
      type="button"
      onClick={onSelect}
      title={decision.rationale ?? undefined}
      className={clsx(
        'absolute focus:outline-none focus:ring-2 focus:ring-[var(--color-purple,#a371f7)]/50',
      )}
      style={{
        left: pos.x,
        top: pos.y,
        width: DECISION_SIZE,
        height: DECISION_SIZE,
        transform: 'rotate(45deg)',
        background: selected
          ? 'rgba(163,113,247,0.18)'
          : 'rgba(163,113,247,0.08)',
        border: '1.5px solid var(--color-purple, #a371f7)',
        borderRadius: 8,
        boxShadow: selected ? '0 0 0 3px var(--color-purple-soft, rgba(163,113,247,0.14))' : undefined,
        cursor: 'pointer',
      }}
      aria-label={`Decision: ${decision.label}`}
    >
      {/* Un-rotate so eyebrow + key sit horizontally inside the diamond. */}
      <div
        className="w-full h-full flex flex-col items-center justify-center gap-0.5"
        style={{ transform: 'rotate(-45deg)' }}
      >
        <div
          className="text-[8.5px] uppercase"
          style={{
            letterSpacing: '0.12em',
            color: 'var(--color-purple, #a371f7)',
            opacity: 0.8,
          }}
        >
          decide
        </div>
        <div
          className="text-[10px] font-mono"
          style={{ color: 'var(--color-purple, #a371f7)' }}
        >
          {compactKey}
        </div>
      </div>
    </button>
  )
}
