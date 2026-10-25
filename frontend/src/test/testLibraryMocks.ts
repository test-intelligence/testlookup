/**
 * Stubs for `@/hooks/useTestLibrary` (W13), for the vitests that render the
 * /test-management page with REAL `@/hooks/useTestManagement` hooks. One set-up
 * line per file:
 *
 *   vi.mock('@/hooks/useTestLibrary', async () => (await import('@/test/testLibraryMocks')).testLibraryHookMocks())
 *
 * Every hook answers an IDLE shape (no data, not loading, a `mutate`), so the
 * page renders without a request (`src/test/networkGuard.ts` fails a test that
 * makes one). Pass options for data: `testLibraryHookMocks({ health: LIBRARY_HEALTH_FIXTURE })`.
 *
 * Deliberately imports nothing from `@/hooks/useTestLibrary`: this module is
 * loaded INSIDE that module's mock factory, so the few non-hook exports are
 * restated here (their values pinned by `hooks/useTestLibrary.test.ts`).
 */
import { vi, type Mock } from 'vitest'

import type { SavedView } from '@/services/savedViewsService'
import type { TestCaseEvidenceGapResponse } from '@/types/test-management'
import type {
  LibraryHealth,
  RequirementLinkList,
  TestCaseTimeline,
  TimelineEntry,
  TraceabilityGaps,
} from '@/types/test-library'

export interface TestLibraryMockOptions {
  /** `useLibraryHealth`'s data (default: undefined, the idle / loading-free shape). */
  health?: LibraryHealth
  healthError?: unknown
  /** The ONE `mutate` every `useLibraryHealth` call returns (default: a fresh `vi.fn()`). */
  healthMutate?: Mock
  requirements?: RequirementLinkList
  /** `useTestCaseTimeline`'s loaded pages. */
  timelinePages?: TestCaseTimeline[]
  caseViews?: SavedView[]
  /** Members after `Me` (the hook always puts `Me` first). */
  ownerMembers?: { value: string; label: string }[]
  traceabilityGaps?: TraceabilityGaps
  evidenceGapPage?: TestCaseEvidenceGapResponse
}

/**
 * The health answer for the TM page vitests' three-case world
 * (`TestManagementPage.tabs.test.tsx`: "Checkout preserves cart" active and
 * automated, "Sign in with SSO" awaiting review, "Refund a partial order" a
 * draft), built so the banner facts read as they did from the 200-row roll:
 * `Cases 3`, `Awaiting review 1`, `Automated 33%`, `Stale drafts 0`, and
 * Insights shows `Review queue · 1` with the row `Sign in with SSO`.
 */
export const LIBRARY_HEALTH_FIXTURE: LibraryHealth = {
  project_id: 'project-1',
  computed_at: '2026-09-20T00:00:00Z',
  measured: true,
  total_cases: 3,
  active_cases: 1,
  retired_cases: 0,
  automated_cases: 1,
  automation_target_pct: 60,
  stale_drafts: 0,
  retired_in_open_plans: 0,
  avg_age_days: 19,
  older_than_180_days: 0,
  older_than_180_pct: 0,
  score: 70,
  tag: 'Needs attention',
  components: [
    { key: 'automation', label: 'Automated', weight: 0.35, measured: true, value: 100 / 3, numerator: 1, denominator: 3,
      detail: '1 of 3 cases automated (target 60 %)' },
    { key: 'stale_drafts', label: 'Stale drafts', weight: 0.2, measured: true, value: 100, numerator: 0, denominator: 3,
      detail: '0 of 3 cases are drafts untouched for 30 days' },
    { key: 'review_freshness', label: 'Review freshness', weight: 0.2, measured: true, value: 100 - (5 / 14) * 100,
      numerator: 5, denominator: 14, detail: 'the oldest of 1 review has waited 5 days (target under 14)' },
    { key: 'retired_in_open_plans', label: 'Retired cases in open plans', weight: 0.25, measured: true, value: 100,
      numerator: 0, denominator: 3, detail: '0 of 3 cases are retired and still in an open plan' },
  ],
  review_queue: {
    total: 1,
    measured: true,
    oldest_age_days: 5,
    items: [{ id: 'case-2', title: 'Sign in with SSO', status: 'review_requested', owner: null, age_days: 5, detail: null }],
  },
  strategy_gaps: [
    { kind: 'automation_below_target', severity: 'warn', title: 'Automation below target',
      detail: '33 % automated · target 60 %', count: 27, pill: '27 % gap' },
  ],
  flaky_active: { total: 0, items: [], measured: false, reason: 'No flaky scores have been computed for this project yet.' },
  no_owner: { total: 0, items: [], measured: true },
  suite_names: [],
  suite_names_total: 0,
  requirements: null,
}

function swr<T>(data: T | undefined, error: unknown = undefined, mutate: Mock = vi.fn()) {
  return { data, error, isLoading: false, isValidating: false, mutate }
}

export function testLibraryHookMocks(options: TestLibraryMockOptions = {}) {
  const healthMutate = options.healthMutate ?? vi.fn()
  const timelinePages = options.timelinePages ?? []
  const lastPage = timelinePages[timelinePages.length - 1]
  const timelineItems: TimelineEntry[] = timelinePages.flatMap((page) => page.items)
  return {
    CASE_VIEW_PAGE: 'test_management',
    CASE_VIEW_KIND: 'test_case_view',
    isCaseView: (view: SavedView) => Boolean(view.filters) && view.filters.kind === 'test_case_view',
    useLibraryHealth: vi.fn(() => swr(options.health, options.healthError, healthMutate)),
    refreshLibraryHealth: vi.fn(() => Promise.resolve(undefined)),
    useTestCaseTimeline: vi.fn(() => ({
      items: timelineItems,
      pages: timelinePages,
      automationLinked: timelinePages[0]?.automation_linked,
      hasMore: Boolean(lastPage?.next_cursor),
      loadMore: vi.fn(),
      isLoading: false,
      isLoadingMore: false,
      error: undefined,
      mutate: vi.fn(),
    })),
    useCaseRequirements: vi.fn(() => swr(options.requirements)),
    useTraceabilityGaps: vi.fn(() => swr(options.traceabilityGaps)),
    useEvidenceGapPage: vi.fn(() => swr(options.evidenceGapPage)),
    useCaseViews: vi.fn(() => ({
      views: options.caseViews ?? [],
      error: undefined,
      isLoading: false,
      mutate: vi.fn(),
    })),
    useCaseOwnerOptions: vi.fn(() => ({
      options: [{ value: 'me', label: 'Me' }, ...(options.ownerMembers ?? [])],
      error: undefined,
      isLoading: false,
      mutate: vi.fn(),
    })),
  }
}
