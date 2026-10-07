/**
 * The suite page, `/suites/:suiteId` (UX redesign P4: one page for one suite).
 *
 * It was two pages keyed differently — this catalog (by id) and the analytics
 * at `/coverage/suite?name=` (by name) — that a reader had to hop between to
 * answer "how is this suite doing, and which of its tests?". Now:
 *
 *   header (suite name, ?, ⋯) with the tabs Tests · Runs · Charts
 *   toolbar: the global window
 *   KPI strip: the window's tests, executions, pass rate, failed, avg duration
 *   Tests (default): the catalog, each test with its pass rate, executions,
 *     duration, latest result, flaky flag and last error — Move and the bulk
 *     move kept (`suite/SuiteTestsTable`)
 *   Runs: the suite's recent runs with this suite's counts in each
 *   Charts: the run history, per-day pass rate, test x run heatmap and test
 *     scatter — the former `/coverage/suite` charts (`SuiteChartsPanel`,
 *     lazy: the Tests tab loads no chart code)
 *
 * `/coverage/suite?name=X` redirects here (`?tab=charts`), keeping its other
 * parameters: an old link's `?days=` becomes the window (`useSuiteWindow`).
 */
import { Suspense, useEffect, useMemo, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { Activity, ArrowLeft, ArrowRightLeft, CheckCircle2, Clock, Layers, X, XCircle } from 'lucide-react'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import KpiStrip from '@/components/ui/KpiStrip'
import MetricCard from '@/components/ui/MetricCard'
import Tabs, { type TabItem } from '@/components/ui/Tabs'
import WindowPicker, { DEFAULT_WINDOW_OPTIONS } from '@/components/ui/WindowPicker'
import { useTabParam } from '@/components/ui/useTabParam'
import { helpTopicParam } from '@/components/help/helpTopics'
import { usePermissions } from '@/hooks/usePermissions'
import {
  refreshSuites,
  useSuite,
  useSuites,
  useSuiteTestCases,
} from '@/hooks/useSuites'
import { useSuiteDetail } from '@/hooks/useMetrics'
import { suitesService } from '@/services/suitesService'
import { snapToAllowed, useTimeWindowStore } from '@/store/timeWindowStore'
import type { SuiteDetailTestCase } from '@/types/analytics'
import type { CanonicalTestCase, TestSuite } from '@/types/suites'
import { lazyWithRetry } from '@/utils/lazyWithRetry'
import SuiteTestsTable from './suite/SuiteTestsTable'
import { RecentRunsTable } from './suite/SuiteParts'
import { formatSuiteDuration } from './suite/format'

// The Charts tab's chart code (the kit's frames, the Wave 3 composite) loads
// when the tab is first opened, not with the catalog.
const SuiteChartsPanel = lazyWithRetry<{ suiteName: string; days: number }>(() =>
  import('./SuiteDetailPage').then((m) => ({ default: m.SuiteChartsPanel })),
)

const SUITE_TABS = ['tests', 'runs', 'charts'] as const
type SuiteTab = (typeof SUITE_TABS)[number]

const TAB_LABEL: Record<SuiteTab, string> = { tests: 'Tests', runs: 'Runs', charts: 'Charts' }

/**
 * The analytics' per-test rows are capped (`analytics_service.get_suite_detail`,
 * `LIMIT 200`, most failures first): at the cap, a catalog row without a match
 * may have run but not made the cut, and the table says so.
 */
const SUITE_DETAIL_ROW_CAP = 200

/**
 * The page's window: the global one (`WindowPicker`). A link from the former
 * `/coverage/suite` page may carry `?days=`; a valid one is adopted into the
 * global window once and removed from the URL, so the picker and the data
 * never disagree (that page's URL pinned the window over the picker, the
 * 2026-05-19 bug). An invalid one (`?days=365`) is dropped.
 */
function useSuiteWindow(): number {
  const [params, setParams] = useSearchParams()
  const stored = useTimeWindowStore((s) => s.days)
  const setStored = useTimeWindowStore((s) => s.setDays)
  const raw = params.get('days')
  const fromUrl = raw !== null && (DEFAULT_WINDOW_OPTIONS as readonly number[]).includes(Number(raw)) ? Number(raw) : null
  useEffect(() => {
    if (raw === null) return
    if (fromUrl !== null) setStored(fromUrl)
    setParams(
      (current) => {
        const out = new URLSearchParams(current)
        out.delete('days')
        return out
      },
      { replace: true },
    )
  }, [raw, fromUrl, setStored, setParams])
  return fromUrl ?? snapToAllowed(stored, DEFAULT_WINDOW_OPTIONS)
}

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

export default function SuiteCasesPage() {
  const { suiteId } = useParams<{ suiteId: string }>()
  const { hasRole } = usePermissions()
  const canEdit = hasRole('QA_ENGINEER')
  const [tab, setTab] = useTabParam(SUITE_TABS, 'tests')
  const days = useSuiteWindow()

  const { data: suite, isLoading: suiteLoading, error: suiteError } = useSuite(suiteId)
  const { data: casesData, isLoading: casesLoading } = useSuiteTestCases(suiteId)
  const { data: allSuites } = useSuites()
  // The window's analytics for this suite (release-scoped like every result
  // on the page): the KPI strip, the Tests tab's per-test columns and the
  // Runs tab read the one response. The Charts tab asks the same key, so SWR
  // sends it once.
  const { data: detail, isLoading: detailLoading, error: detailError } = useSuiteDetail(suite?.name ?? null, days)
  const cases = useMemo(() => casesData?.items ?? [], [casesData])
  const catalogEmpty = casesData !== undefined && cases.length === 0
  // Run-level aggregates for the same suite so an EMPTY catalog can tell
  // "this suite has truly never ingested anything" from "runs landed but
  // per-test rows were not persisted" (the JUnit-XML run-summary /
  // live-buffer-eviction case). Mirrors the wording used by
  // ``/test-management?tab=Test+Suites``. Asked only when the catalog is
  // empty: a populated catalog needs no diagnosis.
  //
  // Deliberately NOT release-scoped. The comparison below is against an
  // unscoped list of test cases, and "did ingestion drop the per-test rows?"
  // is not a per-release question. Letting the global release filter zero
  // these totals would retract the warning whenever the reader happened to
  // have a release selected the suite has no runs in — the filter would hide
  // an ingestion bug instead of narrowing a result.
  const { data: suiteSummary } = useSuiteDetail(catalogEmpty ? suite?.name ?? null : null, 30, {
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

  const stats = useMemo(() => {
    const byFingerprint = new Map<string, SuiteDetailTestCase>()
    for (const row of detail?.test_cases ?? []) byFingerprint.set(row.test_fingerprint, row)
    return byFingerprint
  }, [detail])

  if (suiteLoading) return <LoadingSpinner size="lg" />
  if (suiteError || !suite) {
    return <EmptyState title="Suite not found" description="It may have been deleted." />
  }

  const summary = detail?.summary
  const recentRuns = detail?.recent_runs ?? []
  const windowHasRuns = (summary?.total_executions ?? 0) > 0 || recentRuns.length > 0
  const statsCapped = (detail?.test_cases?.length ?? 0) >= SUITE_DETAIL_ROW_CAP
  const noRunTitle = `Not run in the last ${days} days`

  // Hard cap mirrors the backend's BULK_LINK_MAX_IDS so the action bar
  // can disable / warn before the user fires a doomed request. Users
  // who hit this should split the selection.
  const BULK_LINK_MAX = 200

  function toggleOne(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleAll() {
    const allSelected = cases.length > 0 && cases.every((c) => selectedIds.has(c.id))
    if (allSelected) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(cases.map((c) => c.id)))
    }
  }

  function clearSelection() {
    setSelectedIds(new Set())
  }

  const tabs: TabItem<SuiteTab>[] = [
    { id: 'tests', label: TAB_LABEL.tests, count: casesData ? cases.length : undefined },
    { id: 'runs', label: TAB_LABEL.runs, count: detail ? recentRuns.length : undefined },
    { id: 'charts', label: TAB_LABEL.charts },
  ]

  return (
    <div className="space-y-4">
      <PageHeader
        compact
        title={suite.is_default ? `${suite.name} (default)` : suite.name}
        subtitle={suite.description ?? undefined}
        helpTopic={helpTopicParam('/suites')}
        overflow={[{ label: 'All suites', href: '/suites', icon: <ArrowLeft className="h-4 w-4" /> }]}
        tabs={<Tabs items={tabs} value={tab} onChange={setTab} ariaLabel="Suite sections" />}
      />

      {/* One toolbar row: the window every tab reads (the page is one suite, so no suite filter). */}
      <div data-toolbar="" className="flex flex-wrap items-center gap-3">
        <WindowPicker />
      </div>

      {/* The window's totals for the suite. Nothing ran in the window: no
          strip (a row of zeros would read as a measured 0 %). */}
      {(detailLoading || windowHasRuns) && (
        <KpiStrip>
          {[
            <MetricCard
              key="tests"
              compact
              title="Tests run"
              hint="Unique tests that ran in the window (the Tests tab counts the whole catalog)"
              icon={<Layers className="h-4 w-4" />}
              loading={detailLoading}
              metric={{ value: summary?.unique_tests ?? 0 }}
            />,
            <MetricCard
              key="executions"
              compact
              title="Executions"
              icon={<Activity className="h-4 w-4" />}
              loading={detailLoading}
              metric={{ value: summary?.total_executions ?? 0 }}
            />,
            <MetricCard
              key="pass-rate"
              compact
              title="Pass rate"
              hint="Passed over evaluated executions (skipped excluded)"
              icon={<CheckCircle2 className="h-4 w-4" />}
              loading={detailLoading}
              metric={{ value: summary?.pass_rate != null ? `${Number(summary.pass_rate).toFixed(1)}%` : '—' }}
            />,
            <MetricCard
              key="failed"
              compact
              title="Failed"
              hint="Executions that failed or broke"
              icon={<XCircle className="h-4 w-4" />}
              loading={detailLoading}
              metric={{ value: summary?.failed ?? 0 }}
            />,
            <MetricCard
              key="duration"
              compact
              title="Avg duration"
              icon={<Clock className="h-4 w-4" />}
              loading={detailLoading}
              metric={{ value: formatSuiteDuration(summary?.avg_duration_ms) }}
            />,
          ]}
        </KpiStrip>
      )}

      <div role="tabpanel" aria-label={TAB_LABEL[tab]} data-tab-panel={tab} className="min-w-0">
        {tab === 'tests' && (
          casesLoading ? (
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
                <button
                  type="button"
                  onClick={() => setTab('charts')}
                  className="underline decoration-dotted underline-offset-2 hover:text-[var(--status-broken)]"
                >
                  open the Charts tab
                </button>
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
            <div className="space-y-2">
              <SuiteTestsTable
                cases={cases}
                stats={stats}
                noRunTitle={noRunTitle}
                canEdit={canEdit}
                selectedIds={selectedIds}
                onToggleOne={toggleOne}
                onToggleAll={toggleAll}
                onMove={setMoveTarget}
              />
              {/* The run columns are the window's analytics: say when they
                  could not be read, or cover only part of the catalog. */}
              {detailError ? (
                <p data-stats-note="" className="text-xs text-[var(--status-failed)]">
                  Run statistics could not be loaded: the pass rate, executions, duration and latest result columns are empty.
                </p>
              ) : statsCapped ? (
                <p data-stats-note="" className="text-xs text-[var(--color-text-muted)]">
                  Run statistics cover the {SUITE_DETAIL_ROW_CAP} tests with the most failures in the last {days} days.
                </p>
              ) : null}
            </div>
          )
        )}

        {tab === 'runs' && (
          detailLoading ? (
            <LoadingSpinner />
          ) : detailError ? (
            <EmptyState title="Failed to load the suite's runs" description="Check the console for errors or try again" />
          ) : recentRuns.length === 0 ? (
            <EmptyState title="No runs in this window" description={`No run of this suite in the last ${days} days.`} />
          ) : (
            <RecentRunsTable runs={recentRuns} primary />
          )
        )}

        {tab === 'charts' && (
          <Suspense fallback={<div className="flex h-64 items-center justify-center"><LoadingSpinner size="lg" /></div>}>
            <SuiteChartsPanel suiteName={suite.name} days={days} />
          </Suspense>
        )}
      </div>

      {/* Sticky bulk-action bar — visible only when the user has at least
          one row selected (on the Tests tab, where the rows are).
          Positioned at the bottom of the viewport so long lists keep both
          the selection counter + action button accessible without
          scrolling back up. */}
      {canEdit && tab === 'tests' && selectedIds.size > 0 && (
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
    </div>
  )
}
