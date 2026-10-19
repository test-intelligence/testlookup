/**
 * GateBadge — small pill rendering the release gate decision. Shows up in
 * the release-card identity row, the verdict band, and the right-rail
 * "shipping this week" cards. The three decided states take the one decision
 * vocabulary (W29a UIX-03): its words (Go / Conditional / No-Go, sentence
 * case, never `uppercase`), its tint and border, and its legible `-ink` text;
 * Conditional is the warning hue, never skipped-gold. "Not evaluated" (a
 * recorded NOT_EVALUATED verdict) and "Not evaluated yet" (no verdict was
 * ever recorded) use muted chrome.
 */
import { clsx } from 'clsx'
import { DECISION_LABEL, DECISION_TOKEN, type GateDecision as Decided } from '@/components/ui/statusVocabulary'
import type { GateDecision } from './types'

const decided = (key: Decided) => ({
  label: DECISION_LABEL[key],
  fg: DECISION_TOKEN[key].ink,
  bg: DECISION_TOKEN[key].tint,
  bd: DECISION_TOKEN[key].border,
})

const STYLES: Record<GateDecision, { label: string; fg: string; bg: string; bd: string }> = {
  go:            decided('go'),
  conditional:   decided('conditional'),
  no_go:         decided('no_go'),
  not_evaluated: { label: 'Not evaluated',  fg: 'var(--color-text-muted)', bg: 'var(--color-bg-secondary)', bd: 'var(--color-border)' },
  never_evaluated: { label: 'Not evaluated yet', fg: 'var(--color-text-muted)', bg: 'var(--color-bg-secondary)', bd: 'var(--color-border)' },
  cancelled:     { label: 'Cancelled',      fg: 'var(--color-text-muted)', bg: 'var(--color-bg-secondary)', bd: 'var(--color-border)' },
}

interface GateBadgeProps {
  decision: GateDecision
  /** Optional one-line explanation for the aria-label (e.g. "3 of 40 tests carry evidence; 5 required"). */
  reason?: string
  className?: string
}

export default function GateBadge({ decision, reason, className }: GateBadgeProps) {
  const s = STYLES[decision]
  const aria = reason ? `${s.label} — ${reason}` : s.label
  return (
    <span
      className={clsx('inline-flex items-center text-[11px] font-semibold px-2 py-0.5 rounded-full border', className)}
      style={{ color: s.fg, background: s.bg, borderColor: s.bd }}
      aria-label={aria}
      data-gate-decision={decision}
    >
      {s.label}
    </span>
  )
}
