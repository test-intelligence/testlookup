import { clsx } from 'clsx'
import { statusKey, statusLabel } from './statusVocabulary'

interface Props { status: string; className?: string }

const NEUTRAL_MUTED = 'badge bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] border border-[var(--color-border-light)]'
const NEUTRAL_SECONDARY = 'badge bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)] border border-[var(--color-border-light)]'

/** The badge class per `statusKey`; the result classes read the `-ink` text token (index.css). */
const CLASS_BY_KEY: Record<string, string> = {
  passed: 'badge-passed',
  passed_run: 'badge-passed',
  failed: 'badge-failed',
  broken: 'badge-broken',
  skipped: 'badge-skipped',
  flaky: 'badge-flaky',
  in_progress: NEUTRAL_SECONDARY,
}

/**
 * A result / run status as a chip (W29a UIX-03): the vocabulary's sentence-case
 * word ("Failed", never the raw "FAILED"), the status's tint with its legible
 * ink. Stopped, unknown and any other value: the muted neutral.
 */
export default function StatusBadge({ status, className }: Props) {
  const key = statusKey(status)
  const cls = Object.prototype.hasOwnProperty.call(CLASS_BY_KEY, key) ? CLASS_BY_KEY[key] : NEUTRAL_MUTED
  return (
    <span data-status={key} className={clsx(cls, className)}>
      {statusLabel(status)}
    </span>
  )
}
