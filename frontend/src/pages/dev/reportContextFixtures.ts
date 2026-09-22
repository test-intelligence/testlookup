/**
 * Fixed data for `/__report-context` (the report chrome gallery) and its
 * tests. Every `meta` here is a VALID C2 envelope except `totals-missing`,
 * which models an older cached payload (`reportContextFixtures.test.ts`
 * holds both claims).
 */
import type { EnvelopeMeta } from '@/lib/viz/contracts'
import type { ReportMetricsInput } from '@/components/reports/metricsModel'
import type { DroppedFilterNotice } from '@/components/filters/ReportFilterBar'
import type { MultiSelectOption } from '@/components/ui/MultiSelect'

export const GALLERY_RELEASE_CAP = 20
export const GALLERY_SUITE_CAP = 50
export const GALLERY_DEFAULT_WINDOW = 30

const P_CHECKOUT = { id: '11111111-1111-4111-8111-111111111111', name: 'Checkout' }

const rid = (n: number) => `22222222-2222-4222-8222-${String(n).padStart(12, '0')}`

export const GALLERY_RELEASE_OPTIONS: MultiSelectOption[] = [
  ...Array.from({ length: 24 }, (_, i) => ({ value: rid(i + 1), label: `2026.${String(i + 1).padStart(2, '0')}` })),
  { value: 'unattributed', label: 'Unattributed runs' },
]

export const GALLERY_SUITE_OPTIONS: MultiSelectOption[] = [
  'payments',
  'cart',
  'search',
  'checkout-api',
  'inventory',
  'auth',
  'notifications',
  'shipping',
  'returns',
].map((s) => ({ value: s, label: s }))

const releaseLabel = (id: string) => GALLERY_RELEASE_OPTIONS.find((o) => o.value === id)?.label ?? id

function meta(
  overrides: Partial<Omit<EnvelopeMeta, 'scope'>> & { scope?: Partial<EnvelopeMeta['scope']> } = {},
): EnvelopeMeta {
  const { scope, ...rest } = overrides
  return {
    schema_version: 1,
    scope: {
      projects: [P_CHECKOUT],
      releases: [],
      suites: [],
      window: { from: '2026-08-20', to: '2026-09-19', days: 30, timezone: 'UTC' },
      ...scope,
    },
    totals: { matched_runs: 143, total_runs: 143, matched_executions: 3960, total_executions: 3960 },
    pass_rate_basis: 'executions',
    ignored_filters: [],
    truncated: false,
    truncated_total: null,
    measured: true,
    reason: null,
    includes_in_progress: 0,
    partial_day: null,
    generated_at: '2026-09-19T10:42:07Z',
    as_of: '2026-09-19T10:42:07Z',
    ...rest,
  }
}

const METRICS: ReportMetricsInput = {
  values: {
    runs: 143,
    total_tests: 3960,
    passed: 3612,
    failed: 201,
    broken: 64,
    skipped: 83,
    unknown: 0,
    flaky: 12,
    pass_rate: 91.2121,
    total_duration: 7_620_000,
    avg_duration: 53_287,
  },
  passRateBasis: 'executions',
  trends: { pass_rate: 1.84, failed: -12.5 },
  comparable: true,
}

export interface GalleryCase {
  id: string
  title: string
  meta: EnvelopeMeta | null
  allProjects: boolean
  releaseIds: string[]
  suiteNames: string[]
  windowDays: number
  metrics: ReportMetricsInput
  droppedNotice?: DroppedFilterNotice[]
  loading?: boolean
}

const MANY_SUITES = ['payments', 'cart', 'search', 'checkout-api', 'inventory', 'auth', 'notifications']
const MANY_RELEASES = [1, 2, 3, 4, 5, 6].map(rid)

