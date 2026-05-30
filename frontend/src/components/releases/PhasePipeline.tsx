/**
 * PhasePipeline — six chips in a single bar showing where a release sits
 * in its pipeline. Active chips get a teal halo; failed chips show the
 * failure-mode note inline; skip chip is fully muted.
 */
import { clsx } from 'clsx'
import type { DerivedPhase, PhaseState } from './types'

const STATE_STYLE: Record<PhaseState, {
  dot: string
  textColor: string
  bg: string
  border: string
  halo?: string
}> = {
  idle:    { dot: 'var(--color-text-faint)',  textColor: 'var(--color-text-muted)',     bg: 'var(--color-bg-secondary)', border: 'var(--color-border)' },
  done:    { dot: '#22c55e',                   textColor: '#86efac',                    bg: 'rgba(34,197,94,0.10)',      border: 'rgba(34,197,94,0.30)' },
  active:  { dot: '#14b8a6',                   textColor: '#5eead4',                    bg: 'rgba(20,184,166,0.10)',     border: 'rgba(20,184,166,0.40)',
             halo: '0 0 0 3px rgba(20,184,166,0.18)' },
  failed:  { dot: '#ef4444',                   textColor: '#fca5a5',                    bg: 'rgba(239,68,68,0.10)',      border: 'rgba(239,68,68,0.30)' },
  skipped: { dot: 'var(--color-text-faint)',   textColor: 'var(--color-text-faint)',    bg: 'var(--color-bg-secondary)', border: 'var(--color-border)' },
}

interface PhasePipelineProps {
  phases: DerivedPhase[]
  className?: string
}

export default function PhasePipeline({ phases, className }: PhasePipelineProps) {
  return (
    <div
      className={clsx('flex items-stretch overflow-hidden rounded-md border', className)}
      style={{ borderColor: 'var(--color-border)' }}
      role="list"
      aria-label="Release phase pipeline"
    >
      {phases.map((p, i) => {
        const s = STATE_STYLE[p.state]
        // Render the failure-mode note inline rather than the generic state
        // label — gives the user a one-glance reason without a tooltip.
        const label = p.state === 'failed' && p.note ? `${p.label} · ${p.note}` : p.label
        return (
          <div
            key={p.key}
            role="listitem"
            className={clsx(
              'flex-1 min-w-0 flex items-center gap-1.5 px-2.5 py-1.5 text-[11.5px]',
              i > 0 && 'border-l',
            )}
            style={{
              color: s.textColor,
              background: s.bg,
              borderLeftColor: 'var(--color-border)',
            }}
            aria-label={`${p.label} — ${p.state}`}
            title={p.state === 'failed' && p.note ? p.note : `${p.label} — ${p.state}`}
          >
            <span
              className="inline-block w-2 h-2 rounded-full shrink-0"
              style={{
                background: s.dot,
                border: `1px solid ${s.border}`,
                boxShadow: s.halo,
              }}
              aria-hidden
            />
            <span className="truncate">{label}</span>
          </div>
        )
      })}
    </div>
  )
}
