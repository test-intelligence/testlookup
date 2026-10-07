import { useState, type MouseEvent } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { PanelLeftClose, PanelLeftOpen, X } from 'lucide-react'
import { clsx } from 'clsx'
import { usePermissions } from '@/hooks/usePermissions'
import { useFeatureEnabled } from '@/hooks/useFeatureFlags'
import { useAIConfig } from '@/hooks/useAIConfig'
import { useMyFailuresCountUnscoped } from '@/hooks/useMyFailuresCountUnscoped'
import { useLiveRunningCount } from '@/hooks/useLiveRunningCount'
import AppLogo from '@/components/ui/AppLogo'
import { useModalFocus } from '@/hooks/useModalFocus'
import { ADMIN_ITEM, owningNavItem, visibleNavItems, type NavItem } from './navConfig'

/** Where the rail's collapsed state is kept, per browser. */
export const SIDEBAR_COLLAPSED_KEY = 'tl.sidebar.collapsed'

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === '1'
  } catch {
    return false
  }
}

function writeCollapsed(collapsed: boolean): void {
  try {
    if (collapsed) window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, '1')
    else window.localStorage.removeItem(SIDEBAR_COLLAPSED_KEY)
  } catch {
    // Storage blocked: the rail still collapses, for this page load.
  }
}

/**
 * The sidebar as a drawer (VIZ-106), handed in by `AppLayout` below 1024 px
 * only; `null` at 1024 px and above, where the sidebar is the permanent
 * column.
 */
export interface SidebarDrawer {
  open: boolean
  onClose: () => void
  /** The element id the top bar's menu button names in `aria-controls`. */
  id: string
}

/**
 * Below 1024 px: hidden until opened, then fixed over the page's left edge as
 * a modal dialog. Only `max-lg:` utilities, so none of this reaches the
 * desktop column's styling.
 */
const DRAWER_CLOSED = 'max-lg:hidden'
const DRAWER_OPEN = 'max-lg:fixed max-lg:inset-y-0 max-lg:left-0 max-lg:z-50 max-lg:shadow-xl'

function InboxBadge({ collapsed }: { collapsed: boolean }) {
  const { data } = useMyFailuresCountUnscoped()
  const count = data?.count ?? 0
  if (count <= 0) return null
  return (
    <span
      data-nav-badge=""
      className={clsx(
        'rounded-full text-[10px] font-medium tabular-nums',
        collapsed ? 'absolute right-1 top-0.5 px-1 leading-4' : 'ml-auto px-1.5 py-0.5',
      )}
      style={{
        background: 'color-mix(in srgb, var(--status-failed) 18%, transparent)',
        color: 'var(--status-failed)',
        border: '1px solid color-mix(in srgb, var(--status-failed) 30%, transparent)',
      }}
      aria-label={`${count} assigned failures`}
    >
      {count > 99 ? '99+' : count}
    </span>
  )
}

function LiveDot({ collapsed }: { collapsed: boolean }) {
  const running = useLiveRunningCount()
  if (running <= 0) return null
  return (
    <span
      data-nav-live=""
      role="img"
      aria-label={`${running} run${running === 1 ? '' : 's'} live now`}
      title={`${running} run${running === 1 ? '' : 's'} live now`}
      className={clsx('h-2 w-2 rounded-full bg-[var(--status-failed)] animate-pulse', collapsed ? 'absolute right-2 top-1.5' : 'ml-auto')}
    />
  )
}

function SidebarItem({ item, active, collapsed }: { item: NavItem; active: boolean; collapsed: boolean }) {
  const Icon = item.icon
  return (
    // A plain Link: NavLink would mark itself active by its OWN route (Admin's
    // `/settings` on `/settings/profile`); here the owning item is active.
    <Link
      to={item.to}
      data-nav-id={item.id}
      title={collapsed ? item.label : undefined}
      aria-label={collapsed ? item.label : undefined}
      aria-current={active ? 'page' : undefined}
      className={clsx('sidebar-link relative', active && 'active', collapsed && 'justify-center !px-0')}
    >
      <Icon className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
      {!collapsed && <span className="truncate">{item.label}</span>}
      {item.id === 'inbox' && <InboxBadge collapsed={collapsed} />}
      {item.id === 'runs' && <LiveDot collapsed={collapsed} />}
    </Link>
  )
}

