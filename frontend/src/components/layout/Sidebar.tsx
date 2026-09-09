import { useCallback, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import {
  Activity, BarChart3, Bot, Brain, Bug, ChevronDown, ClipboardList, FileText,
  FolderTree, Gauge, GitBranch, HeartPulse, Inbox, Layers, LayoutDashboard,
  BookOpen, MessageSquare, Network, Package, Radio, Rocket, Search, Settings, Shield,
  ShieldAlert, ShieldCheck, ShieldEllipsis, TrendingUp, Upload, UsersRound, UserCircle2,
} from 'lucide-react'
import { clsx } from 'clsx'
import { usePermissions } from '@/hooks/usePermissions'
import { useFeatureEnabled } from '@/hooks/useFeatureFlags'
import { useAIConfig } from '@/hooks/useAIConfig'
import { useMyFailuresCountUnscoped } from '@/hooks/useMyFailures'
import AppLogo from '@/components/ui/AppLogo'
import AppVersionBadge from '@/components/layout/AppVersionBadge'

/* ─── Navigation structure: grouped with primary + sub-items ─── */

interface NavItem {
  to: string
  icon: React.ComponentType<{ className?: string }>
  label: string
}

interface NavGroup {
  key: string
  label: string
  icon: React.ComponentType<{ className?: string }>
  /** Primary link — clicking the group header navigates here */
  to: string
  /** Sub-items shown when expanded */
  children: NavItem[]
  /** Route prefixes that count as "within this group" */
  activePrefixes: string[]
  /** Optional badge text shown next to the group label */
  badge?: string
}

const GROUPS: NavGroup[] = [
  {
    key: 'dashboard',
    label: 'Dashboard',
    icon: LayoutDashboard,
    to: '/overview',
    activePrefixes: ['/overview', '/getting-started', '/docs', '/value-metrics', '/reports/'],
    children: [
      { to: '/getting-started',  icon: Rocket,    label: 'Getting Started' },
      { to: '/docs',             icon: BookOpen,  label: 'Documentation'   },
      { to: '/value-metrics',    icon: BarChart3, label: 'Value Metrics'   },
      { to: '/reports/summary',  icon: FileText,  label: 'Summary Report'  },
    ],
  },
  {
    key: 'testing',
    label: 'Testing',
    icon: GitBranch,
    to: '/runs',
    activePrefixes: ['/runs', '/run/', '/live', '/coverage', '/failures', '/trends', '/defects', '/search', '/test-management', '/suite/', '/suites'],
    children: [
      { to: '/live',            icon: Radio,       label: 'Live'          },
      { to: '/runs?upload=1',   icon: Upload,      label: 'Upload Report' },
      { to: '/coverage',        icon: ShieldCheck, label: 'Coverage'   },
      { to: '/suites',          icon: FolderTree,  label: 'Suites'     },
      { to: '/failures',        icon: Bug,         label: 'Failures'   },
      { to: '/trends',          icon: TrendingUp,  label: 'Trends'     },
      { to: '/defects',         icon: Gauge,       label: 'Defects'    },
      { to: '/search',          icon: Search,      label: 'Search'     },
      { to: '/activity',        icon: Activity,    label: 'Activity'   },
      { to: '/test-management', icon: ClipboardList, label: 'Test Cases' },
    ],
  },
  {
    key: 'intelligence',
    label: 'AI Reports',
    icon: Brain,
    to: '/intelligence',
    activePrefixes: ['/intelligence', '/agents', '/deep-investigate', '/release-gate', '/flaky-coach', '/quarantine', '/chat'],
    children: [
      { to: '/agents',           icon: Bot,           label: 'AI Pipeline'   },
      { to: '/deep-investigate', icon: Layers,         label: 'Deep Analysis' },
      { to: '/release-gate',     icon: Shield,         label: 'Release Gate'  },
      { to: '/flaky-coach',      icon: HeartPulse,     label: 'Flaky Coach'   },
      { to: '/quarantine',       icon: ShieldAlert,    label: 'Quarantine'    },
      // Gated below: hidden unless the ask_ai_chat flag is on AND the AI
      // analysis mode can reach an LLM (rules mode has nothing to chat with).
      { to: '/chat',             icon: MessageSquare,  label: 'Ask AI'        },
    ],
  },
]

const MANAGEMENT_GROUP: NavGroup = {
  key: 'manage',
  label: 'Management',
  icon: UsersRound,
  to: '/projects',
  activePrefixes: ['/projects', '/releases', '/policies', '/ownership', '/users'],
  children: [
    { to: '/releases',  icon: Package,        label: 'Releases'  },
    { to: '/policies',  icon: ShieldEllipsis, label: 'Policies'  },
    { to: '/ownership', icon: Network,        label: 'Ownership' },
    { to: '/users',     icon: UsersRound,     label: 'Users'     },
  ],
}

/* ─── Collapsible nav group component ─── */

function SidebarGroup({ group }: { group: NavGroup }) {
  const location = useLocation()
  const isWithinGroup = group.activePrefixes.some(p => location.pathname.startsWith(p))
  const [open, setOpen] = useState(isWithinGroup)

  // P4-2: Stable callback reference — prevents child re-renders on every parent render
  const handleToggle = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    setOpen(v => !v)
  }, [])

  const GroupIcon = group.icon

  return (
    <div>
      {/* Group header — navigates + toggles */}
      <div className="flex items-center">
        <NavLink
          to={group.to}
          className={({ isActive }) =>
            clsx(
              'sidebar-link flex-1',
              (isActive || isWithinGroup) && 'active',
            )
          }
        >
          <GroupIcon className="h-4 w-4 flex-shrink-0" />
          {group.label}
          {group.badge && (
            <span className="ml-1 text-[9px] px-1 py-0.5 rounded bg-[var(--color-bg-hover)] text-[var(--color-text-faint)] font-mono uppercase tracking-wide">
              {group.badge}
            </span>
          )}
        </NavLink>
        {group.children.length > 0 && (
          <button
            onClick={handleToggle}
            className="p-1.5 rounded-md transition-colors"
            style={{ color: 'var(--color-text-faint)' }}
            aria-label={open ? 'Collapse' : 'Expand'}
            aria-expanded={open}
          >
            <ChevronDown
              className={clsx('h-3.5 w-3.5 transition-transform duration-200', open && 'rotate-180')}
            />
          </button>
        )}
      </div>

      {/* Sub-items */}
      {open && group.children.length > 0 && (
        <div className="ml-4 pl-3 mt-0.5 space-y-0.5" style={{ borderLeft: '1px solid var(--color-border)' }}>
          {group.children.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                clsx('sidebar-link text-[13px] py-2', isActive && 'active')
              }
            >
              <Icon className="h-3.5 w-3.5 flex-shrink-0" />
              {label}
            </NavLink>
          ))}
        </div>
      )}
    </div>
  )
}

