/**
 * The Run page's **Tests** tab (UX redesign P4, the default tab): the run's
 * counts as status chips that filter the list, then the test table — the
 * page's primary content (`data-primary`) — with failed and broken tests
 * first.
 *
 * The API already lists a run's tests by status, then name
 * (`runs_service.list_run_test_cases`), which puts BROKEN and FAILED ahead of
 * PASSED and SKIPPED; the table used to re-sort every page by name on
 * arrival, burying the failures among the passes. Unsorted, the page keeps a
 * stable failures-first order (also for the live-buffer fallback, whose
 * order is the stream's); a column header still sorts by that column.
 *
 * A row opens its test in a side panel beside the list (plan P4 item 2): the
 * test case body (`TestCaseBody`, compact), "Open full page", and the
 * previous / next failure on this page, so a triager walks the failures
 * without losing the list. Ctrl / ⌘-click opens the full page instead.
 */
import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ChevronLeft, ChevronRight, ExternalLink, ListTree, Loader2, RotateCcw } from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import { appMutate } from '@/utils/swrCacheMutate'
import StatusBadge from '@/components/ui/StatusBadge'
import SortableHeader from '@/components/ui/SortableHeader'
import Pagination from '@/components/ui/Pagination'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import SidePanel from '@/components/ui/SidePanel'
import TestCaseBody from '@/pages/testCase/TestCaseBody'
import { KindBadgeWithEvidence } from '@/components/failures/KindEvidence'
import AttributionVerdictBadge from '@/components/failures/AttributionVerdictBadge'
import { useRunAttribution, useTestCases } from '@/hooks/useRuns'
import { useTableSort } from '@/hooks/useTableSort'
import { api } from '@/services/api'
import type { AttributionItem } from '@/types/attribution'
import type { TestRun } from '@/types/runs'
import { formatDuration } from '@/utils/formatters'
import { retryAttempts } from '@/utils/retryEvidence'

interface TestCase {
  id: string
  test_name: string
  class_name?: string
  suite_name?: string
  status: string
  duration_ms?: number
  failure_category?: string
  // AI-classified failure kind (US-9.1 computed field on TestCaseSummary —
  // included in the list response, so the badge costs no extra call; the
  // AI-4 evidence popover fetches on demand by test_case_id when opened).
  failure_kind?: string | null
  // Phase 1 granular steps: # of top-level steps captured for this test's
  // latest-run snapshot. null when no parser emitted a step tree for this
  // producer; 0 when the parser ran but the test had no steps.
  step_count?: number | null
  // Retry evidence, persisted at ingest and previously never surfaced.
  // ``retry_count`` is the number of RETRIES, so attempts = retries + 1.
  retry_count?: number | null
  is_flaky_run?: boolean | null
}

const PAGE_SIZE = 25

/** Failed and broken first; everything else keeps its order (a stable sort). */
const isFailing = (status: string) => status === 'FAILED' || status === 'BROKEN'

/** The run's counts as filter chips. */
interface StatusChip {
  /** The `status` filter value; '' is All. */
  value: string
  count: number
  label: string
  hue: string
}

/**
 * Every persisted status gets a bucket, so the chips reconcile with
 * `total_tests` (a run of 2 pass / 2 fail / 1 skip / 1 BROKEN once rendered
 * "2 passed, 2 failed, 1 skipped / 6 total": 5 of 6 accounted for, the
 * infrastructure error invisible). Broken and unrecognised show only when
 * non-zero, so the common all-green run stays uncluttered.
 */
function statusChips(run: TestRun): StatusChip[] {
  const chips: StatusChip[] = [
    { value: 'FAILED', count: run.failed_tests ?? 0, label: 'failed', hue: 'var(--status-failed)' },
  ]
  if ((run.broken_tests ?? 0) > 0) {
    chips.push({ value: 'BROKEN', count: run.broken_tests ?? 0, label: 'broken', hue: 'var(--status-broken)' })
  }
  chips.push(
    { value: 'PASSED', count: run.passed_tests ?? 0, label: 'passed', hue: 'var(--status-passed)' },
    { value: 'SKIPPED', count: run.skipped_tests ?? 0, label: 'skipped', hue: 'var(--color-text-muted)' },
  )
  return chips
}

