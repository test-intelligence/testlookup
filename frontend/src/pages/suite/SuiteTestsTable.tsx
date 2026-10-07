/**
 * The suite page's Tests tab table (UX redesign P4): the suite's CATALOG (its
 * canonical test cases, with selection, Move and the latest-run links), each
 * row enriched with what the window's analytics know about that test — pass
 * rate, executions, average duration, latest result, the flaky flag and the
 * last error — joined on the test fingerprint.
 *
 * A catalog row the analytics have no row for did not run in the window (or,
 * past the analytics' row cap, is not among the tests it returned): its run
 * cells read "—" with the reason on hover, never 0 %.
 */
import { Link, useNavigate } from 'react-router-dom'
import { ArrowRightLeft, ExternalLink } from 'lucide-react'
import type { CanonicalTestCase } from '@/types/suites'
import type { SuiteDetailTestCase } from '@/types/analytics'
import { FlakyPill, LastStatusBadge, PassRateBar } from './SuiteParts'
import { formatSuiteDuration } from './format'

const STATUS_COLOR: Record<string, string> = {
  active: 'bg-[var(--status-passed-bg)]/10 text-[var(--status-passed)] ring-[var(--status-passed)]/30',
  deleted: 'bg-[var(--status-failed-bg)]/10 text-[var(--status-failed)] ring-[var(--status-failed)]/30',
  needs_review: 'bg-[var(--status-broken-bg)]/10 text-[var(--status-broken)] ring-[var(--status-broken)]/30',
}

