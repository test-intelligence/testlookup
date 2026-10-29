/**
 * Fixtures for the W23a insights / expected-runs vitests (FAIL-11, TRND-03), in the REAL wire shapes.
 *
 * - `insight(overrides)`: one open `perf_drift` suite insight on "Payments sentinel" (every field present).
 * - `detector(kind, overrides)`: one detector state — measured and quiet by default.
 * - `insightList(items, detectors?)`: the REAL `GET …/insights` answer — `{enabled, range, items, total,
 *   dismissed_total, detectors (always four), generated_at}`, never a bare array.
 * - `cadence(overrides)`: one Mon–Fri 06:00 Europe/Berlin cadence on suite "nightly-regression".
 * - `statusItem(overrides)`: one status row of that cadence (5 expected, 5 met by default).
 * - `cadenceStatus(items | 'none')`: the REAL `GET …/run-cadences/status` answer; `'none'` = the
 *   project has no cadence (`configured: false`).
 *
 * Sentinels are non-hex words ("Payments sentinel", "checkout_sentinel_test", "Release freeze sentinel"),
 * so they never collide with a uuid or read as a phone number. Timestamps are built at CALL time (never a
 * module constant).
 */
import {
  INSIGHT_KINDS,
  type Insight,
  type InsightDetector,
  type InsightKind,
  type InsightList,
} from '@/types/insights'
import type { CadenceWindow, RunCadence, RunCadenceStatus, RunCadenceStatusItem } from '@/types/runCadence'

export const INSIGHT_PROJECT_ID = '00000000-0000-4000-8000-00000000e0a1'
export const SENTINEL_SUITE = 'Payments sentinel'
export const SENTINEL_TEST = 'checkout_sentinel_test'
export const SENTINEL_PAUSE_REASON = 'Release freeze sentinel'
export const CADENCE_SUITE = 'nightly-regression'

let sequence = 0

/** A canonical (version 4 shaped) uuid, distinct per call. */
export function mockUuid(): string {
  sequence += 1
  return `00000000-0000-4000-8000-${String(0xe000 + sequence).padStart(12, '0')}`
}

function hoursAgo(hours: number): string {
  return new Date(Date.now() - hours * 3_600_000).toISOString()
}

export function insight(overrides: Partial<Insight> = {}): Insight {
  const runId = mockUuid()
  return {
    id: mockUuid(),
    kind: 'perf_drift',
    severity: 'high',
    status: 'open',
    resolved_reason: null,
    title: `Suite ${SENTINEL_SUITE} is 2.4× slower than its previous 20 runs`,
    subject_label: SENTINEL_SUITE,
    window_key: '7d',
    evidence: { suite_name: SENTINEL_SUITE, ratio: 2.4, baseline_runs: 20, newest_run_id: runId },
    first_seen_at: hoursAgo(3),
    last_seen_at: hoursAgo(0.2),
    resolved_at: null,
    detection_count: 3,
    dismissed_at: null,
    dismissed_by_name: null,
    link: { path: '/trends?tab=durations', label: 'Trends › Execution time' },
    run_path: `/runs/${runId}?tab=changes`,
    ...overrides,
  }
}

export function detector(kind: InsightKind, overrides: Partial<InsightDetector> = {}): InsightDetector {
  return {
    kind,
    measured: true,
    reason: null,
    evaluated_at: hoursAgo(0.5),
    subjects_measured: 3,
    firing: 0,
    ...overrides,
  }
}

/** `detectors`: the four kinds, measured and quiet, unless given (a given list is used as is). */
export function insightList(items: Insight[] = [], detectors?: InsightDetector[]): InsightList {
  return {
    enabled: true,
    range: '7d',
    items,
    total: items.length,
    dismissed_total: items.filter((i) => i.dismissed_at).length,
    detectors: detectors ?? INSIGHT_KINDS.map((kind) => detector(kind)),
    generated_at: new Date().toISOString(),
  }
}

export function cadence(overrides: Partial<RunCadence> = {}): RunCadence {
  const now = new Date().toISOString()
  return {
    id: mockUuid(),
    project_id: INSIGHT_PROJECT_ID,
    suite_name: CADENCE_SUITE,
    mode: 'weekly',
    weekdays: ['mon', 'tue', 'wed', 'thu', 'fri'],
    expected_by: '06:00',
    cron: null,
    timezone: 'Europe/Berlin',
    grace_minutes: 60,
    summary: 'Mon–Fri by 06:00 (Europe/Berlin) · grace 60 min',
    effective_from: hoursAgo(24 * 10),
    paused: false,
    paused_at: null,
    paused_until: null,
    paused_reason: null,
    paused_by_name: null,
    next_deadline: new Date(Date.now() + 6 * 3_600_000).toISOString(),
    created_by_name: 'Alice Lead',
    created_at: now,
    updated_at: now,
    ...overrides,
  }
}

/** One missed window ending `daysAgo` days ago, labelled with its UTC weekday ("Wed 28 Oct"). */
export function missedWindow(daysAgo: number, overrides: Partial<CadenceWindow> = {}): CadenceWindow {
  const deadline = new Date(Date.now() - daysAgo * 86_400_000)
  const label = deadline.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' })
  return {
    deadline: deadline.toISOString(),
    window_start: new Date(deadline.getTime() - 86_400_000 + 3_600_000).toISOString(),
    window_end: new Date(deadline.getTime() + 3_600_000).toISOString(),
    outcome: 'missed',
    recorded: true,
    run_count: 0,
    local_label: label.replace(',', ''),
    ...overrides,
  }
}

export function statusItem(overrides: Partial<RunCadenceStatusItem> = {}): RunCadenceStatusItem {
  return {
    cadence: cadence(),
    measured: true,
    reason: null,
    expected: 5,
    met: 5,
    missed: 0,
    paused: 0,
    pending: 0,
    missed_windows: [],
    last_run_at: hoursAgo(5),
    ...overrides,
  }
}

/** The status answer; `'none'` = no cadence configured. Sums are derived from the items. */
export function cadenceStatus(items: RunCadenceStatusItem[] | 'none' = 'none', days = 14): RunCadenceStatus {
  const rows = items === 'none' ? [] : items
  const sum = (key: 'expected' | 'met' | 'missed' | 'paused') => rows.reduce((acc, it) => acc + (it[key] ?? 0), 0)
  return {
    configured: rows.length > 0,
    days,
    window_start: new Date(Date.now() - days * 86_400_000).toISOString(),
    window_end: new Date().toISOString(),
    items: rows,
    total: rows.length,
    expected: sum('expected'),
    met: sum('met'),
    missed: sum('missed'),
    paused: sum('paused'),
  }
}
