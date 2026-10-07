/**
 * The cells and the runs table the suite page's tabs share with the former
 * `/coverage/suite` body (`SuiteDetailPage`), moved here in UX redesign P4 so
 * the suite page can draw them without importing the chart page.
 */
import { Link } from 'react-router-dom'
import { AlertTriangle, Calendar } from 'lucide-react'
import { clsx } from 'clsx'
import type { SuiteDetailRun } from '@/types/analytics'
import { formatSuiteDate } from './format'

/** A pass rate as a bar and a number, its hue by band (>= 90 passed, >= 70 broken, else failed). */
export function PassRateBar({ rate }: { rate: number }) {
  const color = rate >= 90 ? 'var(--status-passed)' : rate >= 70 ? 'var(--status-broken)' : 'var(--status-failed)'
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-[var(--color-bg-hover)] rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${Math.min(rate, 100)}%`, backgroundColor: color }}
        />
      </div>
      <span className="text-xs font-mono font-semibold w-12 text-right" style={{ color }}>
        {Number(rate).toFixed(1)}%
      </span>
    </div>
  )
}

/** A test's latest result (PASSED / FAILED / BROKEN / SKIPPED), the word in its status hue. */
export function LastStatusBadge({ status }: { status: string }) {
  const s = (status || '').toUpperCase()
  const cls =
    s === 'PASSED'  ? 'bg-[var(--status-passed-bg)] text-[var(--status-passed)] ring-[var(--status-passed-bd)]' :
    s === 'FAILED'  ? 'bg-[var(--status-failed-bg)] text-[var(--status-failed)] ring-[var(--status-failed-bd)]' :
    s === 'BROKEN'  ? 'bg-[var(--status-broken-bg)] text-[var(--status-broken)] ring-[var(--status-broken-bd)]' :
    s === 'SKIPPED' ? 'bg-[var(--status-skipped-bg)] text-[var(--status-skipped)] ring-[var(--status-skipped-bd)]' :
                      'bg-[var(--color-bg-card)]/10 text-[var(--color-text-muted)] ring-[var(--color-border)]/20'
  return (
    <span className={clsx('inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ring-1 ring-inset', cls)}>
      {s}
    </span>
  )
}

/** The "Flaky" pill beside a test name (the analytics' own flag: 3+ runs, 5-95 % failing). */
export function FlakyPill() {
  return (
    <span className="flex-shrink-0 inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs font-medium bg-[var(--status-flaky-bg)] text-[var(--status-flaky)] ring-1 ring-inset ring-[var(--status-flaky-bd)]">
      <AlertTriangle aria-hidden="true" className="h-3 w-3" />
      Flaky
    </span>
  )
}

/**
 * The suite's most recent runs in the window, with this suite's counts in each
 * (a run can hold several suites). The server returns the newest few, not
 * every run, so the heading counts the rows shown.
 */
export function RecentRunsTable({ runs, primary = false }: { runs: readonly SuiteDetailRun[]; primary?: boolean }) {
  return (
    <div className="card" data-primary={primary ? '' : undefined} data-suite-recent-runs="">
      <div className="flex items-center gap-2 mb-4">
        <Calendar aria-hidden="true" className="h-4 w-4 text-[var(--color-text-muted)]" />
        <h3 className="text-sm font-semibold text-[var(--color-text)]">
          Recent Runs ({runs.length})
        </h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className="th text-left">Build</th>
              <th className="th text-right">Date</th>
              <th className="th text-right text-[var(--status-passed)]">Passed</th>
              <th className="th text-right text-[var(--status-failed)]">Failed</th>
              <th className="th text-right text-[var(--status-skipped)]">Skipped</th>
              <th className="th min-w-[160px]">Pass Rate</th>
              <th className="th text-left">Run</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.test_run_id} className="table-row">
                <td className="td font-mono text-[var(--color-text-secondary)] text-xs">{r.build_number ?? '—'}</td>
                <td className="td text-right text-xs text-[var(--color-text-muted)]">{formatSuiteDate(r.run_date)}</td>
                <td className="td text-right tabular-nums text-[var(--status-passed)]">{r.passed}</td>
                <td className="td text-right tabular-nums text-[var(--status-failed)]">{r.failed}</td>
                <td className="td text-right tabular-nums text-[var(--status-skipped)]">{r.skipped}</td>
                <td className="td w-44">
                  <PassRateBar rate={Number(r.pass_rate ?? 0)} />
                </td>
                <td className="td">
                  <Link
                    to={`/runs/${r.test_run_id}`}
                    className="text-xs text-[var(--color-text)] hover:text-[var(--color-text-secondary)] transition-colors"
                  >
                    View run →
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