/** The catalog status of a case (active / deleted / needs review), not a test result. */
function StatusPill({ status }: { status: string }) {
  const cls = STATUS_COLOR[status] || 'bg-[var(--color-bg-hover)]/10 text-[var(--color-text-muted)] ring-[var(--color-border)]/30'
  return (
    <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium uppercase ring-1 ring-inset ${cls}`}>
      {status}
    </span>
  )
}

/** A run cell with nothing to show: a dash, and why on hover. */
function NoRun({ title }: { title: string }) {
  return (
    <span title={title} data-no-run="" className="text-[var(--color-text-muted)]">
      —
    </span>
  )
}

export interface SuiteTestsTableProps {
  cases: readonly CanonicalTestCase[]
  /** The window's analytics rows, by test fingerprint. */
  stats: ReadonlyMap<string, SuiteDetailTestCase>
  /** Why a run cell is empty, e.g. "Not run in the last 30 days". */
  noRunTitle: string
  canEdit: boolean
  selectedIds: ReadonlySet<string>
  onToggleOne: (id: string) => void
  onToggleAll: () => void
  onMove: (canonical: CanonicalTestCase) => void
}

export default function SuiteTestsTable({
  cases,
  stats,
  noRunTitle,
  canEdit,
  selectedIds,
  onToggleOne,
  onToggleAll,
  onMove,
}: SuiteTestsTableProps) {
  const navigate = useNavigate()
  const allSelected = cases.length > 0 && cases.every((c) => selectedIds.has(c.id))
  const someSelected = !allSelected && cases.some((c) => selectedIds.has(c.id))

  return (
    <div data-primary="" data-suite-tests="" className="overflow-x-auto rounded-lg ring-1 ring-[var(--color-border)]">
      <table className="w-full divide-y divide-[var(--color-border)] text-sm">
        <thead className="bg-[var(--color-bg-secondary)] text-left text-xs uppercase whitespace-nowrap text-[var(--color-text-muted)]">
          <tr>
            {canEdit && (
              <th className="w-10 px-4 py-2">
                <input
                  type="checkbox"
                  checked={allSelected}
                  ref={(el) => { if (el) el.indeterminate = someSelected }}
                  onChange={onToggleAll}
                  aria-label={allSelected ? 'Deselect all' : 'Select all'}
                  className="h-4 w-4 cursor-pointer"
                />
              </th>
            )}
            <th className="px-4 py-2 font-medium">Test</th>
            <th className="px-3 py-2 font-medium">Class</th>
            <th className="px-3 py-2 font-medium w-40">Pass rate</th>
            <th className="px-3 py-2 font-medium text-right">Executions</th>
            <th className="px-3 py-2 font-medium text-right">Avg duration</th>
            <th className="px-3 py-2 font-medium">Last result</th>
            <th className="px-3 py-2 font-medium">Status</th>
            <th className="px-3 py-2 font-medium">Source</th>
            {/* The row carries the latest run's id, not a timestamp, so
                this column names the run rather than claiming a date. */}
            <th className="px-3 py-2 font-medium">Latest run</th>
            <th className="px-3 py-2 font-medium" />
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
            const stat = stats.get(c.test_fingerprint)
            return (
              <tr
                key={c.id}
                data-test-row={c.test_fingerprint}
                onClick={() => navigate(detailHref)}
                className="cursor-pointer hover:bg-[var(--color-bg-secondary)]"
              >
                {canEdit && (
                  <td className="px-4 py-2">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => onToggleOne(c.id)}
                      onClick={e => e.stopPropagation()}
                      aria-label={`Select ${c.test_name}`}
                      className="h-4 w-4 cursor-pointer"
                    />
                  </td>
                )}
                <td className="px-4 py-2 max-w-[360px]">
                  <div className="flex items-center gap-2">
                    <Link
                      to={detailHref}
                      onClick={e => e.stopPropagation()}
                      className="font-medium text-[var(--color-text)] hover:text-[var(--color-accent)] hover:underline"
                    >
                      {c.test_name}
                    </Link>
                    {stat?.is_flaky && <FlakyPill />}
                  </div>
                  {stat?.last_error && (
                    <p data-last-error="" className="mt-0.5 truncate text-xs text-[var(--status-failed)]/70" title={stat.last_error}>
                      {stat.last_error}
                    </p>
                  )}
                </td>
                <td className="px-3 py-2 text-[var(--color-text-muted)] text-xs max-w-[180px] truncate" title={c.class_name ?? ''}>
                  {c.class_name ?? '—'}
                </td>
                <td className="px-3 py-2">
                  {/* A rate over nothing evaluated (every run skipped) comes back null: a dash, never 0 %. */}
                  {stat && stat.pass_rate != null ? (
                    <div title={`${stat.passed} passed · ${stat.failed} failed · ${stat.skipped} skipped`}>
                      <PassRateBar rate={Number(stat.pass_rate)} />
                    </div>
                  ) : (
                    <NoRun title={stat ? 'No evaluated run: every execution was skipped' : noRunTitle} />
                  )}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-[var(--color-text-secondary)]">
                  {stat ? stat.total_executions : <NoRun title={noRunTitle} />}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-xs text-[var(--color-text-muted)]">
                  {stat ? formatSuiteDuration(stat.avg_duration_ms) : <NoRun title={noRunTitle} />}
                </td>
                <td className="px-3 py-2">
                  {stat?.last_status ? <LastStatusBadge status={stat.last_status} /> : <NoRun title={noRunTitle} />}
                </td>
                <td className="px-3 py-2"><StatusPill status={c.status} /></td>
                <td className="px-3 py-2 text-[var(--color-text-muted)]">{c.source}</td>
                <td className="px-3 py-2 whitespace-nowrap text-[var(--color-text-muted)] text-xs">
                  {c.last_seen_run_id ? (
                    <Link
                      to={`/runs/${c.last_seen_run_id}`}
                      onClick={e => e.stopPropagation()}
                      title={`Open run ${c.last_seen_run_id}`}
                      className="hover:text-[var(--color-accent)] hover:underline"
                    >
                      run <span className="font-mono">{c.last_seen_run_id.slice(0, 8)}</span>
                    </Link>
                  ) : '—'}
                </td>
                <td className="px-3 py-2 text-right">
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
                        onClick={e => { e.stopPropagation(); onMove(c) }}
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
  )
}
