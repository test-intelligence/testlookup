import { clsx } from 'clsx'
import { Layers } from 'lucide-react'

interface Props {
  primary?: string | null
  all?: string[] | null
  className?: string
  /** When true, render only the suite text (no icon, no chip background) — for inline use in headers. */
  inline?: boolean
}

/**
 * Renders the suite attribution for a TestRun.
 * - 0 suites → em dash
 * - 1 suite  → plain label
 * - N suites → "<primary> +N" with the full list in the title tooltip
 */
export default function SuiteBadge({ primary, all, className, inline = false }: Props) {
  const list = (all ?? []).filter(Boolean)
  const label = primary ?? (list[0] ?? null)
  const extra = label ? Math.max(0, list.filter(s => s !== label).length) : 0

  if (!label) {
    return <span className={clsx('text-[var(--color-text-muted)]', className)}>—</span>
  }

  const tooltip = list.length > 1 ? list.join(', ') : label

  const chip = (
    <span
      className={clsx(
        inline
          ? 'inline-flex items-center gap-1 text-[var(--color-text)]'
          : 'inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium bg-[var(--color-bg-secondary)] text-[var(--color-text)] border border-[var(--color-border-light)]',
        className,
      )}
      title={tooltip}
    >
      {!inline && <Layers className="w-3 h-3 text-[var(--color-text-muted)]" />}
      <span className="truncate max-w-[200px]">{label}</span>
      {extra > 0 && (
        <span className="text-[var(--color-text-muted)] font-normal">+{extra}</span>
      )}
    </span>
  )

  return chip
}