export const GALLERY_CASES: GalleryCase[] = [
  {
    id: 'unfiltered',
    title: 'Unfiltered',
    meta: meta(),
    allProjects: false,
    releaseIds: [],
    suiteNames: [],
    windowDays: 30,
    metrics: METRICS,
  },
  {
    id: 'filtered',
    title: '2 releases + 2 suites, 14-day window, dropped-value notice',
    meta: meta({
      scope: {
        releases: [
          { id: rid(9), name: '2026.09', status: 'in_progress' },
          { id: rid(8), name: '2026.08', status: 'released' },
        ],
        suites: ['payments', 'cart'],
        window: { from: '2026-09-05', to: '2026-09-19', days: 14, timezone: 'UTC' },
      },
      totals: { matched_runs: 18, total_runs: 143, matched_executions: 412, total_executions: 3960 },
    }),
    allProjects: false,
    releaseIds: [rid(9), rid(8)],
    suiteNames: ['payments', 'cart'],
    windowDays: 14,
    metrics: { ...METRICS, values: { ...METRICS.values, runs: 18, total_tests: 412, unknown: 3 } },
    droppedNotice: [{ dimension: 'release', values: ['2026.03'], reason: 'it belongs to a different project' }],
  },
  {
    id: 'many-suites',
    title: 'More than 3 suites (popover)',
    meta: meta({
      scope: { suites: MANY_SUITES },
      totals: { matched_runs: 90, total_runs: 143, matched_executions: 2400, total_executions: 3960 },
    }),
    allProjects: false,
    releaseIds: [],
    suiteNames: MANY_SUITES,
    windowDays: 30,
    metrics: METRICS,
  },
  {
    id: 'many-chips',
    title: 'More than 8 chips (collapsed)',
    meta: meta({
      scope: {
        releases: MANY_RELEASES.map((id) => ({ id, name: releaseLabel(id), status: 'released' })),
        suites: MANY_SUITES.slice(0, 5),
      },
      totals: { matched_runs: 40, total_runs: 143, matched_executions: 1100, total_executions: 3960 },
    }),
    allProjects: false,
    releaseIds: MANY_RELEASES,
    suiteNames: MANY_SUITES.slice(0, 5),
    windowDays: 30,
    metrics: METRICS,
  },
  {
    id: 'all-projects',
    title: 'All projects (release filter disabled)',
    meta: meta({
      scope: {
        projects: Array.from({ length: 12 }, (_, i) => ({
          id: `33333333-3333-4333-8333-${String(i).padStart(12, '0')}`,
          name: `Project ${i + 1}`,
        })),
      },
    }),
    allProjects: true,
    releaseIds: [],
    suiteNames: [],
    windowDays: 30,
    metrics: METRICS,
  },
  {
    id: 'unmeasured',
    title: 'Nothing matches (unmeasured)',
    meta: meta({
      scope: { suites: ['returns'] },
      totals: { matched_runs: 0, total_runs: 143, matched_executions: 0, total_executions: 3960 },
      measured: false,
      reason: 'No runs match this scope in the window.',
    }),
    allProjects: false,
    releaseIds: [],
    suiteNames: ['returns'],
    windowDays: 30,
    metrics: {
      values: { flaky: 12 },
      reasons: {
        runs: 'No runs match this scope in the window.',
        total_tests: 'No runs match this scope in the window.',
        pass_rate: 'No runs match this scope in the window.',
        avg_duration: 'No runs match this scope in the window.',
      },
      passRateBasis: 'executions',
    },
  },
  {
    id: 'huge-counts',
    title: 'Huge counts',
    meta: meta({
      totals: {
        matched_runs: 1_234_567,
        total_runs: 1_234_567,
        matched_executions: 987_654_321,
        total_executions: 987_654_321,
      },
    }),
    allProjects: false,
    releaseIds: [],
    suiteNames: [],
    windowDays: 30,
    metrics: {
      ...METRICS,
      values: { ...METRICS.values, runs: 1_234_567, total_tests: 987_654_321, passed: 950_000_000 },
    },
  },
  {
    id: 'totals-missing',
    title: 'Totals missing (older cached payload)',
    meta: (() => {
      const m = meta() as Partial<EnvelopeMeta>
      delete m.totals
      return m as EnvelopeMeta
    })(),
    allProjects: false,
    releaseIds: [],
    suiteNames: [],
    windowDays: 30,
    metrics: METRICS,
  },
  {
    id: 'ignored-filter',
    title: 'Ignored release filter',
    meta: meta({
      ignored_filters: [{ dimension: 'release', reason: 'Defect counts are project-wide: the release filter does not apply.' }],
    }),
    allProjects: false,
    releaseIds: [rid(9)],
    suiteNames: [],
    windowDays: 30,
    metrics: METRICS,
  },
  {
    id: 'archived-unattributed',
    title: 'Archived and unattributed releases',
    meta: meta({
      scope: {
        releases: [
          { id: rid(2), name: '2026.02', status: 'archived' },
          { id: 'unattributed', name: 'Unattributed', status: 'unattributed' },
        ],
      },
      totals: { matched_runs: 30, total_runs: 143, matched_executions: 800, total_executions: 3960 },
    }),
    allProjects: false,
    releaseIds: [rid(2), 'unattributed'],
    suiteNames: [],
    windowDays: 30,
    metrics: METRICS,
  },
  {
    id: 'loading',
    title: 'Loading',
    meta: null,
    allProjects: false,
    releaseIds: [],
    suiteNames: [],
    windowDays: 30,
    metrics: { values: {} },
    loading: true,
  },
]

export const galleryReleaseLabel = releaseLabel
