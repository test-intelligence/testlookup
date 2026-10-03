/**
 * Failure-groups bodies for tests and the gallery (VIZ-504): built from the
 * shape `/api/v1/analytics/failure-groups` answers (BE3: a C3 graph plus the
 * additive `groups`, roll-ups, `trend_grain`, `total_failures`), and wrapped as
 * the `{meta, series}` the catalogue reads after `chartResponseFromEnvelope`.
 *
 * Deterministic (no `Math.random`, no clock): the same call gives the same body.
 * The hostile labels are the e2e's (`<img src=x onerror=…>`, `__proto__`,
 * `constructor`, a very long line).
 */
import type { EnvelopeMeta, GraphChart } from '@/lib/viz/contracts'
import { NO_MESSAGE_ID, SINGLETONS_ID } from './failureGroups.model'

export const HOSTILE_GROUP_LABELS = [
  '<img src=x onerror="window.__xss=1">AssertionError',
  '__proto__',
  'constructor',
  `TimeoutError: ${'waiting for selector #checkout-button '.repeat(6).trim()}`,
] as const

const CATEGORIES = ['product_bug', 'infrastructure', 'test_data', 'automation_defect', 'flaky', 'unknown', 'other'] as const

export interface FailureGroupsFixtureOptions {
  /** How many groups (default 8). */
  groups?: number
  /** Include Jaccard edges between neighbours (default true). */
  edges?: boolean
  /** Use the hostile labels for the first groups. */
  hostile?: boolean
  /** One group holds almost every failure. */
  giant?: boolean
  /** Days on the trend axis (default 7). */
  days?: number
}

/** A day `i` days after 2026-09-01, as the server writes it. */
const day = (i: number) => {
  const d = new Date(Date.UTC(2026, 8, 1 + i))
  return d.toISOString().slice(0, 10)
}

/** The series object (graph + additive keys), as the catalogue hands it to a reader. */
export function failureGroupsSeries({
  groups: count = 8,
  edges = true,
  hostile = false,
  giant = false,
  days = 7,
}: FailureGroupsFixtureOptions = {}): GraphChart {
  const counts = Array.from({ length: count }, (_, i) => (giant && i === 0 ? 5000 : Math.max(2, Math.floor(400 / (1 + i * 0.6)))))
  const noMessage = 12
  const singletons = 30
  const total = counts.reduce((a, b) => a + b, 0) + noMessage + singletons
  const groups = counts.map((failures, i) => {
    const signature = `error #${i}: ${['assertion', 'timeout', 'connection refused', 'null pointer'][i % 4]}`
    const label =
      hostile && i < HOSTILE_GROUP_LABELS.length
        ? HOSTILE_GROUP_LABELS[i]
        : `Error ${i}: ${['AssertionError', 'TimeoutError', 'ConnectionRefused', 'NullPointer'][i % 4]} at step ${i}`
    const category = CATEGORIES[i % CATEGORIES.length]
    const trend = Array.from({ length: days }, (_, d) => ({
      x: day(d),
      y: Math.floor(failures / days) + (d === days - 1 ? failures % days : 0),
    }))
    return {
      id: signature,
      signature,
      label,
      distinct_raw_lines: i % 3 === 0 ? 2 : 1,
      failure_count: failures,
      affected_tests: Math.max(1, Math.floor(failures / 7)),
      affected_runs: Math.max(1, Math.floor(failures / 3)),
      first_seen: `${day(0)}T08:00:00Z`,
      last_seen: `${day(days - 1)}T17:30:00Z`,
      share_of_failures: failures / total,
      categories: [
        { category, count: failures - Math.floor(failures / 4) },
        { category: 'unknown', count: Math.floor(failures / 4) },
      ],
      dominant_category: category,
      trend,
      top_tests: [
        {
          fingerprint: `fp-${i}-a`,
          project_id: 'proj-1',
          name: hostile ? '<script>window.__xss=1</script>test_a' : `test_checkout_${i}_a`,
          count: Math.ceil(failures / 2),
        },
        { fingerprint: `fp-${i}-b`, project_id: 'proj-1', name: `test_checkout_${i}_b`, count: Math.floor(failures / 2) },
      ],
    }
  })
  const nodes = groups.map((g) => ({ id: g.id, label: g.label, size: g.failure_count, group: g.dominant_category }))
  const links: GraphChart['edges'] = []
  if (edges) {
    // As the server: pairs among the 60 largest groups, at most 300 links.
    const linked = Math.min(groups.length, 60)
    for (let a = 0; a < linked; a++) {
      for (let b = a + 1; b < linked && links.length < 300; b++) {
        const h = (a * 31 + b * 17) % 7
        if (h < 2) links.push({ source: groups[a].id, target: groups[b].id, weight: Math.round(((h + 2) / 5) * 100) / 100 })
      }
    }
  }
  return {
    kind: 'graph',
    nodes,
    edges: links,
    groups,
    trend_grain: days <= 90 ? 'day' : 'week',
    total_failures: total,
    no_message: {
      id: NO_MESSAGE_ID,
      failure_count: noMessage,
      affected_tests: 3,
      affected_runs: 5,
      share_of_failures: noMessage / total,
    },
    singletons: { id: SINGLETONS_ID, group_count: singletons, failure_count: singletons, share_of_failures: singletons / total },
    omitted: { group_count: 0, failure_count: 0, share_of_failures: 0 },
  } as unknown as GraphChart
}

