/**
 * VerdictBand — the gate-coloured hero strip at the top of /releases. Names
 * the most consequential release this week + 1-3 items the user should
 * clear before it ships.
 *
 * Driven by ``DerivedRelease`` so it picks the "highlighted" release client-side:
 * prefer the in-progress release whose due-date is closest in the future
 * (the one that's actually about to ship), with the highlighted gate
 * decision driving the left accent + badge.
 */
import { AlertTriangle, AlertCircle, CheckCircle2 } from 'lucide-react'
import type { DerivedBlocker, DerivedRelease, GateDecision } from './types'
import GateBadge from './GateBadge'

const GATE_ACCENT: Record<GateDecision, { bar: string; glow: string; border: string }> = {
  go:            { bar: 'var(--gate-go)',           glow: 'linear-gradient(180deg, color-mix(in srgb, var(--status-passed) 5%, transparent), transparent 70%)', border: 'color-mix(in srgb, var(--status-passed) 30%, transparent)' },
  conditional:   { bar: 'var(--gate-conditional)',  glow: 'linear-gradient(180deg, color-mix(in srgb, var(--status-skipped) 5%, transparent), transparent 70%)', border: 'color-mix(in srgb, var(--status-skipped) 30%, transparent)' },
  no_go:         { bar: 'var(--gate-no-go)',        glow: 'linear-gradient(180deg, color-mix(in srgb, var(--status-failed) 5%, transparent), transparent 70%)', border: 'color-mix(in srgb, var(--status-failed) 30%, transparent)' },
  not_evaluated: { bar: 'var(--color-border-light)', glow: 'transparent', border: 'var(--color-border)' },
  cancelled:     { bar: 'var(--color-border-light)', glow: 'transparent', border: 'var(--color-border)' },
}

interface VerdictBandProps {
  highlighted: DerivedRelease | null
  /** All in-progress releases — used to count blockers across the project. */
  inProgressReleases: DerivedRelease[]
}

function ItemIcon({ tone }: { tone: DerivedBlocker['severity'] }) {
  if (tone === 'resolved') return <CheckCircle2 className="h-3 w-3 text-[var(--status-passed)]" />
  if (tone === 'warn')     return <AlertTriangle className="h-3 w-3 text-[var(--status-broken)]" />
  return <AlertCircle className="h-3 w-3 text-[var(--status-failed)]" />
}

export default function VerdictBand({ highlighted, inProgressReleases }: VerdictBandProps) {
  // No in-flight release → muted placeholder so the band always reserves
  // vertical space, but never lies about state.
  if (!highlighted) {
    return (
      <section
        className="rounded-xl border px-5 py-4"
        style={{ borderColor: 'var(--color-border)', background: 'var(--color-bg-card)' }}
      >
        <div className="text-[10.5px] font-semibold uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
          Release health · this week
        </div>
        <p className="mt-1 text-[14px] text-[var(--color-text-secondary)] m-0">
          No release in flight — create a new release or move a Planning release into progress.
        </p>
      </section>
    )
  }

  const style = GATE_ACCENT[highlighted.gate.decision]
  const totalBlockers = inProgressReleases.reduce((acc, r) => acc + r.blockers.length, 0)
  const headline = (() => {
    const verLabel = highlighted.version ? ` (${highlighted.version})` : ''
    const dueDate = highlighted.dueAt ? new Date(highlighted.dueAt) : null
    const dueLabel = dueDate
      ? dueDate.toLocaleDateString(undefined, { weekday: 'long' })
      : null
    if (highlighted.gate.decision === 'go') {
      return `${highlighted.name}${verLabel} is ready to ship`
    }
    if (highlighted.gate.decision === 'no_go') {
      return `${highlighted.name}${verLabel} is blocked${dueLabel ? ` — was due ${dueLabel}` : ''}`
    }
    if (highlighted.gate.decision === 'conditional') {
      return `${highlighted.name}${verLabel} is on track — ${highlighted.blockers.length} item${highlighted.blockers.length === 1 ? '' : 's'} to clear${dueLabel ? ` before ${dueLabel}` : ''}`
    }
    return `${highlighted.name}${verLabel} is in planning${dueLabel ? ` — target ${dueLabel}` : ''}`
  })()

  const lede = (() => {
    const c = highlighted.gate.composite
    if (highlighted.gate.decision === 'not_evaluated' || c == null) {
      return 'No gate evaluation yet — phase results will populate the composite, coverage, and flake numbers once tests start landing.'
    }
    return `Composite pass rate ${c.toFixed(1)}% ${c >= 95 ? 'meets' : 'is under'} the 95% gate. ${highlighted.blockers.length} blocker${highlighted.blockers.length === 1 ? '' : 's'} across this release.`
  })()

  return (
    <section
      className="relative rounded-xl border overflow-hidden"
      style={{
        borderColor: style.border,
        background: `${style.glow}, var(--color-bg-card)`,
      }}
    >
      <div className="absolute inset-y-0 left-0 w-[3px]" style={{ background: style.bar }} aria-hidden />
      <div className="p-5">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-[10.5px] font-semibold uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
            Release health · this week
          </span>
          {totalBlockers > 0 && (
            <span className="text-[10.5px] text-[var(--color-text-muted)]">
              · {totalBlockers} open blocker{totalBlockers === 1 ? '' : 's'} across in-progress releases
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-baseline gap-3 mb-1">
          <h2
            className="m-0 text-[var(--color-text)] font-bold"
            style={{ fontSize: 20, letterSpacing: '-0.015em' }}
          >
            {headline}
          </h2>
          <GateBadge decision={highlighted.gate.decision} />
        </div>
        <p
          className="m-0 text-[13px] text-[var(--color-text-secondary)] max-w-[50ch]"
          dangerouslySetInnerHTML={{ __html: lede }}
        />
        {highlighted.blockers.length > 0 && (
          <ul className="mt-2.5 space-y-1.5 m-0 p-0 list-none">
            {highlighted.blockers.slice(0, 3).map(b => (
              <li key={b.id} className="flex items-center gap-2 text-[12.5px]">
                <ItemIcon tone={b.severity} />
                <span className="text-[var(--color-text)]">{b.title}</span>
                {b.context && (
                  <span className="text-[var(--color-text-muted)]">· {b.context}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}
