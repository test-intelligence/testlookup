import { useEffect, useRef, useState } from 'react'
import { Search, Bell, CheckCircle, XCircle } from 'lucide-react'
import { useNavigate, Link } from 'react-router-dom'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { projectsService } from '@/services/projectsService'
import { useAuthStore } from '@/store/authStore'
import { UserCircle, LogOut } from 'lucide-react'
import { useUnreadCount, useNotificationHistory, invalidateNotifications } from '@/hooks/useNotifications'
import { notificationService } from '@/services/notificationService'
import type { Project } from '@/types/projects'
import ThemeToggle from '@/components/ui/ThemeToggle'

export default function TopBar() {
  const navigate = useNavigate()
  const { activeProject, activeProjectId, setActiveProject, setAllProjects } = useProjectStore()
  const [projects, setProjects] = useState<Project[]>([])
  const [searchVal, setSearchVal] = useState('')
  const [bellOpen, setBellOpen] = useState(false)
  const bellRef = useRef<HTMLDivElement>(null)

  const { data: unreadData } = useUnreadCount()
  const { data: recentLogs, mutate: refreshLogs } = useNotificationHistory(false)
  const unreadCount = unreadData?.unread ?? 0

  useEffect(() => {
    projectsService.list().then(setProjects).catch(() => {})
  }, [])

  // Close bell dropdown when clicking outside
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (bellRef.current && !bellRef.current.contains(e.target as Node)) {
        setBellOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const handleSearch = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && searchVal.trim()) {
      navigate(`/search?q=${encodeURIComponent(searchVal.trim())}`)
      setSearchVal('')
    }
  }

  const handleMarkAll = async () => {
    await notificationService.markAllRead()
    refreshLogs()
    invalidateNotifications()
  }

  return (
    <header className="h-14 border-b backdrop-blur flex items-center px-6 gap-4 flex-shrink-0" style={{ borderColor: 'var(--color-border)', background: 'var(--color-bg-card)' }}>
      {/* Global search */}
      <div className="flex-1 max-w-md relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-[var(--color-text-muted)] pointer-events-none" />
        <input
          type="text"
          placeholder="Search tests, errors… (Enter)"
          className="input pl-9 h-9 text-sm"
          value={searchVal}
          onChange={e => setSearchVal(e.target.value)}
          onKeyDown={handleSearch}
        />
      </div>

      {/* Project selector */}
      <select
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
        <option value={ALL_PROJECTS_ID}>All Projects</option>
        {projects.map(p => (
          <option key={p.id} value={p.id}>{p.name}</option>
        ))}
      </select>

      {/* Notification bell */}
      <div ref={bellRef} className="relative">
        <button
          onClick={() => setBellOpen(v => !v)}
          className="relative p-2 rounded-lg text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-bg-secondary)] transition-colors"
          aria-label="Notifications"
        >
          <Bell className="w-5 h-5" />
          {unreadCount > 0 && (
            <span className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] bg-red-500 text-[var(--color-text)] text-[10px] font-bold rounded-full flex items-center justify-center px-1">
              {unreadCount > 99 ? '99+' : unreadCount}
            </span>
          )}
        </button>

        {bellOpen && (
          <div className="absolute right-0 top-full mt-2 w-80 bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-xl shadow-2xl z-50 overflow-hidden">
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
                          <CheckCircle className="w-4 h-4 text-emerald-400" />
                        ) : (
                          <XCircle className="w-4 h-4 text-red-400" />
                        )}
                      </span>
                      <div className="flex-1 min-w-0">
                        <p className={`text-sm truncate ${!log.is_read ? 'text-[var(--color-text)] font-medium' : 'text-[var(--color-text-muted)]'}`}>
                          {log.title}
                        </p>
                        <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
                          {log.channel.toUpperCase()} · {new Date(log.created_at).toLocaleString()}
                        </p>
                      </div>
                      {!log.is_read && (
                        <span className="w-2 h-2 rounded-full bg-neutral-400 shrink-0 mt-1.5" />
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
        )}
      </div>

      {/* Theme toggle */}
      <ThemeToggle />

      {/* User profile */}
      <div className="flex items-center gap-3 border-l pl-4 relative group cursor-pointer h-full" style={{ borderColor: 'var(--color-border)' }}>
        <UserCircle className="w-8 h-8 text-[var(--color-text-muted)]" />
        <div className="flex flex-col justify-center">
          <span className="text-sm font-medium text-[var(--color-text)] leading-none">
            {useAuthStore(s => s.user?.full_name || s.user?.username || 'User')}
          </span>
          <span className="text-xs text-[var(--color-text-muted)] mt-1 leading-none">{useAuthStore(s => s.user?.role || '')}</span>
        </div>

        {/* Dropdown on hover */}
        <div className="absolute right-0 top-12 mt-2 w-48 bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-md shadow-lg py-1 opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto transition-opacity z-50">
          <div className="px-4 py-2 border-b border-[var(--color-border)]">
            <p className="text-sm text-[var(--color-text-secondary)] font-medium">{useAuthStore(s => s.user?.email || '')}</p>
          </div>
          <button
            onClick={() => useAuthStore.getState().logout()}
            className="w-full text-left px-4 py-2 text-sm text-red-400 hover:bg-[var(--color-bg-hover)]/50 flex items-center gap-2 transition-colors mt-1"
          >
            <LogOut className="w-4 h-4" />
            Sign out
          </button>
        </div>
      </div>
    </header>
  )
}
