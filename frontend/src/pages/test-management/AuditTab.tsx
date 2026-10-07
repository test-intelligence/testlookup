import { useState } from 'react'
import { History, User } from 'lucide-react'
import { clsx } from 'clsx'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import Pagination from '@/components/ui/Pagination'
import { useAuditLog } from '@/hooks/useTestManagement'
import { fmtDateTime } from './format'

// ─── Tab: Audit log (under More ▾ since UX redesign P4) ──────────────────────

interface AuditTabProps { projectId: string | null }

export default function AuditTab({ projectId: _projectId }: AuditTabProps) {
  const [page, setPage] = useState(1)
  const [entityType, setEntityType] = useState('')

  const { data, isLoading } = useAuditLog({
    page,
    size: 25,
    entity_type: entityType || undefined,
  })

  const entries = data?.items ?? []

  const ACTION_COLORS: Record<string, string> = {
    created:          'text-[var(--status-passed)]',
    updated:          'text-[var(--color-text)]',
    deleted:          'text-[var(--status-failed)]',
    status_changed:   'text-[var(--status-broken)]',
    review_requested: 'text-[var(--color-purple)]',
    approved:         'text-[var(--status-passed)]',
    rejected:         'text-[var(--status-failed)]',
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <select className="input" value={entityType} onChange={e => { setEntityType(e.target.value); setPage(1) }}>
          <option value="">All Entity Types</option>
          {['test_case','test_plan','test_strategy','test_case_review'].map(t => (
            <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>
          ))}
        </select>
      </div>

      <div className="card p-0">
        {isLoading ? (
          <div className="flex items-center justify-center h-48"><LoadingSpinner size="lg" /></div>
        ) : entries.length === 0 ? (
          <EmptyState
            icon={<History className="h-10 w-10" />}
            title="No audit log entries"
            description="All changes to test cases, plans and strategies are tracked here"
          />
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr>
                    <th className="th text-left">Timestamp</th>
                    <th className="th text-left">Entity Type</th>
                    <th className="th text-left">Action</th>
                    <th className="th text-left">Actor</th>
                    <th className="th text-left">Details</th>
                  </tr>
                </thead>
                <tbody>
                  {entries.map(entry => (
                    <tr key={entry.id} className="table-row">
                      <td className="td text-[var(--color-text-muted)] whitespace-nowrap text-xs">{fmtDateTime(entry.created_at)}</td>
                      <td className="td">
                        <span className="text-xs text-[var(--color-text-muted)] capitalize">{entry.entity_type.replace(/_/g, ' ')}</span>
                      </td>
                      <td className="td">
                        <span className={clsx('text-xs font-medium capitalize', ACTION_COLORS[entry.action] ?? 'text-[var(--color-text-muted)]')}>
                          {entry.action.replace(/_/g, ' ')}
                        </span>
                      </td>
                      <td className="td">
                        <div className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]">
                          <User className="h-3.5 w-3.5" />
                          {entry.actor_name ?? entry.actor_id?.slice(0, 8) ?? 'System'}
                        </div>
                      </td>
                      <td className="td text-xs text-[var(--color-text-muted)] max-w-xs truncate">{entry.details ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {data && <Pagination page={data.page} pages={data.pages} total={data.total} onChange={setPage} />}
          </>
        )}
      </div>
    </div>
  )
}
