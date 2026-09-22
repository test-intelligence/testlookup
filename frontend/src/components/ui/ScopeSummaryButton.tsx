import { useEffect, useId, useRef, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { findReportFilterControl, type ReportFilterDimension } from '@/lib/scopeControls'

/**
 * A single-value header control for a MULTI-value scope (VIZ-303, a11y M5).
 *
 * The legacy release and suite pickers are native `<select>`s. With the
 * `viz_multi_filters` flag on, the selection can hold several values, and a
 * native `<select>` is a trap for it: focus it and press ArrowDown (or type a
 * letter) and the browser picks the next option at once — silently replacing
 * "2026.09, 2026.10" with one release, with no confirmation and nothing to
 * undo. So this control is a BUTTON that only summarises the selection
 * ("2 releases"); no keystroke on it changes anything.
 *
 * Activating it:
 *  - moves focus to the report filter bar's control for the same dimension
 *    (`[data-report-filter="release"|"suite"]`) when the page has one — the
 *    multi-select is where a multi-value scope is edited;
 *  - otherwise opens a small menu of single values. Picking one is an explicit
 *    click / Enter on a menu item; arrow keys only move focus in the menu.
 */

export interface ScopeSummaryOption {
  value: string
  label: string
}

export function ScopeSummaryButton({
  label,
  ariaLabel,
  title,
  disabled = false,
  dimension,
  options,
  selected,
  onPick,
  className = '',
}: {
  /** Visible summary: "All releases", "2026.09", "2 releases". */
  label: string
  /** What the control is ("Filter by release"). The accessible name is
   *  "<ariaLabel>: <label>", so it contains the visible text (WCAG 2.5.3). */
  ariaLabel: string
  title?: string
  disabled?: boolean
  dimension: ReportFilterDimension
  /** Single values offered by the fallback menu (first is usually "All"). */
  options: readonly ScopeSummaryOption[]
  /** Currently selected values (menu items are marked checked). */
  selected: readonly string[]
  /** Replace the selection with exactly this value (`''` clears). */
  onPick: (value: string) => void
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLUListElement>(null)
  const menuId = useId()

  const items = () => Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]') ?? [])

  useEffect(() => {
    if (!open) return
    // Focus the checked item (or the first) once the menu is in the DOM.
    const list = items()
    const checked = list.find((el) => el.getAttribute('aria-checked') === 'true')
    ;(checked ?? list[0])?.focus()
    const onDown = (event: MouseEvent) => {
      const target = event.target as Node
      if (menuRef.current?.contains(target) || triggerRef.current?.contains(target)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const close = (refocus: boolean) => {
    setOpen(false)
    if (refocus) triggerRef.current?.focus()
  }

  const onTriggerClick = () => {
    const control = findReportFilterControl(dimension)
    if (control) {
      control.focus()
      return
    }
    setOpen((v) => !v)
  }

  const onMenuKeyDown = (event: React.KeyboardEvent<HTMLUListElement>) => {
    const list = items()
    const at = list.indexOf(document.activeElement as HTMLButtonElement)
    const move = (to: number) => {
      event.preventDefault()
      list[(to + list.length) % list.length]?.focus()
    }
    if (event.key === 'ArrowDown') move(at + 1)
    else if (event.key === 'ArrowUp') move(at - 1)
    else if (event.key === 'Home') move(0)
    else if (event.key === 'End') move(list.length - 1)
    else if (event.key === 'Escape') {
      event.preventDefault()
      close(true)
    } else if (event.key === 'Tab') close(false)
  }

  return (
    <div className="relative inline-flex">
      <button
        ref={triggerRef}
        type="button"
        aria-label={`${ariaLabel}: ${label}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        title={title}
        disabled={disabled}
        data-scope-summary={dimension}
        onClick={onTriggerClick}
        className={`inline-flex items-center gap-1.5 bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text-secondary)] text-sm rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-[var(--color-ring)] disabled:opacity-50 disabled:cursor-not-allowed max-w-[240px] ${className}`}
      >
        <span className="truncate">{label}</span>
        <ChevronDown aria-hidden className="h-3.5 w-3.5 shrink-0" />
      </button>
      {open && (
        <ul
          ref={menuRef}
          id={menuId}
          role="menu"
          aria-label={ariaLabel}
          onKeyDown={onMenuKeyDown}
          className="absolute right-0 top-full z-50 mt-1 max-h-72 min-w-[12rem] overflow-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)] py-1 shadow-lg"
        >
          {options.map((option) => {
            const checked = option.value === '' ? selected.length === 0 : selected.length === 1 && selected[0] === option.value
            return (
              <li key={option.value || 'all-values-option'} role="none">
                <button
                  type="button"
                  role="menuitemradio"
                  aria-checked={checked}
                  tabIndex={-1}
                  onClick={() => {
                    onPick(option.value)
                    close(true)
                  }}
                  className="block w-full px-3 py-1.5 text-left text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-secondary)] focus:bg-[var(--color-bg-secondary)] focus:outline-none aria-checked:font-semibold aria-checked:text-[var(--color-text)]"
                >
                  {option.label}
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
