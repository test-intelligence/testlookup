/**
 * Per-test history timeline — the hero view of a canonical test case.
 *
 * Roadmap Phase 1 (P1-A). When practitioners were asked open-endedly what
 * tooling they wanted, visualization of a test's outcome history over time,
 * annotated with environment metadata, was one of the top asks — one
 * participant put it as "we run tests so often, I often miss the bigger
 * picture". A paginated table of rows is not that picture; a compact strip
 * of outcomes in run order is.
 *
 * Deliberate design choices:
 *
 * - **Oldest → newest, left to right.** The API returns newest-first (run
 *   order desc); a timeline read left-to-right as time moving forward is the
 *   only orientation that makes a flip pattern legible, so the list is
 *   reversed here rather than in the API, where newest-first is right for
 *   every other consumer.
 * - **Unknown environment is shown as unknown.** A run that never recorded an
 *   environment renders as "unknown", never folded into a synthetic default —
 *   grouping nulls would make a corpus that never recorded environments look
 *   perfectly consistent.
 * - **Retries are surfaced, not absorbed.** A cell that passed only after
 *   retries is marked, because "passed on attempt 3" is evidence about
 *   stability that a green tick erases.
 * - **No trend-to-zero framing.** Flakiness is a steady state to manage, not
 *   a backlog that burns down, so this shows load and pattern — never a
 *   "remaining" count implying an end state.
 */
import { useMemo } from 'react'

import type { CanonicalRunHistoryItem } from '@/types/suites'

/** Outcome buckets the strip renders. Keep in sync with backend TestStatus. */
const PASSED = new Set(['PASSED', 'passed'])
const FAILED = new Set(['FAILED', 'failed', 'BROKEN', 'broken'])
const SKIPPED = new Set(['SKIPPED', 'skipped'])

type Bucket = 'passed' | 'failed' | 'skipped' | 'unknown'

function bucketOf(status: string): Bucket {
  if (PASSED.has(status)) return 'passed'
  if (FAILED.has(status)) return 'failed'
  if (SKIPPED.has(status)) return 'skipped'
  return 'unknown'
}

const CELL_CLASS: Record<Bucket, string> = {
  passed: 'bg-[var(--status-passed)]',
  failed: 'bg-[var(--status-failed)]',
  skipped: 'bg-[var(--status-skipped)]',
  // An uninterpretable status is its own bucket, not silently a pass or a
  // fail — the same reason the run header grew an "unrecognised" count.
  unknown: 'bg-[var(--status-broken)]',
}

function formatDuration(ms: number | null): string {
  if (ms === null || ms === undefined) return 'unknown duration'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function environmentLabel(item: CanonicalRunHistoryItem): string {
  if (!item.environment) return 'environment unknown'
  return item.environment_source === 'derived'
    ? `${item.environment} (inferred)`
    : item.environment
}

/**
 * Most recent runs to draw. A long-lived test can accumulate thousands of
 * runs, and a strip of thousands of cells is both unreadable and a lot of DOM.
 * When the cap bites we SAY SO in the heading — a silently truncated history
 * would read as "this is everything", which is exactly the kind of quiet
 * under-reporting this view exists to fix.
 */
export const MAX_TIMELINE_CELLS = 120

export interface TestHistoryTimelineProps {
  items: CanonicalRunHistoryItem[]
}

export function TestHistoryTimeline({ items }: TestHistoryTimelineProps) {
  // API order is newest-first; a timeline reads forward in time. Take the most
  // recent slice BEFORE reversing so the cap keeps the newest runs, not the
  // oldest — the recent past is what a stability judgement is made from.
  const ordered = useMemo(
    () => [...items.slice(0, MAX_TIMELINE_CELLS)].reverse(),
    [items],
  )
  const truncated = items.length > MAX_TIMELINE_CELLS

  const summary = useMemo(() => {
    const counts = { passed: 0, failed: 0, skipped: 0, unknown: 0 }
    let flips = 0
    let retried = 0
    let previous: Bucket | null = null
    const environments = new Set<string>()

    for (const item of ordered) {
      const bucket = bucketOf(item.status)
      counts[bucket] += 1
      if ((item.retry_count ?? 0) > 0 || item.is_flaky_run) retried += 1
      if (item.environment) environments.add(item.environment)
      // Flips are counted over pass/fail only — a skip is an absence of
      // evidence, not a change of outcome, and counting it as a transition
      // would inflate instability for a test that was simply not run.
      if (bucket === 'passed' || bucket === 'failed') {
        if (previous !== null && previous !== bucket) flips += 1
        previous = bucket
      }
    }
    return { counts, flips, retried, environments: [...environments] }
  }, [ordered])

  if (ordered.length === 0) return null

  const evaluated = summary.counts.passed + summary.counts.failed

  return (
    <section
      className="mb-6 rounded-lg p-4 ring-1 ring-[var(--color-border)]"
      aria-labelledby="test-history-timeline-heading"
    >
      <div className="mb-3 flex items-baseline justify-between gap-4">
        <h2
          id="test-history-timeline-heading"
          className="text-sm font-semibold uppercase text-[var(--color-text-muted)]"
        >
          History (
          {truncated
            ? `last ${ordered.length} of ${items.length} runs`
            : `${ordered.length} runs`}
          )
        </h2>
        <span className="text-xs text-[var(--color-text-muted)]">oldest → newest</span>
      </div>

      <ol className="flex flex-wrap gap-1" aria-label="Outcome per run, oldest first">
        {ordered.map((item) => {
          const bucket = bucketOf(item.status)
          const retried = (item.retry_count ?? 0) > 0 || item.is_flaky_run === true
          const title = [
            item.status,
            item.build_number ? `build ${item.build_number}` : null,
            item.branch,
            environmentLabel(item),
            formatDuration(item.duration_ms),
            retried
              ? `passed on attempt ${(item.retry_count ?? 0) + 1}`
              : null,
            new Date(item.created_at).toLocaleString(),
          ]
            .filter(Boolean)
            .join(' · ')

          return (
            <li key={item.test_case_id}>
              <span
                title={title}
                aria-label={title}
                className={`block h-6 w-3 rounded-sm ${CELL_CLASS[bucket]} ${
                  retried ? 'ring-2 ring-[var(--status-flaky)]' : ''
                }`}
              />
            </li>
          )
        })}
      </ol>

      <dl className="mt-4 grid grid-cols-2 gap-4 text-sm md:grid-cols-4">
        <div>
          <dt className="text-xs uppercase text-[var(--color-text-muted)]">Outcomes</dt>
          <dd className="mt-1 text-[var(--color-text)]">
            {summary.counts.passed} passed · {summary.counts.failed} failed
            {summary.counts.skipped > 0 ? ` · ${summary.counts.skipped} skipped` : ''}
          </dd>
        </div>
        <div>
          <dt className="text-xs uppercase text-[var(--color-text-muted)]">Flips</dt>
          <dd className="mt-1 text-[var(--color-text)]">
            {summary.flips}
            <span className="ml-1 text-xs text-[var(--color-text-muted)]">
              over {evaluated} evaluated
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-xs uppercase text-[var(--color-text-muted)]">Runs with retries</dt>
          <dd className="mt-1 text-[var(--color-text)]">{summary.retried}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase text-[var(--color-text-muted)]">Environments</dt>
          <dd className="mt-1 truncate text-[var(--color-text)]" title={summary.environments.join(', ')}>
            {summary.environments.length > 0 ? summary.environments.join(', ') : 'none recorded'}
          </dd>
        </div>
      </dl>
    </section>
  )
}

export default TestHistoryTimeline
