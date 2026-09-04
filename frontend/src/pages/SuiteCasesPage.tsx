import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, ArrowRightLeft, BarChart3, ExternalLink, X } from 'lucide-react'
import toast from 'react-hot-toast'
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
import { useSuiteDetail } from '@/hooks/useMetrics'
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
    <div role="dialog" aria-modal="true" aria-labelledby="move-test-case-title" className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-full max-w-md rounded-lg bg-[var(--color-bg)] p-6 ring-1 ring-[var(--color-border)]">
        <div className="flex items-center justify-between">
          <h2 id="move-test-case-title" className="text-lg font-semibold text-[var(--color-text)]">Move test case</h2>
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
            <label htmlFor="suitecase-field-0" className="text-sm text-[var(--color-text-muted)]">Target suite</label>
            <select id="suitecase-field-0"
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

interface BulkMoveModalProps {
  selectedCount: number
  selectedIds: string[]
  currentSuiteId: string
  candidates: TestSuite[]
  onClose: () => void
  onMoved: (movedCount: number, missingIds: string[]) => void
}

function BulkMoveModal({
  selectedCount,
  selectedIds,
  currentSuiteId,
  candidates,
  onClose,
  onMoved,
}: BulkMoveModalProps) {
  const targets = candidates.filter((s) => s.id !== currentSuiteId)
  const [targetId, setTargetId] = useState(targets[0]?.id ?? '')
  const [saving, setSaving] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!targetId) return
    setSaving(true)
    try {
      const result = await suitesService.bulkLinkCanonicals(targetId, selectedIds)
      const target = candidates.find((s) => s.id === targetId)
      const targetName = target?.name ?? 'suite'
      // Always surface "moved" first since that's the success metric.
      // The skipped/missing parts are diagnostic colour the user only
      // needs when the numbers don't match the selection.
      const parts = [`Moved ${result.moved} of ${selectedCount} to ${targetName}`]
      if (result.skipped_already_in_target > 0) {
        parts.push(`${result.skipped_already_in_target} already there`)
      }
      if (result.missing_ids.length > 0) {
        parts.push(`${result.missing_ids.length} not found`)
      }
      toast.success(parts.join(' · '))
      onMoved(result.moved, result.missing_ids)
      onClose()
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        'Failed to move test cases'
      toast.error(message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="move-test-cases-bulk-title" className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-full max-w-md rounded-lg bg-[var(--color-bg)] p-6 ring-1 ring-[var(--color-border)]">
        <div className="flex items-center justify-between">
          <h2 id="move-test-cases-bulk-title" className="text-lg font-semibold text-[var(--color-text)]">
            Move {selectedCount} test case{selectedCount === 1 ? '' : 's'}
          </h2>
          <button onClick={onClose} className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
            <X className="h-5 w-5" />
          </button>
        </div>
        <p className="mt-1 text-xs text-[var(--color-text-muted)]">
          All selected cases will be moved to the target suite within the same project.
        </p>
        <form onSubmit={handleSubmit} className="mt-4 space-y-4">
          <div>
            <label htmlFor="suitecase-field-1" className="text-sm text-[var(--color-text-muted)]">Target suite</label>
            <select id="suitecase-field-1"
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
              {saving ? 'Moving…' : `Move ${selectedCount}`}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

const STATUS_COLOR: Record<string, string> = {
  active: 'bg-[var(--status-passed-bg)]/10 text-[var(--status-passed)] ring-[var(--status-passed)]/30',
  deleted: 'bg-[var(--status-failed-bg)]/10 text-[var(--status-failed)] ring-[var(--status-failed)]/30',
  needs_review: 'bg-[var(--status-broken-bg)]/10 text-[var(--status-broken)] ring-[var(--status-broken)]/30',
}

function StatusPill({ status }: { status: string }) {
  const cls = STATUS_COLOR[status] || 'bg-[var(--color-bg-hover)]/10 text-[var(--color-text-muted)] ring-[var(--color-border)]/30'
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
  // Pull run-level aggregates for the same suite so the empty state can
  // distinguish "this suite has truly never ingested anything" from
  // "runs landed but per-test rows were not persisted" (the JUnit-XML
  // run-summary / live-buffer-eviction case). Mirrors the wording used
  // by ``/test-management?tab=Test+Suites`` and ``/coverage/suite``.
  //
  // Deliberately NOT release-scoped. The comparison below is against an
  // unscoped list of test cases, and "did ingestion drop the per-test rows?"
  // is not a per-release question. Letting the global release filter zero
  // these totals would retract the warning whenever the reader happened to
  // have a release selected the suite has no runs in — the filter would hide
  // an ingestion bug instead of narrowing a result.
  const { data: suiteSummary } = useSuiteDetail(suite?.name ?? null, 30, {
    releaseScoped: false,
  })
  const runLevelExecutions = suiteSummary?.summary?.total_executions ?? 0
  const runLevelUnique = suiteSummary?.summary?.unique_tests ?? 0
  const [moveTarget, setMoveTarget] = useState<CanonicalTestCase | null>(null)
  // Selection lives in a Set keyed by canonical id. Surface as an array
  // to the modal but a Set is the right shape for O(1) "is selected"
  // lookups in the row render.
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [bulkOpen, setBulkOpen] = useState(false)

  const siblingSuites = useMemo(() => {
    if (!suite || !allSuites?.items) return []
    return allSuites.items.filter((s) => s.project_id === suite.project_id)
  }, [allSuites, suite])

  if (suiteLoading) return <LoadingSpinner size="lg" />
  if (suiteError || !suite) {
    return <EmptyState title="Suite not found" description="It may have been deleted." />
  }

  const cases = casesData?.items ?? []
  // Hard cap mirrors the backend's BULK_LINK_MAX_IDS so the action bar
  // can disable / warn before the user fires a doomed request. Users
  // who hit this should split the selection.
  const BULK_LINK_MAX = 200
  const allSelected = cases.length > 0 && cases.every((c) => selectedIds.has(c.id))
  const someSelected = !allSelected && cases.some((c) => selectedIds.has(c.id))

  function toggleOne(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleAll() {
    if (allSelected) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(cases.map((c) => c.id)))
    }
  }

  function clearSelection() {
    setSelectedIds(new Set())
  }

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
        actions={
          // Pivot from catalog ("what tests live in this suite") to
          // analytics ("how have those tests performed"). The two views
          // are owned by different routes; this link is the one place
          // a QA lead can hop between them without losing context. The
          // analytics page reads ``?name=`` from the URL (see
          // ``SuiteDetailPage``) — we pass the canonical suite name
          // verbatim. Time window is picked up from the shared
          // ``timeWindowStore`` on the destination page.
          <Link
            to={`/coverage/suite?name=${encodeURIComponent(suite.name)}`}
            className="inline-flex items-center gap-1 rounded px-3 py-1.5 text-sm text-[var(--color-text-muted)] ring-1 ring-[var(--color-border)] hover:bg-[var(--color-bg-secondary)] hover:text-[var(--color-text)]"
          >
            <BarChart3 className="h-4 w-4" /> Open analytics
          </Link>
        }
      />

      {casesLoading ? (
        <LoadingSpinner />
      ) : cases.length === 0 && (runLevelExecutions > 0 || runLevelUnique > 0) ? (
        <div className="rounded border border-[var(--status-broken-bd)]/30 bg-[var(--status-broken-bg)]/5 px-4 py-3 text-sm text-[var(--status-broken)]">
          <p className="font-medium">
            {runLevelUnique || runLevelExecutions} test{(runLevelUnique || runLevelExecutions) === 1 ? '' : 's'} reported by recent runs, but per-test rows are missing for this suite.
          </p>
          <p className="mt-1 text-xs text-[var(--status-broken)]/80">
            This happens when the SDK doesn&apos;t emit <code className="font-mono">test_result</code> events,
            the upload was a run-level summary (e.g. JUnit XML with no <code className="font-mono">&lt;testcase&gt;</code> elements),
            or the live buffer evicted before persistence. Re-run the suite to populate detail rows, or
            {' '}
            <Link to={`/coverage/suite?name=${encodeURIComponent(suite.name)}`} className="underline decoration-dotted underline-offset-2 hover:text-[var(--status-broken)]">
              open analytics
            </Link>
            {' '}
            to see the run-level totals.
          </p>
        </div>
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
                {canEdit && (
                  <th className="w-10 px-4 py-2">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      ref={(el) => { if (el) el.indeterminate = someSelected }}
                      onChange={toggleAll}
                      aria-label={allSelected ? 'Deselect all' : 'Select all'}
                      className="h-4 w-4 cursor-pointer"
                    />
                  </th>
                )}
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
                // Primary row click → canonical detail page (full run
                // history). The per-run deep link is preserved as a
                // small icon button so the "I just want the latest
                // execution" path still takes one click. Rows derived
                // from the legacy fallback path don't have a real
                // canonical id mapped 1:1 (they use the TestCase.id as
                // their row id — see ``list_legacy_suite_test_cases``),
                // so we still offer the per-run jump for those.
                const detailHref = `/canonical-test-cases/${c.id}`
                const runHref = c.last_seen_run_id && c.last_seen_test_case_id
                  ? `/runs/${c.last_seen_run_id}/tests/${c.last_seen_test_case_id}`
                  : c.last_seen_run_id
                    ? `/runs/${c.last_seen_run_id}`
                    : null
                const checked = selectedIds.has(c.id)
                return (
                  <tr
                    key={c.id}
                    onClick={() => navigate(detailHref)}
                    className="cursor-pointer hover:bg-[var(--color-bg-secondary)]"
                  >
                    {canEdit && (
                      <td className="px-4 py-2">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleOne(c.id)}
                          onClick={e => e.stopPropagation()}
                          aria-label={`Select ${c.test_name}`}
                          className="h-4 w-4 cursor-pointer"
                        />
                      </td>
                    )}
                    <td className="px-4 py-2 font-medium text-[var(--color-text)]">
                      <Link
                        to={detailHref}
                        onClick={e => e.stopPropagation()}
                        className="hover:text-[var(--color-accent)] hover:underline"
                      >
                        {c.test_name}
                      </Link>
                    </td>
                    <td className="px-4 py-2 text-[var(--color-text-muted)]">{c.class_name ?? '—'}</td>
                    <td className="px-4 py-2"><StatusPill status={c.status} /></td>
                    <td className="px-4 py-2 text-[var(--color-text-muted)]">{c.source}</td>
                    <td className="px-4 py-2 text-[var(--color-text-muted)] font-mono text-xs">
                      {c.last_seen_run_id ? c.last_seen_run_id.slice(0, 8) : '—'}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <div className="inline-flex items-center gap-1">
                        {runHref && (
                          <Link
                            to={runHref}
                            onClick={e => e.stopPropagation()}
                            title="Open latest run"
                            className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-bg)] hover:text-[var(--color-text)]"
                          >
                            <ExternalLink className="h-3 w-3" /> Run
                          </Link>
                        )}
                        {canEdit && (
                          <button
                            onClick={e => { e.stopPropagation(); setMoveTarget(c) }}
                            className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-bg)] hover:text-[var(--color-text)]"
                          >
                            <ArrowRightLeft className="h-3 w-3" /> Move
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Sticky bulk-action bar — visible only when the user has at least
          one row selected. Positioned at the bottom of the viewport so
          long lists keep both the selection counter + action button
          accessible without scrolling back up. */}
      {canEdit && selectedIds.size > 0 && (
        <div
          role="region"
          aria-label="Bulk actions"
          className="fixed bottom-4 left-1/2 z-40 -translate-x-1/2 rounded-full bg-[var(--color-bg)] px-4 py-2 shadow-lg ring-1 ring-[var(--color-border)]"
        >
          <div className="flex items-center gap-3 text-sm">
            <span className="text-[var(--color-text)]">
              {selectedIds.size} selected
              {selectedIds.size > BULK_LINK_MAX && (
                <span className="ml-1 text-[var(--status-broken)]">
                  (max {BULK_LINK_MAX} per move)
                </span>
              )}
            </span>
            <button
              type="button"
              onClick={() => setBulkOpen(true)}
              disabled={selectedIds.size > BULK_LINK_MAX}
              className="inline-flex items-center gap-1 rounded bg-[var(--color-accent)] px-3 py-1 text-xs font-medium text-white disabled:opacity-50"
            >
              <ArrowRightLeft className="h-3 w-3" /> Move
            </button>
            <button
              type="button"
              onClick={clearSelection}
              className="rounded px-2 py-1 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-bg-secondary)] hover:text-[var(--color-text)]"
            >
              Clear
            </button>
          </div>
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

      {bulkOpen && (
        <BulkMoveModal
          selectedCount={selectedIds.size}
          selectedIds={Array.from(selectedIds)}
          currentSuiteId={suite.id}
          candidates={siblingSuites}
          onClose={() => setBulkOpen(false)}
          onMoved={() => {
            // Cases that resolved are gone from this suite now; missing
            // ids never resolved on the server either. Clear the whole
            // selection — re-selecting from the refreshed list is one
            // click. Keeping stale ids in selection state is the
            // worse-of-two-evils.
            clearSelection()
            refreshSuites()
          }}
        />
      )}
    </>
  )
}
