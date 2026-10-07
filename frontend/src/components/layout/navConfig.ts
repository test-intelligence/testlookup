/**
 * The sidebar, as data (UX redesign P1, `docs/ux-review-2026-10-06/02-design-spec.md` §1).
 *
 * One list is the single source for three things:
 *  - the sidebar's items, in order, under their section labels;
 *  - which item is active: the item that OWNS the current path (the longest
 *    owned prefix wins, so `/coverage/suite` is Suites, not Trends);
 *  - the section tabs a page shows (`SectionTabs`): the owning item's `tabs`,
 *    links to the routes that already exist, so related pages read as one
 *    place without being merged.
 *
 * 11 items for a viewer, 13 for a QA lead or admin (Releases and Admin). The
 * routes that left the nav keep working: Documentation and Getting started
 * are in the Help menu, Search is the top bar, Upload is the primary button
 * on Runs, everything else is a section tab or under Admin.
 */
import {
  Bug, ClipboardList, FileText, FolderTree, GitBranch, HeartPulse, Inbox, LayoutDashboard,
  MessageSquare, Package, Settings, Shield, TrendingUp, type LucideIcon,
} from 'lucide-react'
import type { RouteTabItem } from '@/components/ui/RouteTabs'

export type NavSection = 'INVESTIGATE' | 'QUALITY' | 'RELEASE' | 'ASSIST'

export interface NavItem {
  /** Stable id: the `data-nav-id` tests select on, never the label. */
  id: string
  label: string
  icon: LucideIcon
  /** Where the item goes. */
  to: string
  /** Route prefixes this item owns (a path equal to one, or under it). */
  owns: readonly string[]
  /** The muted label above the item's group; `undefined` for the top items. */
  section?: NavSection
  /** `management`: QA lead and admin only (`usePermissions().canAccessManagement`). */
  requires?: 'management'
  /** Shown only when the Ask-AI chat can run (its flag on, and an LLM mode). */
  needsChat?: boolean
  /** The section's pages, as route tabs at the top of each of them. */
  tabs?: readonly RouteTabItem[]
}

export const NAV_ITEMS: readonly NavItem[] = [
  { id: 'home', label: 'Home', icon: LayoutDashboard, to: '/overview', owns: ['/overview'] },
  {
    id: 'inbox',
    label: 'Inbox',
    icon: Inbox,
    to: '/my-failures',
    // P4: one page, with its own tabs (Assigned to me · Approvals); /reviews redirects to it.
    owns: ['/my-failures', '/reviews'],
  },
  {
    id: 'runs',
    label: 'Runs',
    icon: GitBranch,
    to: '/runs',
    section: 'INVESTIGATE',
    // P4 (D2): the AI verdict is a column of the runs table; /intelligence redirects to /runs.
    owns: ['/runs', '/live', '/intelligence'],
    tabs: [
      { to: '/runs', label: 'History' },
      { to: '/live', label: 'Live' },
      { to: '/runs/compare', label: 'Compare' },
    ],
  },
  {
    id: 'failures',
    label: 'Failures',
    icon: Bug,
    to: '/failures',
    section: 'INVESTIGATE',
    owns: ['/failures', '/defects', '/deep-investigate'],
    tabs: [
      { to: '/failures', label: 'Failures' },
      { to: '/defects', label: 'Defects' },
      { to: '/deep-investigate', label: 'Root cause (AI)' },
    ],
  },
  {
    id: 'flaky',
    label: 'Flaky tests',
    icon: HeartPulse,
    // P4: one page, with its own tabs; /flaky-coach and /quarantine redirect to it.
    to: '/flaky',
    section: 'INVESTIGATE',
    owns: ['/flaky', '/flaky-coach', '/quarantine'],
  },
  {
    id: 'trends',
    label: 'Trends',
    icon: TrendingUp,
    to: '/trends',
    section: 'QUALITY',
    owns: ['/trends', '/coverage', '/explore'],
    tabs: [
      { to: '/trends', label: 'Trends' },
      { to: '/coverage', label: 'Coverage' },
      { to: '/explore', label: 'Explorer' },
    ],
  },
  {
    id: 'suites',
    label: 'Suites',
    icon: FolderTree,
    to: '/suites',
    section: 'QUALITY',
    owns: ['/suites', '/coverage/suite', '/canonical-test-cases'],
  },
  { id: 'test-cases', label: 'Test cases', icon: ClipboardList, to: '/test-management', section: 'QUALITY', owns: ['/test-management'] },
  {
    id: 'reports',
    label: 'Reports',
    icon: FileText,
    to: '/reports/summary',
    section: 'QUALITY',
    owns: ['/reports', '/value-metrics'],
    tabs: [
      { to: '/reports/summary', label: 'Summary' },
      { to: '/value-metrics', label: 'Value' },
    ],
  },
  { id: 'release-gate', label: 'Release gate', icon: Shield, to: '/release-gate', section: 'RELEASE', owns: ['/release-gate'] },
  {
    id: 'releases',
    label: 'Releases',
    icon: Package,
    to: '/releases',
    section: 'RELEASE',
    owns: ['/releases', '/policies'],
    requires: 'management',
    tabs: [
      { to: '/releases', label: 'Releases' },
      { to: '/policies', label: 'Gate policies' },
    ],
  },
  { id: 'ask-ai', label: 'Ask AI', icon: MessageSquare, to: '/chat', section: 'ASSIST', owns: ['/chat'], needsChat: true },
]

/** The footer item: settings and the admin pages, QA lead and admin only. */
export const ADMIN_ITEM: NavItem = {
  id: 'admin',
  label: 'Admin',
  icon: Settings,
  to: '/settings',
  owns: ['/settings', '/projects', '/users', '/ownership', '/activity', '/agents'],
  requires: 'management',
}

/** The pages that are personal, not admin, though they live under `/settings`. */
export const PERSONAL_PATHS: readonly string[] = ['/settings/profile', '/settings/my-notifications']

export interface NavAccess {
  canAccessManagement: boolean
  chatEnabled: boolean
}

/** The items this user sees, in order. */
export function visibleNavItems(access: NavAccess): NavItem[] {
  return NAV_ITEMS.filter(
    (item) =>
      (item.requires !== 'management' || access.canAccessManagement) && (!item.needsChat || access.chatEnabled),
  )
}

function ownsPath(prefix: string, pathname: string): boolean {
  return pathname === prefix || pathname.startsWith(`${prefix}/`)
}

/** The length of the longest prefix `item` owns that `pathname` falls under; 0 for none. */
function ownership(item: NavItem, pathname: string): number {
  return item.owns.reduce((best, prefix) => (ownsPath(prefix, pathname) ? Math.max(best, prefix.length) : best), 0)
}

/**
 * The item that owns `pathname`, among every item (the admin footer item
 * included); `null` for a path no item owns (`/search`, `/docs`, a personal
 * page). The longest owned prefix wins.
 */
export function owningNavItem(pathname: string): NavItem | null {
  if (PERSONAL_PATHS.some((p) => ownsPath(p, pathname))) return null
  let best: NavItem | null = null
  let bestLength = 0
  for (const item of [...NAV_ITEMS, ADMIN_ITEM]) {
    const length = ownership(item, pathname)
    if (length > bestLength) {
      best = item
      bestLength = length
    }
  }
  return best
}
