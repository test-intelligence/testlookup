/**
 * Deterministic fixtures for `StackedColumnChart` (VIZ-104, Wave 2.5 K1): the
 * unit tests read them, and the chart gallery (wired by the integrator) draws
 * them for the visual baselines.
 *
 * Every value is a literal — no `Date.now()`, no random — because a fixture
 * that changes with the clock makes a screenshot diff that is not a
 * regression. Each one carries the edge case it exists for, so the gallery
 * draws the interesting case rather than the happy one. The writing rule of
 * `wave2Fixtures.ts` holds here too: no id is ever written with a leading
 * hash, which `check:theme` would read as a colour literal.
 */
import { formatNumber } from '@/utils/formatters'
import {
  buildStackedColumnModel,
  type StackedColumnBucketInput,
  type StackedColumnModel,
  type StackedColumnSeriesInput,
} from '../stackedColumnModel'

/** The four statuses a test-run chart stacks, bottom first. */
export const STATUS_SERIES: StackedColumnSeriesInput[] = [
  { key: 'passed', label: 'Passed', status: 'passed' },
  { key: 'failed', label: 'Failed', status: 'failed' },
  { key: 'broken', label: 'Broken', status: 'broken' },
  { key: 'skipped', label: 'Skipped', status: 'skipped' },
]

const statusDay = (
  key: string,
  label: string,
  passed: number | null,
  failed: number | null,
  broken: number | null,
  skipped: number | null,
): StackedColumnBucketInput => ({ key, label, values: { passed, failed, broken, skipped } })

/**
 * Two weeks of executions by status. Carries: a day nothing was measured on
 * (a gap, 2026-03-04), a day that ran and executed nothing (a measured zero,
 * 2026-03-07), a day whose skipped count was not reported (2026-03-09), and
 * broken executions on several days (Overview used to drop them).
 */
export const statusDailyBuckets: StackedColumnBucketInput[] = [
  statusDay('2026-02-25', 'Feb 25', 180, 12, 3, 5),
  statusDay('2026-02-26', 'Feb 26', 176, 9, 0, 6),
  statusDay('2026-02-27', 'Feb 27', 190, 14, 4, 4),
  statusDay('2026-02-28', 'Feb 28', 60, 2, 0, 1),
  statusDay('2026-03-01', 'Mar 1', 58, 3, 1, 0),
  statusDay('2026-03-02', 'Mar 2', 185, 20, 6, 5),
  statusDay('2026-03-03', 'Mar 3', 188, 11, 2, 7),
  statusDay('2026-03-04', 'Mar 4', null, null, null, null),
  statusDay('2026-03-05', 'Mar 5', 192, 8, 1, 3),
  statusDay('2026-03-06', 'Mar 6', 195, 6, 0, 3),
  statusDay('2026-03-07', 'Mar 7', 0, 0, 0, 0),
  statusDay('2026-03-08', 'Mar 8', 70, 1, 0, 2),
  statusDay('2026-03-09', 'Mar 9', 199, 7, 2, null),
  statusDay('2026-03-10', 'Mar 10', 120, 4, 1, 2),
]

export const statusDailyFixture: StackedColumnModel = buildStackedColumnModel({
  buckets: statusDailyBuckets,
  series: STATUS_SERIES,
  valueTitle: 'Executions',
  bucketTitle: 'Day (UTC)',
  xType: 'time',
})

/** Series that are NOT statuses: they take series colours 1-3 and category decals. */
export const HOURS_SERIES: StackedColumnSeriesInput[] = [
  { key: 'triage', label: 'Triage' },
  { key: 'quarantine', label: 'Quarantine' },
  { key: 'dedup', label: 'Deduplication' },
]

const hoursMonth = (key: string, label: string, triage: number, quarantine: number, dedup: number | null) => ({
  key,
  label,
  values: { triage, quarantine, dedup },
})

/** Six months of hours saved, fractional, with one month whose dedup leg was not measured. */
export const seriesMonthlyFixture: StackedColumnModel = buildStackedColumnModel({
  buckets: [
    hoursMonth('2025-10', 'Oct 2025', 12.5, 4.25, 2),
    hoursMonth('2025-11', 'Nov 2025', 14, 5.5, 3.75),
    hoursMonth('2025-12', 'Dec 2025', 9.5, 3, null),
    hoursMonth('2026-01', 'Jan 2026', 18.25, 6, 4.5),
    hoursMonth('2026-02', 'Feb 2026', 21, 7.75, 5),
    hoursMonth('2026-03', 'Mar 2026', 16.5, 6.25, 4),
  ],
  series: HOURS_SERIES,
  valueTitle: 'Hours saved',
  bucketTitle: 'Month',
  format: (value) => `${formatNumber(value, { maximumFractionDigits: 2 })} h`,
  xType: 'time',
})