const CHIP = 'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12.5px] font-medium tabular-nums transition-colors'

export default function RunTestsTab({
  runId,
  run,
  statusFilter,
  suiteFilter,
  page,
  onStatusFilter,
  onSuiteFilter,
  onPage,
}: {
  runId: string
  run: TestRun | undefined
  statusFilter: string
  suiteFilter: string
  page: number
  onStatusFilter: (status: string) => void
  onSuiteFilter: (suite: string) => void
  onPage: (page: number) => void
}) {
  const navigate = useNavigate()
  const [recoveringLive, setRecoveringLive] = useState(false)
  /** The test open in the side panel (a row of this page). */
  const [openTestId, setOpenTestId] = useState<string | null>(null)

  // Roadmap Phase 4 verdicts, keyed by test case for O(1) lookup in the row
  // renderer. Deliberately non-blocking: the table renders with or without
  // this, so a slow or failed attribution fetch degrades to the previous
  // behaviour rather than holding up the failure list.
  const { data: attributionData } = useRunAttribution(runId)
  const attributionByTestCase = useMemo(() => {
    const map: Record<string, AttributionItem> = {}
    for (const item of attributionData?.items ?? []) {
      map[item.test_case_id] = item
    }
    return map
  }, [attributionData])

  const { data, isLoading, error } = useTestCases(runId, {
    page, size: PAGE_SIZE,
    ...(statusFilter && { status: statusFilter }),
    ...(suiteFilter && { suite: suiteFilter }),
  })
  const tcItems = (data?.items ?? []) as TestCase[]
  const { sorted, sortKey, sortDir, toggleSort } = useTableSort(tcItems, '', 'asc')
  const rows = useMemo(
    () => (sortKey ? sorted : [...sorted].sort((a, b) => Number(isFailing(b.status)) - Number(isFailing(a.status)))),
    [sorted, sortKey],
  )
  const openTest = rows.find((tc) => tc.id === openTestId) ?? null
  // The failures on this page, in the table's order: what Previous / Next walk.
  const failingIds = rows.filter((tc) => isFailing(tc.status)).map((tc) => tc.id)
  const failingIndex = openTestId ? failingIds.indexOf(openTestId) : -1
  // From a passing test, Next goes to the first failure.
  const prevFailure = failingIndex > 0 ? failingIds[failingIndex - 1] : null
  const nextFailure = failingIndex === -1 ? (failingIds[0] ?? null) : (failingIds[failingIndex + 1] ?? null)

  async function handleRecoverLive() {
    if (recoveringLive) return
    setRecoveringLive(true)
    try {
      const resp = await api.post<{ queued: boolean; buffered_events: number }>(
        `/api/v1/runs/${runId}/recover-live`,
      )
      toast.success(
        `Replaying ${resp.data.buffered_events} buffered events. Refreshing shortly…`,
        { icon: '↻', duration: 5000 },
      )
      // Persist task runs async on the ingestion worker. Give it a moment
      // then revalidate the SWR test-cases cache so the table populates
      // without a full page reload.
      setTimeout(() => { appMutate(['test-cases', runId, { page, size: PAGE_SIZE }]) }, 2500)
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error)?.message ??
        'Failed to queue recovery'
      toast.error(detail)
    } finally {
      setRecoveringLive(false)
    }
  }

  return (
    <div className="space-y-3">
      {/* The run's counts, failures first; each chip filters the table. */}
      <div className="flex items-center gap-2 flex-wrap" role="group" aria-label="Filter by status">
        {run && (
          <>
            <button
              type="button"
              onClick={() => onStatusFilter('')}
              aria-pressed={statusFilter === ''}
              className={clsx(
                CHIP,
                statusFilter === ''
                  ? 'border-[var(--color-accent)] bg-[var(--color-accent-bg-soft)] text-[var(--color-text)]'
                  : 'border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]',
              )}
            >
              <span>All {run.total_tests}</span>
            </button>
            {statusChips(run).map((chip) => {
              const active = statusFilter === chip.value
              return (
                <button
                  key={chip.value}
                  type="button"
                  data-status-chip={chip.value}
                  onClick={() => onStatusFilter(active ? '' : chip.value)}
                  aria-pressed={active}
                  className={clsx(
                    CHIP,
                    active
                      ? 'border-[var(--color-accent)] bg-[var(--color-accent-bg-soft)] text-[var(--color-text)]'
                      : 'border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]',
                  )}
                >
                  <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: chip.hue }} />
                  <span>{chip.count} {chip.label}</span>
                </button>
              )
            })}
            {(run.unknown_tests ?? 0) > 0 && (
              <span
                className={clsx(CHIP, 'border-dashed border-[var(--color-border)] text-[var(--status-broken)]')}
                title="Reported status was outside PASSED/FAILED/SKIPPED/BROKEN"
              >
                <span>{run.unknown_tests} unrecognised</span>
              </span>
            )}
          </>
        )}
        <input
          type="text"
          aria-label="Filter by suite"
          placeholder="Filter by suite…"
          className="input w-48 h-8 ml-auto"
          value={suiteFilter}
          onChange={e => onSuiteFilter(e.target.value)}
        />
      </div>

      {/* The test table: the page's primary content. */}
      <section data-primary="" aria-label="Tests" className="card p-0 overflow-hidden">
        {isLoading ? (
          <div className="flex items-center justify-center py-20"><LoadingSpinner size="lg" /></div>
        ) : error ? (
          <div className="flex items-center justify-center py-16 text-[var(--status-failed)] text-sm gap-2">
            <span>Failed to load test cases — {(error as Error)?.message ?? 'server error'}</span>
          </div>
        ) : !data?.items?.length ? (
          <div className="flex flex-col items-center justify-center py-16 text-[var(--color-text-muted)] text-sm gap-3 px-6 text-center max-w-2xl mx-auto">
            {(() => {
              const isLive = run?.trigger_source === 'live_stream'
              const totalReported = run?.total_tests ?? 0
              const hasAggregates = totalReported > 0
              const hasActiveFilter = Boolean(statusFilter || suiteFilter)

              if (hasActiveFilter) {
                return (
                  <>
                    <p>No test cases match the current filters{statusFilter && ` (status: ${statusFilter})`}{suiteFilter && ` (suite: ${suiteFilter})`}.</p>
                    <button
                      onClick={() => { onStatusFilter(''); onSuiteFilter('') }}
                      className="text-[var(--color-text)] hover:text-[var(--color-text-secondary)] text-xs underline"
                    >
                      Clear filters
                    </button>
                  </>
                )
              }

              if (isLive && hasAggregates) {
                // The run record carries aggregate counts from the live state
                // hash (HINCRBY) but persist_live_session didn't materialise
                // per-test rows — usually the persistence task crashed after
                // setting its dedup key (so retries silently skipped) while
                // the Redis event buffer (25h TTL) still has the data. The
                // ``Recover from buffer`` button below triggers a fresh
                // persist task that idempotency-checks based on actual
                // TestCase row count rather than a stuck dedup flag.
                // Migration 0086 also archives the events on the TestRun
                // row at session-close time so the 15-day fallback path
                // works even after the 25-hour Redis TTL has lapsed.
                return (
                  <>
                    <p className="text-[var(--color-text)]">
                      This live run reported <strong>{totalReported}</strong> test{totalReported === 1 ? '' : 's'}
                      {' '}({run?.passed_tests ?? 0} passed, {run?.failed_tests ?? 0} failed
                      {(run?.skipped_tests ?? 0) > 0 && `, ${run?.skipped_tests} skipped`}
                      {(run?.broken_tests ?? 0) > 0 && `, ${run?.broken_tests} broken`}
                      {(run?.unknown_tests ?? 0) > 0 && `, ${run?.unknown_tests} unrecognised status`}),
                      but per-test details aren't loaded yet.
                    </p>
                    <p className="text-xs">
                      Buffered events are held in Redis for 25 hours after a run
                      closes, and a durable copy is archived on the run for{' '}
                      <strong>up to 15 days</strong>. If persistence didn&apos;t
                      finish on the first try (worker crash, transient error),
                      use the button below to replay from whichever source is
                      still available. After 15 days, re-run the suite or
                      re-ingest the results as a file upload.
                    </p>
                    <div className="flex items-center gap-3 mt-1">
                      <button
                        type="button"
                        disabled={recoveringLive}
                        onClick={() => handleRecoverLive()}
                        className="btn-primary text-xs flex items-center gap-1.5 disabled:opacity-50"
                      >
                        {recoveringLive
                          ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          : <RotateCcw className="h-3.5 w-3.5" />}
                        {recoveringLive ? 'Replaying…' : 'Recover from buffer'}
                      </button>
                      <button
                        type="button"
                        onClick={() => window.location.reload()}
                        className="text-[var(--color-text-muted)] hover:text-[var(--color-text)] text-xs underline"
                      >
                        Refresh
                      </button>
                    </div>
                  </>
                )
              }

              if (isLive) {
                return (
                  <>
                    <p>Live test results are still being processed. This usually takes a few seconds after the session closes.</p>
                    <button
                      onClick={() => window.location.reload()}
                      className="text-[var(--color-text)] hover:text-[var(--color-text-secondary)] text-xs underline"
                    >
                      Refresh page
                    </button>
                  </>
                )
              }

              return <p>No test cases found for this run.</p>
            })()}
          </div>
        ) : (
          <>
            <table className="w-full">
              <thead className="border-b border-[var(--color-border)]">
                <tr>
                  <SortableHeader label="Test Name" sortKey="test_name" currentKey={sortKey} dir={sortDir} onSort={toggleSort} />
                  <SortableHeader label="Suite" sortKey="suite_name" currentKey={sortKey} dir={sortDir} onSort={toggleSort} />
                  <SortableHeader label="Status" sortKey="status" currentKey={sortKey} dir={sortDir} onSort={toggleSort} />
                  <SortableHeader label="Duration" sortKey="duration_ms" currentKey={sortKey} dir={sortDir} onSort={toggleSort} />
                  <SortableHeader label="Category" sortKey="failure_category" currentKey={sortKey} dir={sortDir} onSort={toggleSort} />
                  <th className="th" />
                </tr>
              </thead>
              <tbody>
                {rows.map((tc) => (
                  <tr
                    key={tc.id}
                    className={clsx('table-row', tc.id === openTestId && 'bg-[var(--color-bg-secondary)]')}
                    aria-selected={tc.id === openTestId || undefined}
                    data-test-row={tc.id}
                    onClick={(e) => {
                      if (e.metaKey || e.ctrlKey) navigate(`/runs/${runId}/tests/${tc.id}`)
                      else setOpenTestId(tc.id)
                    }}
                  >
                    <td className="td max-w-[280px]">
                      <div className="flex items-center gap-2">
                        <p className="truncate text-[var(--color-text)] text-sm font-medium">{tc.test_name}</p>
                        {typeof tc.step_count === 'number' && tc.step_count > 0 && (
                          <span
                            title={`${tc.step_count} captured step${tc.step_count === 1 ? '' : 's'} — open to view the step tree`}
                            className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] flex-shrink-0 tabular-nums"
                          >
                            <ListTree className="h-3 w-3" />
                            {tc.step_count}
                          </span>
                        )}
                      </div>
                      {tc.class_name && <p className="truncate text-xs text-[var(--color-text-muted)] font-mono mt-0.5">{tc.class_name}</p>}
                    </td>
                    <td className="td text-[var(--color-text-muted)] text-sm truncate max-w-[160px]">{tc.suite_name ?? '—'}</td>
                    <td className="td">
                      <span className="inline-flex items-center gap-1.5">
                        <StatusBadge status={tc.status} />
                        {/* A retry is evidence, not a way to make the build
                            green. A bare tick on a test that only passed on
                            attempt 3 erases the one fact that mattered. */}
                        {retryAttempts(tc) > 1 && (
                          <span
                            title={`Passed on attempt ${retryAttempts(tc)} — this test needed ${retryAttempts(tc) - 1} retry${retryAttempts(tc) === 2 ? '' : 'ies'} to reach its final status`}
                            className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium tabular-nums flex-shrink-0 bg-[var(--color-bg-secondary)] text-[var(--status-flaky)]"
                          >
                            <RotateCcw className="h-3 w-3" />
                            attempt {retryAttempts(tc)}
                          </span>
                        )}
                      </span>
                    </td>
                    <td className="td text-[var(--color-text-muted)]">{formatDuration(tc.duration_ms)}</td>
                    <td className="td">
                      <span className="inline-flex items-center gap-2">
                        {/* Roadmap Phase 4: lead a failure with what it appears
                            to BE. A raw failure list is mostly noise — ~84% of
                            pass->fail transitions involve a flaky test — and a
                            verdict here is annotation, never suppression: the
                            row renders identically with or without it. */}
                        {attributionByTestCase[tc.id] && (
                          <AttributionVerdictBadge
                            attribution={attributionByTestCase[tc.id]}
                            compact
                          />
                        )}
                        {tc.failure_category && (
                          <span className="text-xs text-[var(--color-text-muted)]">{tc.failure_category.replace('_', ' ')}</span>
                        )}
                        {tc.failure_kind && (tc.status === 'FAILED' || tc.status === 'BROKEN') && (
                          <KindBadgeWithEvidence kind={tc.failure_kind} testCaseId={tc.id} compact />
                        )}
                      </span>
                    </td>
                    <td className="td text-[var(--color-text-faint)]">
                      <ChevronRight className="h-4 w-4" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data && <Pagination page={page} pages={data.pages} total={data.total} onChange={onPage} />}
          </>
        )}
      </section>

      <SidePanel
        open={openTest !== null}
        onClose={() => setOpenTestId(null)}
        title={openTest?.test_name ?? 'Test'}
        width={640}
        footer={
          openTest && (
            <div className="flex items-center gap-2" data-test-panel-nav="">
              <button
                type="button"
                className="btn-secondary inline-flex items-center gap-1 text-xs disabled:opacity-50"
                disabled={!prevFailure}
                onClick={() => prevFailure && setOpenTestId(prevFailure)}
              >
                <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" /> Previous failure
              </button>
              <button
                type="button"
                className="btn-secondary inline-flex items-center gap-1 text-xs disabled:opacity-50"
                disabled={!nextFailure}
                onClick={() => nextFailure && setOpenTestId(nextFailure)}
              >
                Next failure <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
              {failingIndex >= 0 && (
                <span className="text-xs tabular-nums text-[var(--color-text-muted)]">
                  {failingIndex + 1} of {failingIds.length} failures on this page
                </span>
              )}
              <Link
                to={`/runs/${runId}/tests/${openTest.id}`}
                className="ml-auto inline-flex items-center gap-1 text-xs font-medium text-[var(--color-accent)] hover:underline"
              >
                Open full page <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
              </Link>
            </div>
          )
        }
      >
        {/* Keyed: a new test starts on its own History tab, with its own data. */}
        {openTest && <TestCaseBody key={openTest.id} runId={runId} testId={openTest.id} compact />}
      </SidePanel>
    </div>
  )
}
