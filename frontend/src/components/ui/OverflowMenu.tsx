import { useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { MoreHorizontal } from 'lucide-react'
import { clsx } from 'clsx'
import { HeaderPopover } from './HeaderPopover'

export interface OverflowItem {
  label: string
  icon?: ReactNode
  /** An action. Exactly one of `onClick` / `href` is used; `href` wins. */
  onClick?: () => void
  /** An in-app route. */
  href?: string
  /** A destructive action, drawn in the failure hue. */
  danger?: boolean
  disabled?: boolean
}

const ITEM =
  'flex w-full items-center gap-2 rounded px-2.5 py-1.5 text-left text-[13px] hover:bg-[var(--color-bg-hover)] disabled:cursor-not-allowed disabled:opacity-50'

/**
 * The page header's **⋯** menu (UX redesign P0): every header action beyond
 * the one primary and one secondary button (Export, Saved views, PDF, Share,
 * …). Built on `HeaderPopover`, so it renders above page content and closes on
 * an outside click; picking an item closes it.
 */
export default function OverflowMenu({ items, label = 'More actions' }: { items: readonly OverflowItem[]; label?: string }) {
  const [open, setOpen] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  if (items.length === 0) return null
  const close = () => setOpen(false)
  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        data-overflow-trigger=""
        onClick={() => setOpen((was) => !was)}
        className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-[var(--color-border)] text-[var(--color-text-muted)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text)]"
      >
        <MoreHorizontal aria-hidden="true" className="h-4 w-4" />
      </button>
      <HeaderPopover anchorRef={triggerRef} open={open} onClose={close} width={220} ariaLabel={label}>
        <div className="p-1">
          {items.map((item) => {
            const tone = item.danger ? 'text-[var(--status-failed)]' : 'text-[var(--color-text)]'
            if (item.href && !item.disabled) {
              return (
                <Link key={item.label} role="menuitem" to={item.href} onClick={close} className={clsx(ITEM, tone)}>
                  {item.icon}
                  {item.label}
                </Link>
              )
            }
            return (
              <button
                key={item.label}
                type="button"
                role="menuitem"
                disabled={item.disabled}
                onClick={() => {
                  close()
                  item.onClick?.()
                }}
                className={clsx(ITEM, tone)}
              >
                {item.icon}
                {item.label}
              </button>
            )
          })}
        </div>
      </HeaderPopover>
    </>
  )
}
