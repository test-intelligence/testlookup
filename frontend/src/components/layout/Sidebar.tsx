import { useCallback, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import {
  BarChart3, Bot, Brain, Bug, ChevronDown, ClipboardList, FlaskConical,
  Gauge, GitBranch, HeartPulse, Layers, LayoutDashboard, MessageSquare,
  Network, Package, Radio, Rocket, Search, Settings, Shield,
  ShieldAlert, ShieldCheck, ShieldEllipsis, TrendingUp, UsersRound, UserCircle2,
} from 'lucide-react'
import { clsx } from 'clsx'
import { usePermissions } from '@/hooks/usePermissions'
import { useAIConfig } from '@/hooks/useAIConfig'
import AppLogo from '@/components/ui/AppLogo'

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
    activePrefixes: ['/overview', '/getting-started', '/value-metrics'],
    children: [
      { to: '/getting-started', icon: Rocket,    label: 'Getting Started' },
      { to: '/value-metrics',   icon: BarChart3, label: 'Value Metrics'   },
    ],
  },
  {
    key: 'testing',
    label: 'Testing',
    icon: GitBranch,
    to: '/runs',
    activePrefixes: ['/runs', '/run/', '/live', '/coverage', '/failures', '/trends', '/defects', '/search', '/test-management', '/suite/'],
    children: [
      { to: '/live',            icon: Radio,       label: 'Live'       },
      { to: '/coverage',        icon: ShieldCheck, label: 'Coverage'   },
      { to: '/failures',        icon: Bug,         label: 'Failures'   },
      { to: '/trends',          icon: TrendingUp,  label: 'Trends'     },
      { to: '/defects',         icon: Gauge,       label: 'Defects'    },
      { to: '/search',          icon: Search,      label: 'Search'     },
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
      { to: '/chat',             icon: MessageSquare,  label: 'Chat'          },
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

  // Show a mode badge on the AI Reports group when in rules or ML mode
  const aiModeBadge =
    !aiConfig ? undefined :
    aiConfig.analysis_mode === 'rules' ? 'rules' :
    aiConfig.analysis_mode === 'ml' ? 'ml' :
    undefined

  const groups = GROUPS.map(g =>
    g.key === 'intelligence' && aiModeBadge ? { ...g, badge: aiModeBadge } : g
  )

  return (
    <aside className="w-56 flex-shrink-0 border-r flex flex-col" style={{ background: 'var(--color-bg-card)', borderColor: 'var(--color-border)' }}>
      {/* Logo */}
      <div className="px-4 py-5 border-b flex items-center justify-center" style={{ borderColor: 'var(--color-border)' }}>
        <AppLogo className="h-12 w-auto" />
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 py-4 overflow-y-auto space-y-1">
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
      </div>
    </aside>
  )
}
