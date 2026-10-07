import { useEffect } from 'react'
import { clsx } from 'clsx'
import { snapToAllowed, useTimeWindowStore } from '@/store/timeWindowStore'

export const DEFAULT_WINDOW_OPTIONS = [1, 7, 14, 30, 90] as const

/** "24h" for one day, else "<n>d". */
export function windowLabel(days: number): string {
  return days === 1 ? '24h' : `${days}d`
}

/**
 * The one time-window control (UX redesign P0), bound to the global
 * time-window store so a window chosen on one page follows the reader to the
 * next. A page with its own option set passes `options`; the stored window is
 * snapped to the nearest one, and written back so the page and the store agree.
 */
export default function WindowPicker({
  options = DEFAULT_WINDOW_OPTIONS,
  className,
}: {
  options?: readonly number[]
  className?: string
}) {
  const stored = useTimeWindowStore((s) => s.days)
  const setDays = useTimeWindowStore((s) => s.setDays)
  const days = snapToAllowed(stored, options)
  useEffect(() => {
    if (days !== stored) setDays(days)
  }, [days, stored, setDays])
  return (
    <div role="radiogroup" aria-label="Time window" data-window-picker="" className={clsx('inline-flex items-center gap-1.5', className)}>
      {options.map((option) => {
        const active = option === days
        return (
          <button
            key={option}
            type="button"
            role="radio"
            aria-checked={active}
            data-window={option}
            onClick={() => setDays(option)}
            className="inline-flex items-center rounded-full border px-2.5 py-1 text-[12.5px] transition-colors"
            style={{
              background: active ? 'color-mix(in srgb, var(--color-accent) 14%, transparent)' : 'transparent',
              borderColor: active ? 'color-mix(in srgb, var(--color-accent) 30%, transparent)' : 'var(--color-border)',
              color: active ? 'var(--color-accent)' : 'var(--color-text-muted)',
            }}
          >
            {windowLabel(option)}
          </button>
        )
      })}
    </div>
  )
}
