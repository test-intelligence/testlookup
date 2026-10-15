/**
 * PhasePipeline — one chip per phase of the release, in a single bar, showing
 * where the release sits in its pipeline. The chips are the release's OWN
 * phases (name and status, REL-01): there is no fixed template, so a release
 * with "Build verification" and "Soak" shows exactly those two. At most
 * `MAX_PHASE_CHIPS` chips, then a `+N` chip; no phases renders nothing.
 * Active chips get a teal halo; failed chips show the failure-mode note
 * inline; skip chip is fully muted.
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
  done:    { dot: 'var(--status-passed)',                   textColor: 'var(--status-passed)',                    bg: 'color-mix(in srgb, var(--status-passed) 10%, transparent)',      border: 'color-mix(in srgb, var(--status-passed) 30%, transparent)' },
  active:  { dot: 'var(--color-cyan)',                   textColor: 'var(--color-cyan)',                    bg: 'color-mix(in srgb, var(--color-cyan) 10%, transparent)',     border: 'color-mix(in srgb, var(--color-cyan) 40%, transparent)',
             halo: '0 0 0 3px color-mix(in srgb, var(--color-cyan) 18%, transparent)' },
  failed:  { dot: 'var(--status-failed)',                   textColor: 'var(--status-failed)',                    bg: 'color-mix(in srgb, var(--status-failed) 10%, transparent)',      border: 'color-mix(in srgb, var(--status-failed) 30%, transparent)' },
  skipped: { dot: 'var(--color-text-faint)',   textColor: 'var(--color-text-faint)',    bg: 'var(--color-bg-secondary)', border: 'var(--color-border)' },
}

/** Chips drawn before the rest collapse into one `+N` chip. */
export const MAX_PHASE_CHIPS = 8

interface PhasePipelineProps {
  phases: DerivedPhase[]
  className?: string
}

export default function PhasePipeline({ phases, className }: PhasePipelineProps) {
  if (phases.length === 0) return null
  const shown = phases.slice(0, MAX_PHASE_CHIPS)
  const hidden = phases.slice(MAX_PHASE_CHIPS)
  return (
    <div
      className={clsx('flex items-stretch overflow-hidden rounded-md border', className)}
      style={{ borderColor: 'var(--color-border)' }}
      role="list"
      aria-label="Release phase pipeline"
    >
      {shown.map((p, i) => {
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
            title={p.state === 'failed' && p.note ? `${p.label} — ${p.state}: ${p.note}` : `${p.label} — ${p.state}`}
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
      {hidden.length > 0 && (
        <div
          role="listitem"
          data-phase-more=""
          className="shrink-0 flex items-center px-2.5 py-1.5 text-[11.5px] border-l text-[var(--color-text-muted)] bg-[var(--color-bg-secondary)]"
          style={{ borderLeftColor: 'var(--color-border)' }}
          aria-label={`${hidden.length} more phase${hidden.length === 1 ? '' : 's'}`}
          title={hidden.map(p => `${p.label} — ${p.state}`).join(', ')}
        >
          +{hidden.length}
        </div>
      )}
    </div>
  )
}