/** Names as ingested CI files carry them: markup, a very long name, right-to-left text, an emoji. */
export const HOSTILE_BUCKET_LABEL = '<img src=x onerror="alert(1)">'
export const HOSTILE_SERIES_LABEL = '<script>alert("series")</script>'
export const LONG_BUCKET_LABEL = 'integration-tests/payments/checkout-flow-with-saved-card-and-3ds-challenge'

export const hostileLabelsFixture: StackedColumnModel = buildStackedColumnModel({
  buckets: [
    { key: 'suite-a', label: HOSTILE_BUCKET_LABEL, values: { a: 12, b: 3 } },
    { key: 'suite-b', label: LONG_BUCKET_LABEL, values: { a: 30, b: 1 } },
    { key: 'suite-c', label: 'בדיקות קצה', values: { a: 8, b: 0 } },
    { key: 'suite-d', label: 'Smoke 🔥 suite', values: { a: 5, b: 2 } },
  ],
  series: [
    { key: 'a', label: HOSTILE_SERIES_LABEL },
    { key: 'b', label: 'Failed', status: 'failed' },
  ],
  valueTitle: 'Executions',
  bucketTitle: 'Suite',
})

/** One bucket: still a chart, one column wide. */
export const singleBucketFixture: StackedColumnModel = buildStackedColumnModel({
  buckets: [statusDay('2026-03-10', 'Mar 10', 42, 3, 1, 0)],
  series: STATUS_SERIES,
  valueTitle: 'Executions',
  bucketTitle: 'Day (UTC)',
  xType: 'time',
})

const MONTHS = ['Jan', 'Feb', 'Mar'] as const

/** Sixteen suites: more categories than columns can name, so the chart draws them as horizontal bars. */
export const MANY_SUITE_NAMES = [
  'auth',
  'billing-api',
  'cart',
  'checkout',
  LONG_BUCKET_LABEL,
  'inventory',
  'login-sso',
  'notifications',
  'orders',
  'profile',
  'recommendations',
  'search',
  'shipping',
  'smoke',
  'ui-e2e',
  'webhooks',
] as const

/**
 * Executions by status per suite, over more than `MAX_CATEGORY_COLUMNS`
 * suites: the kit never drops a category name, so this is drawn as bars with
 * every name beside its bar. The statuses are passed in Trends' old order
 * (skipped before broken) on purpose: the kit stacks them in its one order.
 */
export const manyCategoriesFixture: StackedColumnModel = buildStackedColumnModel({
  buckets: MANY_SUITE_NAMES.map((label, i) =>
    statusDay(`suite-${i}`, label, 40 + ((i * 13) % 50), i % 4 === 0 ? 0 : (i * 3) % 7, i % 5 === 2 ? 1 : 0, (i * 2) % 3),
  ),
  series: [STATUS_SERIES[0], STATUS_SERIES[1], STATUS_SERIES[3], STATUS_SERIES[2]],
  valueTitle: 'Executions',
  bucketTitle: 'Suite',
})

/** Sixty days: too many labels to lie flat in a card, so the axis thins or slants them. */
export const longWindowFixture: StackedColumnModel = buildStackedColumnModel({
  buckets: Array.from({ length: 60 }, (_, i) => {
    const month = Math.min(2, Math.floor(i / 30))
    const dayOfMonth = (i % 30) + 1
    const key = `2026-0${month + 1}-${String(dayOfMonth).padStart(2, '0')}`
    // Deterministic, uneven volumes: a weekly rhythm plus a slow rise.
    const passed = 120 + ((i * 37) % 60) + i
    const failed = (i * 7) % 11
    return statusDay(key, `${MONTHS[month]} ${dayOfMonth}`, passed, failed, i % 9 === 0 ? 2 : 0, (i * 3) % 5)
  }),
  series: STATUS_SERIES,
  valueTitle: 'Executions',
  bucketTitle: 'Day (UTC)',
  xType: 'time',
})