/* ─── Sidebar ─── */

export default function Sidebar() {
  const { canAccessManagement } = usePermissions()
  const { data: aiConfig } = useAIConfig()
  // Rollout gate (MRU-17): hide the Upload Report item until the flag is on.
  const uploadEnabled = useFeatureEnabled('manual_upload')
  // Ask-AI chat (US-2.1): flag-gated, and pointless without an LLM — hide the
  // nav item in rules mode so there is no dead entry. Direct navigation to
  // /chat still works; the page renders its own "switch mode" guidance.
  const chatFlagEnabled = useFeatureEnabled('ask_ai_chat')
  const chatEnabled = chatFlagEnabled && !!aiConfig && aiConfig.analysis_mode !== 'rules'

  // Show a mode badge on the AI Reports group when in rules or ML mode
  const aiModeBadge =
    !aiConfig ? undefined :
    aiConfig.analysis_mode === 'rules' ? 'rules' :
    aiConfig.analysis_mode === 'ml' ? 'ml' :
    undefined

  const groups = GROUPS.map(g => {
    let group = g.key === 'intelligence' && aiModeBadge ? { ...g, badge: aiModeBadge } : g
    if (!uploadEnabled) {
      const children = group.children.filter(c => c.to !== '/runs?upload=1')
      if (children.length !== group.children.length) group = { ...group, children }
    }
    if (!chatEnabled) {
      const children = group.children.filter(c => c.to !== '/chat')
      if (children.length !== group.children.length) group = { ...group, children }
    }
    return group
  })

  return (
    <aside className="w-56 flex-shrink-0 border-r flex flex-col" style={{ background: 'var(--color-bg-card)', borderColor: 'var(--color-border)' }}>
      {/* Logo */}
      <div className="px-4 py-5 border-b flex items-center justify-center" style={{ borderColor: 'var(--color-border)' }}>
        <AppLogo className="text-[20px]" />
      </div>

      {/* Navigation */}
      {/* Named landmark: the app shell's <aside> is not the only one on screen
          (LiveExecutionPage renders a "Pipeline events" <aside> beside it), so
          a bare element locator is ambiguous. The accessible name gives tests
          and screen readers one unmistakable handle on the primary nav. */}
      <nav className="flex-1 px-3 py-4 overflow-y-auto space-y-1" aria-label="Main navigation">
        {/* My Failures inbox — personal action queue. Sits above the group
            tree because it's user-specific work, not a project navigation
            target. Polls /api/v1/me/assigned-failures/count every 30s. */}
        <MyFailuresLink />

        {groups.map(group => (
          <SidebarGroup key={group.key} group={group} />
        ))}

        {canAccessManagement && (
          <>
            <div className="my-2" style={{ borderTop: '1px solid var(--color-border)', opacity: 0.5 }} />
            <SidebarGroup group={MANAGEMENT_GROUP} />
          </>
        )}
      </nav>

      {/* Footer */}
      <div className="px-3 py-3 space-y-1" style={{ borderTop: '1px solid var(--color-border)' }}>
        {/* Profile is accessible to all authenticated roles */}
        <NavLink
          to="/settings/profile"
          className={({ isActive }) => clsx('sidebar-link', isActive && 'active')}
        >
          <UserCircle2 className="h-4 w-4 flex-shrink-0" />
          My Profile
        </NavLink>
        {canAccessManagement && (
          <NavLink
            to="/settings"
            end
            className={({ isActive }) => clsx('sidebar-link', isActive && 'active')}
          >
            <Settings className="h-4 w-4 flex-shrink-0" />
            Settings
          </NavLink>
        )}
        <AppVersionBadge />
      </div>
    </aside>
  )
}


/**
 * Sidebar entry for the My Failures inbox. The count badge is unscoped
 * (across all accessible projects) so a user switching projects doesn't
 * lose sight of pending work elsewhere. SWR polls every 30s.
 *
 * Rendered as its own component so the count hook only fires here — the
 * rest of the sidebar doesn't re-render when the badge changes.
 */
function MyFailuresLink() {
  const { data } = useMyFailuresCountUnscoped()
  const count = data?.count ?? 0
  return (
    <NavLink
      to="/my-failures"
      className={({ isActive }) => clsx('sidebar-link', isActive && 'active')}
    >
      <Inbox className="h-4 w-4 flex-shrink-0" />
      My Failures
      {count > 0 && (
        <span
          className="ml-auto text-[10px] px-1.5 py-0.5 rounded-full font-medium tabular-nums"
          style={{
            background: 'color-mix(in srgb, var(--status-failed) 18%, transparent)',
            color: 'var(--status-failed)',
            border: '1px solid color-mix(in srgb, var(--status-failed) 30%, transparent)',
          }}
          aria-label={`${count} assigned failures`}
        >
          {count > 99 ? '99+' : count}
        </span>
      )}
    </NavLink>
  )
}
