import { clsx } from 'clsx'

interface Props { status: string; className?: string }

const MAP: Record<string, string> = {
  PASSED:  'badge-passed',
  FAILED:  'badge-failed',
  BROKEN:  'badge-broken',
  SKIPPED: 'badge-skipped',
  FLAKY:   'badge-flaky',
  UNKNOWN: 'badge bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] border border-[var(--color-border-light)]',
  // run statuses
  IN_PROGRESS: 'badge bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)] border border-[var(--color-border-light)]',
  PASSED_RUN:  'badge-passed',
  STOPPED:     'badge bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] border border-[var(--color-border-light)]',
}

export default function StatusBadge({ status, className }: Props) {
  const cls = MAP[status?.toUpperCase()] ?? MAP.UNKNOWN
  return <span className={clsx(cls, className)}>{status}</span>
}
