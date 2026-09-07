import { useEffect, useRef, useState } from 'react'
import { Search, Bell, CheckCircle, XCircle } from 'lucide-react'
import { useNavigate, Link } from 'react-router-dom'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { ReleasePicker } from './ReleasePicker'
import { useAuthStore } from '@/store/authStore'
import { usePermissions } from '@/hooks/usePermissions'
import { LogOut } from 'lucide-react'
import { useUnreadCount, useNotificationHistory, invalidateNotifications } from '@/hooks/useNotifications'
import { notificationService } from '@/services/notificationService'
import ThemePicker from '@/components/ui/ThemePicker'
import { HeaderPopover } from '@/components/ui/HeaderPopover'
import { formatCompactDateTime } from '@/utils/formatters'

const AVATAR_BG: Record<string, string> = {
  slate: 'bg-[var(--color-bg-hover)]', red: 'bg-[var(--status-failed-bg)]', orange: 'bg-[var(--status-broken-bg)]',
  amber: 'bg-[var(--status-broken-bg)]', lime: 'bg-[var(--status-passed-bg)]', emerald: 'bg-[var(--status-passed-bg)]',
  teal: 'bg-[var(--status-passed-bg)]', cyan: 'bg-[var(--color-accent-muted)]', blue: 'bg-[var(--color-accent-muted)]',
  violet: 'bg-[var(--status-flaky-bg)]', fuchsia: 'bg-[var(--status-flaky-bg)]', pink: 'bg-[var(--status-flaky-bg)]',
}

