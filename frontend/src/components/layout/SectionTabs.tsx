import { useLocation } from 'react-router-dom'
import RouteTabs from '@/components/ui/RouteTabs'
import { usePermissions } from '@/hooks/usePermissions'
import { owningNavItem } from './navConfig'

/**
 * The section's pages as tabs, above every page of a multi-page section (UX
 * redesign P1): on `/coverage` it reads Trends · Coverage · Explorer. Rendered
 * by `AppLayout` from `navConfig`, so no page carries it and a page added to a
 * section gets its tabs by being listed there. Nothing for a page whose item
 * has no tabs, or no item at all (`/search`, `/docs`, a personal page), or one
 * whose section this user is not shown.
 */
export default function SectionTabs() {
  const { pathname } = useLocation()
  const { canAccessManagement, canReadReleases } = usePermissions()
  const item = owningNavItem(pathname)
  if (!item?.tabs || (item.requires === 'management' && !canAccessManagement)) return null
  if (item.requires === 'qa_engineer' && !canReadReleases) return null
  // A tab whose page redirects this user is not offered (D-118: a QA engineer
  // reads Releases, not Gate policies); one tab left is no set of tabs.
  const tabs = canAccessManagement
    ? item.tabs
    : item.tabs.filter((tab) => !item.managementTabs?.includes(tab.to))
  if (tabs.length < item.tabs.length && tabs.length < 2) return null
  return (
    <div data-section-tabs={item.id} className="mb-4">
      <RouteTabs items={tabs} ariaLabel={`${item.label} pages`} />
    </div>
  )
}
