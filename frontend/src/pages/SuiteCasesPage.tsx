import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, ArrowRightLeft, X } from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import PageHeader from '@/components/ui/PageHeader'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { usePermissions } from '@/hooks/usePermissions'
import {
  refreshSuites,
  useSuite,
  useSuites,
  useSuiteTestCases,
} from '@/hooks/useSuites'
import { suitesService } from '@/services/suitesService'
import type { CanonicalTestCase, TestSuite } from '@/types/suites'

interface MoveModalProps {
  canonical: CanonicalTestCase
  currentSuiteId: string
  candidates: TestSuite[]
  onClose: () => void
  onMoved: () => void
}

function MoveModal({ canonical, currentSuiteId, candidates, onClose, onMoved }: MoveModalProps) {
  const targets = candidates.filter((s) => s.id !== currentSuiteId)
  const [targetId, setTargetId] = useState(targets[0]?.id ?? '')
  const [saving, setSaving] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!targetId) return
    setSaving(true)
    try {
      await suitesService.linkCanonicalToSuite(canonical.id, targetId)
      const target = candidates.find((s) => s.id === targetId)
      toast.success(`Moved "${canonical.test_name}" to ${target?.name ?? 'suite'}`)
      onMoved()
      onClose()
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        'Failed to move test case'
      toast.error(message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-full max-w-md rounded-lg bg-[var(--color-bg)] p-6 ring-1 ring-[var(--color-border)]">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-[var(--color-text)]">Move test case</h2>
          <button onClick={onClose} className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
            <X className="h-5 w-5" />
          </button>
        </div>
        <p className="mt-1 text-xs text-[var(--color-text-muted)]">
          {canonical.class_name ? `${canonical.class_name} :: ` : ''}
          {canonical.test_name}
        </p>
        <form onSubmit={handleSubmit} className="mt-4 space-y-4">
          <div>
            <label className="text-sm text-[var(--color-text-muted)]">Target suite</label>
            <select
              value={targetId}
              onChange={(e) => setTargetId(e.target.value)}
              required
              className="mt-1 w-full rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-2 text-sm text-[var(--color-text)]"
            >
              {targets.length === 0 ? (
                <option value="">No other suites available</option>
              ) : (
                targets.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                    {s.is_default ? ' (default)' : ''}
                  </option>
                ))
              )}
            </select>
          </div>
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded px-3 py-1.5 text-sm text-[var(--color-text-muted)] hover:bg-[var(--color-bg-secondary)]"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving || !targetId}
              className="rounded bg-[var(--color-accent)] px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              {saving ? 'Moving…' : 'Move'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

const STATUS_COLOR: Record<string, string> = {
  active: 'bg-emerald-500/10 text-emerald-400 ring-emerald-500/30',
  deleted: 'bg-red-500/10 text-red-400 ring-red-500/30',
  needs_review: 'bg-amber-500/10 text-amber-400 ring-amber-500/30',
}

function StatusPill({ status }: { status: string }) {
  const cls = STATUS_COLOR[status] || 'bg-neutral-500/10 text-[var(--color-text-muted)] ring-neutral-500/30'
  return (
    <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium uppercase ring-1 ring-inset ${cls}`}>
      {status}
    </span>
  )
}

export default function SuiteCasesPage() {
  const { suiteId } = useParams<{ suiteId: string }>()
  const navigate = useNavigate()
  const { hasRole } = usePermissions()
  const canEdit = hasRole('QA_ENGINEER')

  const { data: suite, isLoading: suiteLoading, error: suiteError } = useSuite(suiteId)
  const { data: casesData, isLoading: casesLoading } = useSuiteTestCases(suiteId)
  const { data: allSuites } = useSuites()
  const [moveTarget, setMoveTarget] = useState<CanonicalTestCase | null>(null)

  const siblingSuites = useMemo(() => {
    if (!suite || !allSuites?.items) return []
    return allSuites.items.filter((s) => s.project_id === suite.project_id)
  }, [allSuites, suite])

  if (suiteLoading) return <LoadingSpinner size="lg" />
  if (suiteError || !suite) {
    return <EmptyState title="Suite not found" description="It may have been deleted." />
  }

  const cases = casesData?.items ?? []

  return (
    <>
      <button
        type="button"
        onClick={() => navigate('/suites')}
        className="mb-3 inline-flex items-center gap-1 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
      >
        <ArrowLeft className="h-4 w-4" /> All suites
      </button>

      <PageHeader
        title={suite.is_default ? `${suite.name} (default)` : suite.name}
        subtitle={suite.description ?? 'No description'}
      />

      {casesLoading ? (
        <LoadingSpinner />
      ) : cases.length === 0 ? (
        <EmptyState
          title="No test cases in this suite"
          description="Ingest a run that tags tests with this suite name, or move existing cases here from another suite."
        />
      ) : (
        <div className="overflow-hidden rounded-lg ring-1 ring-[var(--color-border)]">
          <table className="w-full divide-y divide-[var(--color-border)] text-sm">
            <thead className="bg-[var(--color-bg-secondary)] text-left text-xs uppercase text-[var(--color-text-muted)]">
              <tr>
                <th className="px-4 py-2 font-medium">Test</th>
                <th className="px-4 py-2 font-medium">Class</th>
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-4 py-2 font-medium">Source</th>
                <th className="px-4 py-2 font-medium">Last seen</th>
                <th className="px-4 py-2 font-medium" />
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--color-border)]">
              {cases.map((c) => {
                // Deep-link target: prefer the per-run test-detail page when
                // we resolved the matching TestCase.id; otherwise fall back
                // to the run detail page so the user still lands close to
                // this test. Rows with no last_seen_run_id (manual cases
                // never executed) aren't clickable.
                const href = c.last_seen_run_id && c.last_seen_test_case_id
                  ? `/runs/${c.last_seen_run_id}/tests/${c.last_seen_test_case_id}`
                  : c.last_seen_run_id
                    ? `/runs/${c.last_seen_run_id}`
                    : null
                return (
                  <tr
                    key={c.id}
                    onClick={() => { if (href) navigate(href) }}
                    className={clsx(
                      'hover:bg-[var(--color-bg-secondary)]',
                      href && 'cursor-pointer',
                    )}
                  >
                    <td className="px-4 py-2 font-medium text-[var(--color-text)]">
                      {href ? (
                        <Link
                          to={href}
                          onClick={e => e.stopPropagation()}
                          className="hover:text-[var(--color-accent)] hover:underline"
                        >
                          {c.test_name}
                        </Link>
                      ) : (
                        c.test_name
                      )}
                    </td>
                    <td className="px-4 py-2 text-[var(--color-text-muted)]">{c.class_name ?? '—'}</td>
                    <td className="px-4 py-2"><StatusPill status={c.status} /></td>
                    <td className="px-4 py-2 text-[var(--color-text-muted)]">{c.source}</td>
                    <td className="px-4 py-2 text-[var(--color-text-muted)] font-mono text-xs">
                      {c.last_seen_run_id ? c.last_seen_run_id.slice(0, 8) : '—'}
                    </td>
                    <td className="px-4 py-2 text-right">
                      {canEdit && (
                        <button
                          onClick={e => { e.stopPropagation(); setMoveTarget(c) }}
                          className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-bg)] hover:text-[var(--color-text)]"
                        >
                          <ArrowRightLeft className="h-3 w-3" /> Move
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {moveTarget && (
        <MoveModal
          canonical={moveTarget}
          currentSuiteId={suite.id}
          candidates={siblingSuites}
          onClose={() => setMoveTarget(null)}
          onMoved={() => refreshSuites()}
        />
      )}
    </>
  )
}
