import { clsx } from 'clsx'

export interface ChipOption<T extends string> {
  id: T
  label: string
  /** A count after the label (`0` is shown; `undefined` is not). */
  count?: number
  /** Hover text for the option. */
  title?: string
}

/**
 * A one-row filter of mutually exclusive options, drawn as the pill chips the
 * time-window control uses (`WindowPicker`), with an optional visible label
 * before it. A radio group, not a tab bar: it filters the list under it, it
 * does not switch to another section of the page (UX redesign P4, Flaky tests
 * and Inbox toolbars).
 */
export default function ChipFilter<T extends string>({
  label,
  ariaLabel,
  options,
  value,
  onChange,
  optionClassName,
  className,
}: {
  /** Shown before the chips (and their accessible name unless `ariaLabel`). */
  label?: string
  ariaLabel?: string
  options: readonly ChipOption<T>[]
  value: T
  onChange: (next: T) => void
  optionClassName?: string
  className?: string
}) {
  return (
    <div className={clsx('inline-flex items-center gap-2', className)}>
      {label && (
        <span
          aria-hidden={ariaLabel ? undefined : true}
          className="text-[10.5px] font-medium uppercase text-[var(--color-text-muted)]"
          style={{ letterSpacing: 'var(--tracking-wider)' }}
        >
          {label}
        </span>
      )}
      <div role="radiogroup" aria-label={ariaLabel ?? label} className="flex flex-wrap items-center gap-1.5">
        {options.map((option) => {
          const active = option.id === value
          return (
            <button
              key={option.id}
              type="button"
              role="radio"
              aria-checked={active}
              data-chip-option={option.id}
              title={option.title}
              onClick={() => onChange(option.id)}
              className={clsx(
                'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12.5px] transition-colors',
                optionClassName,
              )}
              style={{
                background: active ? 'color-mix(in srgb, var(--color-accent) 14%, transparent)' : 'transparent',
                borderColor: active ? 'color-mix(in srgb, var(--color-accent) 30%, transparent)' : 'var(--color-border)',
                // The selected chip's text is the body colour, not the accent:
                // accent on its own 14% tint measured 4.29:1 at 12.5 px (axe,
                // dark theme), under the 4.5:1 AA floor. Tint and border mark it.
                color: active ? 'var(--color-text)' : 'var(--color-text-muted)',
              }}
            >
              {option.label}
              {/* A space for the accessible name ("AI reports 3", not "AI reports3"); a flex container drops it from the layout. */}
              {option.count !== undefined && ' '}
              {option.count !== undefined && (
                <span className="tabular-nums text-[11px] opacity-80" data-chip-count="">
                  {option.count}
                </span>
              )}
            </button>
          )
        })}
      </div>
    </div>
  )
}