/** A C2 envelope that validates (the shape of `contracts/viz/fixtures/envelope/valid/filtered.json`). */
export const FAILURE_GROUPS_META: EnvelopeMeta = {
  schema_version: 1,
  scope: {
    projects: [{ id: '11111111-1111-4111-8111-111111111111', name: 'Checkout' }],
    releases: [],
    suites: [],
    window: { from: '2026-09-01', to: '2026-09-07', days: 7, timezone: 'UTC' },
  },
  totals: { matched_runs: 40, total_runs: 40, matched_executions: 3960, total_executions: 3960 },
  pass_rate_basis: 'executions',
  ignored_filters: [],
  truncated: false,
  truncated_total: null,
  measured: true,
  reason: null,
  includes_in_progress: 0,
  partial_day: null,
  generated_at: '2026-09-08T12:00:00Z',
  as_of: '2026-09-08T12:00:00Z',
} as unknown as EnvelopeMeta

/** The `{meta, series}` response a section's state holds. */
export function failureGroupsResponse(options: FailureGroupsFixtureOptions = {}) {
  return { meta: FAILURE_GROUPS_META, series: failureGroupsSeries(options) }
}

/** The body `/analytics/systemic-clusters` answers (BE3: `membership_key` added in 0193). */
export function clustersBody(over: Record<string, unknown> = {}) {
  return {
    items: [
      {
        cluster_key: 'sfc_001',
        membership_key: 'a'.repeat(32),
        label: 'checkout tests fail together',
        cause_family: 'external_dependency',
        size: 14,
        cohesion: 0.82,
        co_failure_runs: 9,
        window_days: 60,
        computed_at: '2026-10-01T06:40:00Z',
        members: [
          { test_fingerprint: 'fp1', test_name: '<img src=x onerror="window.__xss=1">test_pay', failure_runs: 9 },
          { test_fingerprint: 'fp2', test_name: 'test_cart', failure_runs: 8 },
        ],
      },
      {
        cluster_key: 'sfc_002',
        membership_key: 'b'.repeat(32),
        label: 'constructor',
        cause_family: 'unknown',
        size: 3,
        cohesion: 0.5,
        co_failure_runs: 4,
        window_days: 60,
        computed_at: '2026-10-01T06:40:00Z',
        members: [],
      },
    ],
    total: 2,
    empty_is_normal: 'Most projects have no systemic clusters.',
    meta: FAILURE_GROUPS_META,
    ...over,
  }
}
