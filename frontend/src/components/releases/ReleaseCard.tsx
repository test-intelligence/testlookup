/**
 * ReleaseCard — the always-visible card per release. Four state variants:
 *
 *   in_progress: identity + gate cluster + 4-segment pass/fail bar + phase pipeline + blocker rows
 *   planning:    identity + phase pipeline (all idle) + dashed empty bar with 2 CTAs
 *   released:    identity + gate snapshot (no progress bar, no blockers)
 *   cancelled:   identity only, muted
 *
 * Cards are buttons so the whole surface is clickable — selecting one opens
 * the inline detail panel (the existing ``ReleaseDetailPanel``).
 */
import { clsx } from 'clsx'
import { Clock, CheckCircle2, Sparkles, Copy, BadgeCheck, ChevronRight } from 'lucide-react'
import toast from 'react-hot-toast'
import type { DerivedRelease, GateDecision } from './types'
import GateBadge from './GateBadge'
import PhasePipeline from './PhasePipeline'
import BlockerRow from './BlockerRow'

const ICON_TILE: Record<GateDecision, { bg: string; fg: string; icon: typeof Clock }> = {
  go:            { bg: 'rgba(34,197,94,0.14)',  fg: '#22c55e', icon: CheckCircle2 },
  conditional:   { bg: 'rgba(234,179,8,0.14)',  fg: '#eab308', icon: BadgeCheck },
  no_go:         { bg: 'rgba(239,68,68,0.14)',  fg: '#ef4444', icon: BadgeCheck },
  not_evaluated: { bg: 'var(--color-accent-muted)', fg: 'var(--color-accent)', icon: Clock },
  cancelled:     { bg: 'var(--color-bg-secondary)', fg: 'var(--color-text-muted)', icon: Clock },
}

interface GateCellProps {
  label: string
  value: string
  sub: string
  /** Drives the value colour — defaults to neutral. */
  tone?: 'good' | 'warn' | 'bad' | 'neutral'
}
function GateCell({ label, value, sub, tone = 'neutral' }: GateCellProps) {
  const color =
    tone === 'good' ? '#86efac'
    : tone === 'warn' ? '#fcd34d'
    : tone === 'bad'  ? '#fca5a5'
    : 'var(--color-text)'
  return (
    <div className="text-right">
      <div className="text-[9.5px] uppercase tracking-[0.08em] text-[var(--color-text-muted)]">{label}</div>
      <div className="text-[16px] tabular-nums font-semibold leading-none mt-1" style={{ color }}>{value}</div>
      <div className="text-[10.5px] text-[var(--color-text-faint)] mt-0.5">{sub}</div>
    </div>
  )
}

function PassFailBar({ totals }: { totals: NonNullable<DerivedRelease['totals']> }) {
  const total = Math.max(1, totals.passed + totals.failed + totals.flaky + totals.skipped)
  const segs: { color: string; flex: number; label: string }[] = [
    { color: '#22c55e', flex: totals.passed,  label: 'passed' },
    { color: '#ef4444', flex: totals.failed,  label: 'failed' },
    { color: '#c084fc', flex: totals.flaky,   label: 'flaky' },
    { color: '#9ca3af', flex: totals.skipped, label: 'skipped' },
  ]
  return (
    <div className="flex items-center gap-3">
      <div
        className="flex flex-1 h-1.5 rounded-full overflow-hidden border"
        style={{ borderColor: 'var(--color-border)', background: 'var(--color-bg-secondary)' }}
        aria-hidden
      >
        {segs.map(s => s.flex > 0 && (
          <span key={s.label} style={{ flex: s.flex, background: s.color }} />
        ))}
      </div>
      <div className="flex items-center gap-2 text-[11px] text-[var(--color-text-muted)] tabular-nums shrink-0">
        <Dot color="#22c55e" /> {totals.passed}
        <Dot color="#ef4444" /> {totals.failed}
        <Dot color="#c084fc" /> {totals.flaky}
        <Dot color="#9ca3af" /> {totals.skipped}
        <span className="text-[var(--color-text-faint)]">/ {total}</span>
      </div>
    </div>
  )
}
function Dot({ color }: { color: string }) {
  return <span aria-hidden className="inline-block w-1.5 h-1.5 rounded-full" style={{ background: color }} />
}

function PlanningCallout({ releaseName }: { releaseName: string }) {
  return (
    <div
      className="rounded-md border border-dashed p-3 flex items-center justify-between gap-3"
      style={{ borderColor: 'var(--color-border)' }}
    >
      <div className="min-w-0">
        <div className="text-[12.5px] font-medium text-[var(--color-text)]">No phases scoped yet</div>
        <div className="text-[11.5px] text-[var(--color-text-muted)]">
          Bootstrap {releaseName} from a previous release or a PRD upload.
        </div>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <button
          type="button"
          onClick={() => toast('Clone from previous release — coming in next iteration', { icon: '📋' })}
          className="inline-flex items-center gap-1 text-[12px] px-2.5 py-1 rounded-md border border-[var(--color-border)] hover:border-[var(--color-border-light)] text-[var(--color-text-secondary)] hover:text-[var(--color-text)]"
        >
          <Copy className="h-3 w-3" /> Clone from …
        </button>
        <button
          type="button"
          onClick={() => toast('Generate from PRD — coming in next iteration', { icon: '✨' })}
          className="inline-flex items-center gap-1 text-[12px] px-2.5 py-1 rounded-md border text-[var(--color-accent)] hover:underline"
          style={{ background: 'var(--color-accent-muted)', borderColor: 'rgba(68,147,248,0.40)' }}
        >
          <Sparkles className="h-3 w-3" /> Generate from PRD
        </button>
      </div>
    </div>
  )
}

