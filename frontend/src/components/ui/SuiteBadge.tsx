import { clsx } from 'clsx'
import { Layers } from 'lucide-react'
import { Link } from 'react-router-dom'

interface Props {
  primary?: string | null
  all?: string[] | null
  className?: string
  /** When true, render only the suite text (no icon, no chip background) — for inline use in headers. */
  inline?: boolean
  /** Build a destination route for a suite name. When set, the badge becomes a Link. */
  linkTo?: (suiteName: string) => string
}

/**
 * Renders the suite attribution for a TestRun.
 * - 0 suites → em dash
 * - 1 suite  → plain label
 * - N suites → "<primary> +N" with the full list in the title tooltip
 */
export default function SuiteBadge({ primary, all, className, inline = false, linkTo }: Props) {
  const list = (all ?? []).filter(Boolean)
  const label = primary ?? (list[0] ?? null)
  const extra = label ? Math.max(0, list.filter(s => s !== label).length) : 0

  if (!label) {
    return <span className={clsx('text-[var(--color-text-muted)]', className)}>—</span>
  }

  const tooltip = list.length > 1 ? list.join(', ') : label

  const chipClass = clsx(
    inline
      ? 'inline-flex items-center gap-1 text-[var(--color-text)]'
      : 'inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium bg-[var(--color-bg-secondary)] text-[var(--color-text)] border border-[var(--color-border-light)]',
    linkTo && 'hover:text-[var(--color-accent)] hover:underline',
    className,
  )

  const inner = (
    <>
      {!inline && <Layers className="w-3 h-3 text-[var(--color-text-muted)]" />}
      <span className="truncate max-w-[200px]">{label}</span>
      {extra > 0 && (
        <span className="text-[var(--color-text-muted)] font-normal">+{extra}</span>
      )}
    </>
  )

  if (linkTo) {
    // stopPropagation so clicking the badge inside a clickable row doesn't
    // also trigger the row's onClick (run detail navigation, etc.).
    return (
      <Link
        to={linkTo(label)}
        className={chipClass}
        title={tooltip}
        onClick={e => e.stopPropagation()}
      >
        {inner}
      </Link>
    )
  }

  return (
    <span className={chipClass} title={tooltip}>
      {inner}
    </span>
  )
}
