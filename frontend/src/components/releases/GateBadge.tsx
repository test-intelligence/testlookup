/**
 * GateBadge — small pill rendering the release gate decision. Shows up in
 * the release-card identity row, the verdict band, and the right-rail
 * "shipping this week" cards. Colour follows --gate-go/--gate-conditional/
 * --gate-no-go; "Not evaluated" uses muted chrome.
 */
import { clsx } from 'clsx'
import type { GateDecision } from './types'

const STYLES: Record<GateDecision, { label: string; fg: string; bg: string; bd: string }> = {
  go:            { label: 'Go',             fg: 'var(--status-passed)', bg: 'color-mix(in srgb, var(--status-passed) 10%, transparent)', bd: 'color-mix(in srgb, var(--status-passed) 40%, transparent)' },
  conditional:   { label: 'Conditional Go', fg: 'var(--status-broken)', bg: 'color-mix(in srgb, var(--status-skipped) 10%, transparent)', bd: 'color-mix(in srgb, var(--status-skipped) 40%, transparent)' },
  no_go:         { label: 'No-Go',          fg: 'var(--status-failed)', bg: 'color-mix(in srgb, var(--status-failed) 10%, transparent)', bd: 'color-mix(in srgb, var(--status-failed) 40%, transparent)' },
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
