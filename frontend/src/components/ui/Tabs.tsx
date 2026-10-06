import type { ReactNode } from 'react'
import { clsx } from 'clsx'

export interface TabItem<T extends string = string> {
  id: T
  label: ReactNode
  /** A count beside the label (`0` is shown; `undefined` is not). */
  count?: number
  /** A short marker after the label, e.g. a "New" pill or a status dot. */
  badge?: ReactNode
}

/** Classes shared with `RouteTabs`, so section tabs and in-page tabs look the same. */
export const TAB_BAR_CLASS = 'flex items-end gap-1 border-b border-[var(--color-border)]'

export function tabClass(active: boolean): string {
  return clsx(
    '-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-2 text-[13px] font-medium transition-colors',
    active
      ? 'border-[var(--color-accent)] text-[var(--color-text)]'
      : 'border-transparent text-[var(--color-text-muted)] hover:text-[var(--color-text)]',
  )
}

export function TabCount({ value, active }: { value: number; active: boolean }) {
  return (
    <span
      data-tab-count=""
      className={clsx(
        'rounded px-1.5 py-px text-[10.5px] tabular-nums',
        active ? 'bg-[var(--color-accent-bg-soft)] text-[var(--color-text)]' : 'bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)]',
      )}
    >
      {value}
    </span>
  )
}

/**
 * The one in-page tab bar (UX redesign P0). Controlled: pair it with
 * `useTabParam` to keep the selection in `?tab=`. For tabs that are links to
 * other routes (a section's pages), use `RouteTabs`.
 */
export default function Tabs<T extends string>({
  items,
  value,
  onChange,
  ariaLabel,
  className,
}: {
  items: readonly TabItem<T>[]
  value: T
  onChange: (next: T) => void
  ariaLabel: string
  className?: string
}) {
  return (
    <div role="tablist" aria-label={ariaLabel} data-tabs="" className={clsx(TAB_BAR_CLASS, className)}>
      {items.map((item) => {
        const active = item.id === value
        return (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={active}
            data-tab={item.id}
            onClick={() => onChange(item.id)}
            className={tabClass(active)}
          >
            {item.label}
            {item.count !== undefined && <TabCount value={item.count} active={active} />}
            {item.badge}
          </button>
        )
      })}
    </div>
  )
}