function getInitials(fullName: string | null | undefined, username: string): string {
  if (fullName?.trim()) {
    const parts = fullName.trim().split(/\s+/)
    return parts.length >= 2
      ? (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
      : parts[0].slice(0, 2).toUpperCase()
  }
  return username.slice(0, 2).toUpperCase()
}

/* P4-1: Three selectors that each return a primitive string — Zustand's
   default Object.is equality works correctly with primitives, so each
   selector only triggers a re-render when its specific value changes. */
function UserProfileDropdown() {
  const userName = useAuthStore(s => s.user?.full_name || s.user?.username || 'User')
  const userRole = useAuthStore(s => s.user?.role || '')
  const userEmail = useAuthStore(s => s.user?.email || '')
  const avatarColor = useAuthStore(s => s.user?.avatar_color || 'blue')
  const username = useAuthStore(s => s.user?.username || 'U')
  const fullName = useAuthStore(s => s.user?.full_name ?? null)

  const [open, setOpen] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  // Dismissal lives in HeaderPopover: once portaled, the panel is not a DOM
  // descendant of this subtree, so a rootRef.contains() test would read clicks
  // inside the menu as outside clicks.

  const initials = getInitials(fullName, username)
  const bgClass = AVATAR_BG[avatarColor] ?? AVATAR_BG['blue']

  return (
    <div className="relative border-l pl-4 h-full flex items-center" style={{ borderColor: 'var(--color-border)' }}>
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Account menu"
        onClick={() => setOpen(v => !v)}
        className="flex items-center gap-3 h-full rounded-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)]"
      >
        <div className={`w-8 h-8 rounded-full flex items-center justify-center text-white font-bold text-xs flex-shrink-0 ${bgClass}`}>
          {initials}
        </div>
        <div className="flex flex-col justify-center items-start">
          <span className="text-sm font-medium text-[var(--color-text)] leading-none">
            {userName}
          </span>
          <span className="text-xs text-[var(--color-text-muted)] mt-1 leading-none">{userRole}</span>
        </div>
      </button>

      <HeaderPopover
        anchorRef={triggerRef}
        open={open}
        onClose={() => setOpen(false)}
        width={192}
        ariaLabel="Account"
      >
        <div className="py-1">
          <div className="px-4 py-2 border-b border-[var(--color-border)]">
            <p className="text-sm text-[var(--color-text-secondary)] font-medium">{userEmail}</p>
          </div>
          <button
            role="menuitem"
            onClick={() => useAuthStore.getState().logout()}
            className="w-full text-left px-4 py-2 text-sm text-[var(--status-failed)] hover:bg-[var(--color-bg-hover)]/50 flex items-center gap-2 transition-colors mt-1"
          >
            <LogOut className="w-4 h-4" />
            Sign out
          </button>
        </div>
      </HeaderPopover>
    </div>
  )
}

export default function TopBar() {
  const navigate = useNavigate()
  // Individual primitive selectors avoid Zustand v5 infinite-loop pitfall (#30).
  const activeProject = useProjectStore(s => s.activeProject)
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const setActiveProject = useProjectStore(s => s.setActiveProject)
  const setAllProjects = useProjectStore(s => s.setAllProjects)
  const projects = useProjectStore(s => s.projects)
  const refreshProjects = useProjectStore(s => s.refreshProjects)
  const [searchVal, setSearchVal] = useState('')
  const [bellOpen, setBellOpen] = useState(false)
  const bellTriggerRef = useRef<HTMLButtonElement>(null)
  const searchInputRef = useRef<HTMLInputElement>(null)
  const { isAdmin } = usePermissions()

  const { data: unreadData } = useUnreadCount()
  const { data: recentLogs, mutate: refreshLogs } = useNotificationHistory(false)
  const unreadCount = unreadData?.unread ?? 0

  useEffect(() => {
    refreshProjects().then((list) => {
      // Non-admin users cannot use "All Projects" — auto-select first project
      if (!isAdmin) {
        const currentIsAll = useProjectStore.getState().activeProjectId === ALL_PROJECTS_ID
        const currentIsNull = useProjectStore.getState().activeProjectId === null
        if (currentIsAll || currentIsNull) {
          if (list.length > 0) {
            setActiveProject(list[0])
          }
        }
      }
    }).catch(() => {})
  }, [isAdmin, setActiveProject, refreshProjects])

  const handleSearch = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && searchVal.trim()) {
      navigate(`/search?q=${encodeURIComponent(searchVal.trim())}`)
      setSearchVal('')
    }
  }

  // "/" focuses the global search box — the canonical GitHub/GitLab-style
  // focus-search key. Deliberately not ⌘/Ctrl-K: the search results page owns
  // that combo for its own input (SearchPage.tsx), and a second global handler
  // would fight it there. Guarded so it never steals a slash the user is
  // actually typing into a field, textarea, contenteditable, or select.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey) return
      const target = e.target as HTMLElement | null
      const tag = target?.tagName?.toLowerCase()
      if (tag === 'input' || tag === 'textarea' || tag === 'select' || target?.isContentEditable) return
      e.preventDefault()
      searchInputRef.current?.focus()
      searchInputRef.current?.select()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const handleMarkAll = async () => {
    await notificationService.markAllRead()
    refreshLogs()
    invalidateNotifications()
  }

  return (
    <header className="relative z-40 h-14 border-b backdrop-blur flex items-center px-6 gap-4 flex-shrink-0" style={{ borderColor: 'var(--color-border)', background: 'var(--color-bg-card)' }}>
      {/* Global search */}
      <div className="flex-1 max-w-md relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-[var(--color-text-muted)] pointer-events-none" />
        <input
          ref={searchInputRef}
          type="text"
          placeholder="Search tests, runs, defects… (Enter)"
          className="input pl-9 pr-9 h-9 text-sm"
          value={searchVal}
          onChange={e => setSearchVal(e.target.value)}
          onKeyDown={handleSearch}
        />
        {/* Shortcut hint — hidden once the box has a value so it never overlaps
            typed text. pointer-events-none so it can't intercept the click that
            would otherwise focus the input. */}
        {!searchVal && (
          <kbd
            aria-hidden="true"
            className="absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none hidden sm:inline-flex items-center justify-center h-5 min-w-[1.25rem] px-1 rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[11px] font-mono text-[var(--color-text-muted)]"
          >
            /
          </kbd>
        )}
      </div>

      {/* Project selector */}
      <select
        aria-label="Select project"
        className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text-secondary)] text-sm rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-[var(--color-ring)]"
        value={activeProjectId === ALL_PROJECTS_ID ? ALL_PROJECTS_ID : (activeProject?.id ?? '')}
        onChange={e => {
          if (e.target.value === ALL_PROJECTS_ID) {
            setAllProjects()
          } else {
            const p = projects.find(x => x.id === e.target.value) ?? null
            setActiveProject(p)
          }
        }}
      >
        <option value="">— Select project —</option>
        {isAdmin && <option value={ALL_PROJECTS_ID}>All Projects</option>}
        {projects.map(p => (
          <option key={p.id} value={p.id}>{p.name}</option>
        ))}
      </select>

      {/* Release selector — the third global filter, after project and time
          window. Sits next to the project picker because it is scoped BY it:
          it is inert until a single project is pinned. */}
      <ReleasePicker />

      {/* Notification bell — starts the right-aligned trailing group.
          ``ml-auto`` on the first of these siblings pushes it and everything
          after it to the right edge. Without it the search box (``flex-1
          max-w-md``) stops growing at its max width and the bell / theme picker
          / profile menu bunch up mid-header on a wide screen — which put the
          theme picker's 16rem ``right-0`` panel over page content instead of
          against the edge, where the other menus sit. */}
      <div className="relative ml-auto">
        <button
          ref={bellTriggerRef}
          onClick={() => setBellOpen(v => !v)}
          className="relative p-2 rounded-lg text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-bg-secondary)] transition-colors"
          aria-label={unreadCount > 0 ? `Notifications (${unreadCount} unread)` : 'Notifications'}
        >
          <Bell className="w-5 h-5" />
          {unreadCount > 0 && (
            <span className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] bg-[var(--status-failed)] text-white text-[11px] font-bold rounded-full flex items-center justify-center px-1">
              {unreadCount > 99 ? '99+' : unreadCount}
            </span>
          )}
        </button>

        <HeaderPopover
          anchorRef={bellTriggerRef}
          open={bellOpen}
          onClose={() => setBellOpen(false)}
          width={320}
          ariaLabel="Notifications"
          role="dialog"
        >
          <div className="overflow-hidden">
            <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border)]">
              <span className="font-semibold text-[var(--color-text)] text-sm">Notifications</span>
              <div className="flex items-center gap-3">
                {unreadCount > 0 && (
                  <button
                    onClick={handleMarkAll}
                    className="text-xs text-[var(--color-text)] hover:text-[var(--color-text-secondary)] transition-colors"
                  >
                    Mark all read
                  </button>
                )}
                <Link
                  to="/settings/notifications"
                  onClick={() => setBellOpen(false)}
                  className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
                >
                  Settings
                </Link>
              </div>
            </div>

            <div className="max-h-80 overflow-y-auto">
              {!recentLogs || recentLogs.length === 0 ? (
                <div className="px-4 py-8 text-center">
                  <Bell className="w-8 h-8 text-[var(--color-text-faint)] mx-auto mb-2" />
                  <p className="text-sm text-[var(--color-text-muted)]">No notifications yet</p>
                </div>
              ) : (
                <ul>
                  {recentLogs.slice(0, 15).map(log => (
                    <li
                      key={log.id}
                      className={`px-4 py-3 border-b border-[var(--color-border)] last:border-0 flex items-start gap-3 hover:bg-[var(--color-bg-hover)]/30 transition-colors ${
                        !log.is_read ? 'bg-[var(--color-bg-hover)]/20' : ''
                      }`}
                    >
                      <span className="mt-0.5 shrink-0">
                        {log.status === 'sent' ? (
                          <CheckCircle className="w-4 h-4 text-[var(--status-passed)]" />
                        ) : (
                          <XCircle className="w-4 h-4 text-[var(--status-failed)]" />
                        )}
                      </span>
                      <div className="flex-1 min-w-0">
                        <p className={`text-sm truncate ${!log.is_read ? 'text-[var(--color-text)] font-medium' : 'text-[var(--color-text-muted)]'}`}>
                          {log.title}
                        </p>
                        <p
                          className="text-xs text-[var(--color-text-muted)] mt-0.5"
                          title={new Date(log.created_at).toISOString()}
                        >
                          {log.channel.toUpperCase()} · {formatCompactDateTime(log.created_at)}
                        </p>
                      </div>
                      {!log.is_read && (
                        <span className="w-2 h-2 rounded-full bg-[var(--color-bg-hover)] shrink-0 mt-1.5" />
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="px-4 py-2 border-t border-[var(--color-border)] bg-[var(--color-bg-hover)]/50">
              <Link
                to="/settings/notifications"
                onClick={() => setBellOpen(false)}
                className="text-xs text-[var(--color-text)] hover:text-[var(--color-text-secondary)] transition-colors"
              >
                Manage notification settings →
              </Link>
            </div>
          </div>
        </HeaderPopover>
      </div>

      {/* Color-theme picker */}
      <ThemePicker />

      {/* User profile — P4-1: single consolidated selector to prevent 3x re-renders */}
      <UserProfileDropdown />
    </header>
  )
}
