/**
 * My Failures — the calling user's auto-assigned failures (migration 0080).
 *
 * Reads from /api/v1/me/assigned-failures. Project scope is driven by the
 * project store (ALL_PROJECTS_ID = "all"). The page polls every 30s so new
 * ingests appear without a manual refresh.
 *
 * Layout:
 *   Header        → title + counts + filter chips (days + status hint)
 *   Empty state   → friendly "you're caught up" when total === 0
 *   Table         → severity dot, test name + suite, project · build, age,
 *                   error preview, row-click → /runs/:rid/tests/:cid
 *   Pagination    → standard Pagination component
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, Clock, ExternalLink, Inbox } from 'lucide-react'
import { clsx } from 'clsx'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import EmptyState from '@/components/ui/EmptyState'
import Pagination from '@/components/ui/Pagination'
import { useMyFailures } from '@/hooks/useMyFailures'
import { useProjectStore, ALL_PROJECTS_ID } from '@/store/projectStore'
import { useAuthStore } from '@/store/authStore'
import type { MyFailureItem } from '@/types/myFailures'

// Time-window chips. The backend allows 1-365; surface the most useful three.
const DAYS_OPTIONS = [1, 7, 30] as const

function relativeAge(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime()
  if (Number.isNaN(ms) || ms < 0) return '—'
  const m = Math.floor(ms / 60_000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  return `${d}d ago`
}

function severityDot(severity: string | null | undefined): string {
  const s = (severity || '').toLowerCase()
  if (s === 'blocker' || s === 'critical') return '#fca5a5'  // red
  if (s === 'major' || s === 'high')        return '#fcd34d' // amber
  if (s === 'minor' || s === 'low')         return '#9198a1' // slate
  return '#86efac'                                            // unknown → green-ish
}

export default function MyFailuresPage() {
  const navigate = useNavigate()
  const user = useAuthStore(s => s.user)
  const project = useProjectStore(s => s.activeProject)
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID

  const [days, setDays] = useState<number>(30)
  const [page, setPage] = useState(1)
  const size = 25

  const { data, isLoading, error } = useMyFailures({ days, page, size })

  const scopeLabel = isAllProjects ? 'all your projects' : (project?.name ?? 'the selected project')

  return (
    <>
      <PageHeader
        title="My Failures"
        subtitle={
          user
            ? `Auto-assigned failures across ${scopeLabel}. Updated every 30 seconds.`
            : 'Sign in to see your assigned failures.'
        }
      />

      {/* Filter row */}
      <div className="flex items-center gap-2.5 flex-wrap mb-3">
        <span
          className="inline-flex items-center gap-1.5 text-[10.5px] uppercase font-medium text-[var(--color-text-muted)]"
          style={{ letterSpacing: 'var(--tracking-wider)' }}
        >
          Window
        </span>
        <div role="radiogroup" aria-label="Time window" className="flex items-center gap-1.5">
          {DAYS_OPTIONS.map(d => {
            const active = days === d
            return (
              <button
                key={d}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => { setDays(d); setPage(1) }}
                className="inline-flex items-center gap-1 px-2.5 py-1 text-[12.5px] rounded-full border transition-colors"
                style={{
                  background: active ? 'rgba(68,147,248,0.14)' : 'transparent',
                  borderColor: active ? 'rgba(68,147,248,0.30)' : 'var(--color-border)',
                  color: active ? '#93c5fd' : 'var(--color-text-muted)',
                }}
              >
                {d === 1 ? '24h' : `${d}d`}
              </button>
            )
          })}
        </div>
        <span className="w-px h-4" style={{ background: 'var(--color-border)' }} aria-hidden />
        <span className="text-[12px] text-[var(--color-text-muted)]">
          Status: <code className="font-mono text-[11px] bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-1.5 py-px rounded-sm">FAILED, BROKEN</code>
        </span>
        {data?.unresolved_total !== undefined && (
          <span className="ml-auto text-[12px] text-[var(--color-text-muted)]">
            {data.unresolved_total === 0
              ? 'Nothing assigned'
              : <><strong className="text-[var(--color-text)] tabular-nums">{data.unresolved_total}</strong> assigned</>}
          </span>
        )}
      </div>

      {/* Body */}
      {isLoading && !data ? (
        <div className="flex items-center justify-center py-16"><LoadingSpinner size="lg" /></div>
      ) : error ? (
        <div className="flex items-center gap-2 text-sm text-amber-300 bg-amber-900/20 border border-amber-700/30 rounded px-3 py-3">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          Couldn’t load your failures. The inbox will retry automatically.
        </div>
      ) : !data || data.items.length === 0 ? (
        <EmptyState
          title="You’re caught up"
          description={`No failed or broken tests assigned to you in the last ${
            days === 1 ? '24 hours' : `${days} days`
          }. New failures land here automatically when a test run is ingested.`}
          icon={<Inbox className="h-6 w-6" />}
        />
      ) : (
        <div className="rounded-xl border border-[var(--color-border)] overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-[var(--color-bg-secondary)]/80">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider w-2">{/* severity dot */}</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Test</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Project · Build</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Status</th>
                <th
                  className="px-4 py-3 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider"
                  title={`Failures of this test in the last ${days === 1 ? '24 hours' : `${days} days`}`}
                >
                  Failures
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Age</th>
                <th className="px-4 py-3 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider w-4">{/* open */}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--color-border)]">
              {data.items.map(item => (
                <FailureRow
                  key={item.id}
                  item={item}
                  windowDays={days}
                  onOpen={() => navigate(item.navigation_url)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data && data.pages > 1 && (
        <div className="mt-3.5">
          <Pagination page={page} pages={data.pages} total={data.total} onChange={setPage} />
        </div>
      )}
    </>
  )
}

function FailureRow({
  item,
  windowDays,
  onOpen,
}: {
  item: MyFailureItem
  windowDays: number
  onOpen: () => void
}) {
  const dot = severityDot(item.severity)
  const count = item.failure_count ?? 1
  const windowLabel = windowDays === 1 ? '24h' : `${windowDays}d`
  // Visual emphasis scales with repetition — a single failure stays neutral,
  // 2-4 is amber, 5+ is red so repeat offenders pop without screen-real-estate cost.
  const countTone =
    count >= 5
      ? 'bg-red-900/40 text-red-300'
      : count >= 2
      ? 'bg-amber-900/30 text-amber-300'
      : 'bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]'
  return (
    <tr
      onClick={onOpen}
      className="cursor-pointer hover:bg-[var(--color-bg-secondary)]/60 transition-colors"
    >
      <td className="px-4 py-3">
        <span className="inline-block h-2 w-2 rounded-full" style={{ background: dot }} aria-hidden />
      </td>
      <td className="px-4 py-3">
        <div className="text-[var(--color-text)] font-medium truncate max-w-[420px]" title={item.test_name}>
          {item.test_name}
        </div>
        <div className="text-[11px] text-[var(--color-text-muted)] truncate max-w-[420px]">
          {item.suite_name ?? '—'}
          {item.error_message ? <> · <span className="font-mono">{item.error_message}</span></> : null}
        </div>
      </td>
      <td className="px-4 py-3 text-[var(--color-text-muted)]">
        <div className="text-[var(--color-text-secondary)] truncate max-w-[200px]">{item.project_name ?? '—'}</div>
        <div className="text-[11px] tabular-nums">
          {item.build_number ? <>Build {item.build_number}</> : <span className="text-[var(--color-text-faint)]">—</span>}
        </div>
      </td>
      <td className="px-4 py-3">
        <span className={clsx('text-[11px] px-2 py-0.5 rounded font-medium', statusBadgeClass(item.status))}>
          {item.status}
        </span>
      </td>
      <td className="px-4 py-3 text-right">
        <span
          className={clsx('inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium tabular-nums', countTone)}
          title={`Failed ${count} time${count === 1 ? '' : 's'} in the last ${windowLabel}`}
        >
          × {count}
        </span>
      </td>
      <td className="px-4 py-3 text-[var(--color-text-muted)] text-xs">
        <span className="inline-flex items-center gap-1">
          <Clock className="h-3 w-3" />
          {relativeAge(item.created_at)}
        </span>
      </td>
      <td className="px-4 py-3 text-right">
        <ExternalLink className="h-3.5 w-3.5 text-[var(--color-text-faint)] inline-block" />
      </td>
    </tr>
  )
}

function statusBadgeClass(status: string): string {
  if (status === 'BROKEN') return 'bg-amber-900/40 text-amber-300'
  if (status === 'FAILED') return 'bg-red-900/40 text-red-300'
  return 'bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]'
}