export default function Sidebar({ drawer = null }: { drawer?: SidebarDrawer | null } = {}) {
  const drawerOpen = drawer?.open === true
  // The drawer is a modal dialog while it is open: focus moves into it, Tab
  // and Shift+Tab stay inside, Escape closes it, and focus goes back to the
  // menu button that opened it.
  const drawerRef = useModalFocus<HTMLElement>({ open: drawerOpen, onClose: drawer?.onClose })
  // A link inside the drawer closes it even when it points at the page
  // already shown (no location change for AppLayout to see).
  const closeOnLink = (event: MouseEvent<HTMLElement>) => {
    if (event.target instanceof Element && event.target.closest('a[href]')) drawer?.onClose()
  }
  const { pathname } = useLocation()
  const { canAccessManagement } = usePermissions()
  const { data: aiConfig } = useAIConfig()
  // Ask-AI chat (US-2.1): flag-gated, and pointless without an LLM, so no
  // dead entry in rules mode. Direct navigation to /chat still works.
  const chatFlagEnabled = useFeatureEnabled('ask_ai_chat')
  const chatEnabled = chatFlagEnabled && !!aiConfig && aiConfig.analysis_mode !== 'rules'

  const [collapsedPref, setCollapsedPref] = useState(readCollapsed)
  // The drawer always shows labels: it is opened to read them.
  const collapsed = collapsedPref && !drawer
  const toggleCollapsed = () => {
    const next = !collapsedPref
    setCollapsedPref(next)
    writeCollapsed(next)
  }

  const items = visibleNavItems({ canAccessManagement, chatEnabled })
  const activeId = owningNavItem(pathname)?.id ?? null

  return (
    <aside
      ref={drawerRef}
      id={drawer?.id}
      role={drawerOpen ? 'dialog' : undefined}
      aria-modal={drawerOpen ? true : undefined}
      aria-label={drawerOpen ? 'Navigation' : undefined}
      onClick={drawerOpen ? closeOnLink : undefined}
      data-sidebar={collapsed ? 'collapsed' : 'expanded'}
      className={clsx(
        'flex-shrink-0 border-r flex flex-col transition-[width] duration-150',
        collapsed ? 'w-16' : 'w-56',
        drawerOpen ? DRAWER_OPEN : DRAWER_CLOSED,
      )}
      style={{ background: 'var(--color-bg-card)', borderColor: 'var(--color-border)' }}
    >
      <div
        className={clsx('py-5 border-b flex items-center justify-center max-lg:relative', collapsed ? 'px-2' : 'px-4')}
        style={{ borderColor: 'var(--color-border)' }}
      >
        <AppLogo glyph={collapsed} className="text-[20px]" />
        {drawerOpen && (
          <button
            type="button"
            onClick={drawer?.onClose}
            aria-label="Close navigation"
            className="absolute right-2 top-1/2 -translate-y-1/2 inline-flex h-8 w-8 items-center justify-center rounded-md text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-ring)]"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        )}
      </div>

      {/* Named landmark: the app shell's <aside> is not the only one on screen
          (LiveExecutionPage renders a "Pipeline events" <aside> beside it). */}
      <nav className={clsx('flex-1 py-4 overflow-y-auto', collapsed ? 'px-2' : 'px-3')} aria-label="Main navigation">
        {items.map((item, index) => {
          const startsSection = item.section !== undefined && item.section !== items[index - 1]?.section
          return (
            <div key={item.id} className="mt-0.5">
              {startsSection &&
                (collapsed ? (
                  <div className="mx-2 my-3 border-t" style={{ borderColor: 'var(--color-border)' }} aria-hidden="true" />
                ) : (
                  <div
                    data-nav-section={item.section}
                    className="px-3 pb-1 pt-4 text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]"
                  >
                    {item.section}
                  </div>
                ))}
              <SidebarItem item={item} active={item.id === activeId} collapsed={collapsed} />
            </div>
          )
        })}
      </nav>

      <div className={clsx('py-3 space-y-1', collapsed ? 'px-2' : 'px-3')} style={{ borderTop: '1px solid var(--color-border)' }}>
        {canAccessManagement && <SidebarItem item={ADMIN_ITEM} active={activeId === ADMIN_ITEM.id} collapsed={collapsed} />}
        {!drawer && (
          <button
            type="button"
            data-sidebar-toggle=""
            onClick={toggleCollapsed}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            aria-expanded={!collapsed}
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            className={clsx('sidebar-link w-full', collapsed && 'justify-center !px-0')}
          >
            {collapsed ? (
              <PanelLeftOpen className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
            ) : (
              <PanelLeftClose className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
            )}
            {!collapsed && <span>Collapse</span>}
          </button>
        )}
      </div>
    </aside>
  )
}
