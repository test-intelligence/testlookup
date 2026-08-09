/**
 * BlockerRow — single horizontal row inside a release card highlighting an
 * open blocker or a resolved one. Three severities (warn / red / resolved).
 */
import { clsx } from 'clsx'
import { AlertTriangle, CheckCircle2, ArrowRight, ExternalLink } from 'lucide-react'
import type { DerivedBlocker } from './types'

const SEV_STYLE: Record<DerivedBlocker['severity'], {
  icon: typeof AlertTriangle
  iconClass: string
  border: string
  bg: string
}> = {
  warn: {
    icon: AlertTriangle,
    iconClass: 'text-[var(--status-broken)]',
    border: 'color-mix(in srgb, var(--status-skipped) 22%, transparent)',
    bg: 'linear-gradient(180deg, color-mix(in srgb, var(--status-skipped) 6%, transparent), transparent 70%)',
  },
  red: {
    icon: AlertTriangle,
    iconClass: 'text-[var(--status-failed)]',
    border: 'color-mix(in srgb, var(--status-failed) 28%, transparent)',
    bg: 'linear-gradient(180deg, color-mix(in srgb, var(--status-failed) 6%, transparent), transparent 70%)',
  },
  resolved: {
    icon: CheckCircle2,
    iconClass: 'text-[var(--status-passed)]',
    border: 'color-mix(in srgb, var(--status-passed) 28%, transparent)',
    bg: 'linear-gradient(180deg, color-mix(in srgb, var(--status-passed) 6%, transparent), transparent 70%)',
  },
}

interface BlockerRowProps { blocker: DerivedBlocker }

export default function BlockerRow({ blocker }: BlockerRowProps) {
  const s = SEV_STYLE[blocker.severity]
  const Icon = s.icon
  return (
    <div
      className={clsx(
        'flex items-center gap-2 px-3 py-2 rounded-md border text-[12.5px]',
      )}
      style={{ borderColor: s.border, background: `${s.bg}, var(--color-bg)` }}
    >
      <Icon className={clsx('h-3.5 w-3.5 shrink-0', s.iconClass)} />
      <div className="flex-1 min-w-0">
        <div className="text-[var(--color-text)] truncate">
          <span className="font-medium">{blocker.title}</span>
          {blocker.context && (
            <>
              <span className="text-[var(--color-text-muted)]"> · </span>
              <span className="text-[var(--color-text-muted)]">{blocker.context}</span>
            </>
          )}
          {blocker.linkedTicketId && (
            <>
              <span className="text-[var(--color-text-muted)]"> · </span>
              <code className="font-mono text-[11.5px] px-1 rounded bg-[var(--color-bg-secondary)] border border-[var(--color-border)]">
                {blocker.linkedTicketId}
              </code>
            </>
          )}
          {blocker.ownerInitials && (
            <>
              <span className="text-[var(--color-text-muted)]"> · </span>
              <span className="text-[var(--color-text-muted)]">@{blocker.ownerInitials}</span>
            </>
          )}
        </div>
      </div>
      <a
        href={blocker.action.href}
        className="inline-flex items-center gap-1 text-[12px] text-[var(--color-accent)] hover:underline shrink-0"
      >
        {blocker.action.label.includes('→') ? null : <ExternalLink className="h-3 w-3" />}
        {blocker.action.label}
        {blocker.action.label.includes('→') ? null : <ArrowRight className="h-3 w-3" />}
      </a>
    </div>
  )
}
