/**
 * Where-am-I trail for drill-down pages (VIZ-109; used by VIZ-602/604).
 *
 * The WAI-ARIA breadcrumb pattern: a `<nav aria-label="Breadcrumb">` around an
 * ordered list, the current page last with `aria-current="page"` (and not a
 * link — it goes nowhere). Ancestors are real `<Link>`s, so Tab/Enter work
 * and middle-click opens a tab. Separators are `aria-hidden`.
 *
 * Long trails must not push the page sideways: MIDDLE items truncate with an
 * ellipsis (full text in `title`), while the first (the root the user knows)
 * and the last (where they are) keep as much room as they can. Labels are
 * text, never markup.
 */
import { ChevronRight } from 'lucide-react'
import { Link } from 'react-router-dom'

export interface BreadcrumbItem {
  label: string
  /** Omit for the current page, or for a level that has no page of its own. */
  to?: string
}

export interface BreadcrumbsProps {
  items: BreadcrumbItem[]
  className?: string
}

const LINK =
  'rounded-sm text-[var(--color-text-secondary)] underline-offset-2 hover:text-[var(--color-text)] hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-ring)]'

export default function Breadcrumbs({ items, className = '' }: BreadcrumbsProps) {
  if (items.length === 0) return null
  const lastIndex = items.length - 1

  return (
    <nav aria-label="Breadcrumb" className={`min-w-0 text-sm ${className}`}>
      {/* leading-6: every link is at least 24 px tall (WCAG 2.5.8 target size). */}
      <ol className="flex min-w-0 flex-wrap items-center gap-x-1 gap-y-1 leading-6">
        {items.map((item, index) => {
          const isLast = index === lastIndex
          const isMiddle = index > 0 && !isLast
          // Middle levels give up their width first; the ends keep theirs.
          const width = isMiddle ? 'max-w-[10rem] sm:max-w-[14rem]' : 'max-w-full'
          return (
            <li
              key={`${index}-${item.label}`}
              className={`flex min-w-0 items-center gap-1 ${isMiddle ? 'shrink' : ''}`}
            >
              {index > 0 && (
                <ChevronRight aria-hidden="true" className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
              )}
              {isLast ? (
                <span
                  aria-current="page"
                  title={item.label}
                  className={`block truncate font-medium text-[var(--color-text)] ${width}`}
                >
                  {item.label}
                </span>
              ) : item.to ? (
                <Link to={item.to} title={item.label} className={`block truncate ${width} ${LINK}`}>
                  {item.label}
                </Link>
              ) : (
                <span title={item.label} className={`block truncate text-[var(--color-text-secondary)] ${width}`}>
                  {item.label}
                </span>
              )}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}