interface ReleaseCardProps {
  release: DerivedRelease
  /** When true, the body collapses to the identity row only. */
  collapsed?: boolean
  onClick?: () => void
}

export default function ReleaseCard({ release, collapsed = false, onClick }: ReleaseCardProps) {
  const tile = ICON_TILE[release.gate.decision]
  const Icon = tile.icon
  const muted = release.stage === 'cancelled'
  const isReleased = release.stage === 'released'
  const isPlanning = release.stage === 'planning'

  const composite = release.gate.composite
  const coverage  = release.gate.coverage
  const flakePct  = release.gate.flakePct
  const compTone = composite == null ? 'neutral' : composite >= 95 ? 'good' : composite >= 90 ? 'warn' : 'bad'
  const covTone  = coverage  == null ? 'neutral' : coverage  >= 80 ? 'good' : coverage  >= 70 ? 'warn' : 'bad'
  const flakeTone = flakePct == null ? 'neutral' : flakePct <= 1.5 ? 'good' : flakePct <= 3.0 ? 'warn' : 'bad'

  const dueLabel = release.dueAt
    ? new Date(release.dueAt).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
    : null
  const overdue = release.dueAt
    ? new Date(release.dueAt).getTime() < Date.now() && release.stage === 'in_progress'
    : false

  return (
    <article
      onClick={onClick}
      className={clsx(
        'rounded-xl border bg-[var(--color-bg-card)] overflow-hidden transition-colors',
        onClick && 'cursor-pointer hover:border-[var(--color-border-light)]',
        muted && 'opacity-60',
      )}
      style={{ borderColor: 'var(--color-border)' }}
      aria-label={`${release.name}${release.version ? ' ' + release.version : ''} — ${release.gate.decision}`}
    >
      {/* Identity + gate cluster */}
      <header className="grid grid-cols-[minmax(0,1fr)_auto] gap-4 items-start px-4 pt-3.5 pb-3">
        <div className="min-w-0 flex items-start gap-3">
          <div
            className="w-8 h-8 rounded-md flex items-center justify-center shrink-0"
            style={{ background: tile.bg, color: tile.fg }}
          >
            <Icon className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h3 className="text-[15px] font-semibold text-[var(--color-text)] m-0 truncate max-w-[280px]">{release.name}</h3>
              {release.version && (
                <code className="font-mono text-[11.5px] bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-1.5 py-px rounded-sm">{release.version}</code>
              )}
              <span className="text-[10px] uppercase tracking-[0.08em] text-[var(--color-text-muted)] px-1.5 py-0.5 rounded-sm border border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
                {release.stage.replace('_', ' ')}
              </span>
            </div>
            <div className="mt-1 flex items-center gap-2 text-[11.5px] text-[var(--color-text-muted)] flex-wrap">
              <span
                className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-sky-700 text-sky-100 text-[9px] font-semibold"
                title={release.ownerName}
              >{release.ownerInitials}</span>
              <span>{release.ownerName}</span>
              {dueLabel && (
                <>
                  <span aria-hidden>·</span>
                  <span className={clsx(overdue && 'text-red-300 font-medium')}>
                    {overdue ? `Overdue (${dueLabel})` : `Due ${dueLabel}`}
                  </span>
                </>
              )}
              {isReleased && release.source.released_at && (
                <>
                  <span aria-hidden>·</span>
                  <span>Released {new Date(release.source.released_at).toLocaleDateString()}</span>
                </>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-end gap-4 shrink-0">
          {!isPlanning && release.gate.decision !== 'not_evaluated' && (
            <>
              <GateCell
                label="Composite"
                value={composite != null ? `${composite.toFixed(1)}%` : '—'}
                sub="need ≥ 95%"
                tone={compTone}
              />
              <GateCell
                label="Coverage"
                value={coverage != null ? `${coverage.toFixed(1)}%` : '—'}
                sub="need ≥ 80%"
                tone={covTone}
              />
              <GateCell
                label="Flake"
                value={flakePct != null ? `${flakePct.toFixed(1)}%` : '—'}
                sub="cap 1.5%"
                tone={flakeTone}
              />
            </>
          )}
          <GateBadge decision={release.gate.decision} />
        </div>
      </header>

      {/* Body — body sections per state */}
      {!collapsed && !muted && (
        <div className="px-4 pb-4 space-y-3">
          {/* Progress bar — only when we have real totals (in-progress / released) */}
          {!isPlanning && release.totals && release.stage !== 'cancelled' && (
            <PassFailBar totals={release.totals} />
          )}

          {/* Phase pipeline — every non-cancelled state shows the 6 chips. */}
          {!muted && <PhasePipeline phases={release.phases} />}

          {/* Planning empty state */}
          {isPlanning && release.phases.every(p => p.state === 'idle') && (
            <PlanningCallout releaseName={release.name} />
          )}

          {/* Blocker rows (in-progress only) */}
          {!isReleased && !isPlanning && release.blockers.length > 0 && (
            <div className="space-y-1.5">
              {release.blockers.slice(0, 3).map(b => <BlockerRow key={b.id} blocker={b} />)}
              {release.blockers.length > 3 && (
                <div className="text-[11.5px] text-[var(--color-text-muted)] flex items-center gap-1">
                  <ChevronRight className="h-3 w-3" />
                  +{release.blockers.length - 3} more · open release for full list
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </article>
  )
}
