/**
 * GateBadge — small pill rendering the release gate decision. Shows up in
 * the release-card identity row, the verdict band, and the right-rail
 * "shipping this week" cards. Colour follows --gate-go/--gate-conditional/
 * --gate-no-go; "Not evaluated" uses muted chrome.
 */
import { clsx } from 'clsx'
import type { GateDecision } from './types'

const STYLES: Record<GateDecision, { label: string; fg: string; bg: string; bd: string }> = {
  go:            { label: 'Go',             fg: '#86efac', bg: 'rgba(34,197,94,0.10)', bd: 'rgba(34,197,94,0.40)' },
  conditional:   { label: 'Conditional Go', fg: '#fcd34d', bg: 'rgba(234,179,8,0.10)', bd: 'rgba(234,179,8,0.40)' },
  no_go:         { label: 'No-Go',          fg: '#fca5a5', bg: 'rgba(239,68,68,0.10)', bd: 'rgba(239,68,68,0.40)' },
  not_evaluated: { label: 'Not evaluated',  fg: 'var(--color-text-muted)', bg: 'var(--color-bg-secondary)', bd: 'var(--color-border)' },
  cancelled:     { label: 'Cancelled',      fg: 'var(--color-text-muted)', bg: 'var(--color-bg-secondary)', bd: 'var(--color-border)' },
}

interface GateBadgeProps {
  decision: GateDecision
  /** Optional one-line explanation for the aria-label (e.g. "flake rate above cap"). */
  reason?: string
  className?: string
}

export default function GateBadge({ decision, reason, className }: GateBadgeProps) {
  const s = STYLES[decision]
  const aria = reason ? `${s.label} — ${reason}` : s.label
  return (
    <span
      className={clsx('inline-flex items-center text-[11px] font-semibold uppercase tracking-[0.05em] px-2 py-0.5 rounded-full border', className)}
      style={{ color: s.fg, background: s.bg, borderColor: s.bd }}
      aria-label={aria}
    >
      {s.label}
    </span>
  )
}
