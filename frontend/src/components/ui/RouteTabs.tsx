import { Link, matchPath, useLocation } from 'react-router-dom'
import { TabCount, TAB_BAR_CLASS, tabClass } from './Tabs'

export interface RouteTabItem {
  /** Where the tab goes. */
  to: string
  label: string
  count?: number
  /**
   * Route patterns that also make this tab active (e.g. `/runs/:runId` under
   * "History"). `to` itself always matches, exactly or as a prefix of a
   * deeper path.
   */
  match?: readonly string[]
}

/** Whether `pathname` belongs to `item`: `to` or a path under it, or one of `match`. */
export function routeTabActive(item: RouteTabItem, pathname: string): boolean {
  const base = item.to.split(/[?#]/)[0]
  if (pathname === base || pathname.startsWith(`${base}/`)) return true
  return (item.match ?? []).some((pattern) => matchPath({ path: pattern, end: false }, pathname) !== null)
}

/**
 * Section tabs (UX redesign P0): tabs that are links to routes, so a section's
 * pages read as one place without being merged into one page. The first item
 * whose route matches is active; when two match (a tab's `to` is a prefix of
 * another's), the longest `to` wins.
 */
export default function RouteTabs({ items, ariaLabel }: { items: readonly RouteTabItem[]; ariaLabel: string }) {
  const { pathname } = useLocation()
  const matching = items.filter((item) => routeTabActive(item, pathname))
  const active = matching.sort((a, b) => b.to.length - a.to.length)[0] ?? null
  return (
    <nav aria-label={ariaLabel} data-route-tabs="" className={TAB_BAR_CLASS}>
      {items.map((item) => {
        const on = item === active
        return (
          <Link
            key={item.to}
            to={item.to}
            aria-current={on ? 'page' : undefined}
            data-route-tab={item.to}
            className={tabClass(on)}
          >
            {item.label}
            {item.count !== undefined && <TabCount value={item.count} active={on} />}
          </Link>
        )
      })}
    </nav>
  )
}
