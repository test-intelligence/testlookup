/**
 * W29a UIX-02 (contract §2.2 test 6): the Failures page's category card and kind
 * chips draw their colours from the ONE failure-category map
 * (`components/charts/failureTaxonomy`), so a row, its kind badge and the
 * Overview donut agree:
 *  - each row's mark is its taxon's colour (and decal);
 *  - "Assertion / product" and its PRODUCT badge carry the same colour token;
 *  - the three infrastructure rows share the colour and differ by decal;
 *  - the unknown bar's hatch is tokens only (no `#` literal);
 *  - the active kind chip's count is neutral text (`--color-text`), not the
 *    kind colour as text (~3.3:1 on Lab).
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import FailureAnalysisPage from './FailureAnalysisPage'
import {
  FAILURE_CATEGORY_PATTERNS,
  INFRASTRUCTURE_DETAIL_DECALS,
  failureCategoryColor,
  failureKindColor,
} from '@/components/charts/failureTaxonomy'
import { DEFAULT_TIME_WINDOW_DAYS, useTimeWindowStore } from '@/store/timeWindowStore'
import { useAuthStore } from '@/store/authStore'

// The card sits behind a SectionErrorBoundary: the real reporter batches for 5 s.
vi.mock('@/utils/errorReporting', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/utils/errorReporting')>(),
  reportBoundaryError: vi.fn(),
}))
vi.mock('@/services/savedViewsService', () => ({
  listSavedViews: vi.fn(async () => []),
  createSavedView: vi.fn(),
  updateSavedView: vi.fn(),
  deleteSavedView: vi.fn(),
}))
vi.mock('@/hooks/useMetrics', () => ({
  useFlakyTests: vi.fn(() => ({ data: { items: [] }, isLoading: false })),
  useFailureCategories: vi.fn(),
  useTopFailing: vi.fn(() => ({ data: { items: [] }, isLoading: false })),
  useTrendData: vi.fn(() => ({
    data: { data: [{ date: '2026-07-01', passed: 12, failed: 8, skipped: 0, broken: 0, total: 20, pass_rate: 60 }] },
    isLoading: false,
  })),
  useKindEvidence: vi.fn(() => ({ data: undefined, isLoading: false })),
}))
vi.mock('@/hooks/useFailureInsights', () => ({
  TIME_TO_FIX_KEY: 'analytics-time-to-fix',
  useTimeToFix: vi.fn(() => ({ data: undefined, isLoading: false, error: undefined })),
}))
vi.mock('@/hooks/useClassificationHints', () => ({
  PREVIEW_MIN_CHARS: 3,
  useClassificationHints: vi.fn(() => ({ data: { items: [], total: 0, max_hints: 50 }, error: undefined, isLoading: false, mutate: vi.fn() })),
  useClassificationHintPreview: vi.fn(() => ({ data: undefined, error: undefined })),
}))
vi.mock('@/hooks/useFeatureFlags', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useFeatureFlags')>(),
  useFeatureFlagStatus: () => undefined,
}))
vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(() => ({ data: { items: [] }, isLoading: false })),
}))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: { activeProjectId: string; activeProject: { id: string; name: string } | null }) => unknown) =>
    selector({ activeProjectId: 'proj-1', activeProject: { id: 'proj-1', name: 'Project One' } })),
}))
vi.mock('@/services/flakyQuarantineService', () => ({ flakyQuarantineService: { propose: vi.fn() } }))
vi.mock('@/services/aiFeedbackService', () => ({ aiFeedbackService: { submitFeedback: vi.fn() } }))
vi.mock('@/hooks/useJiraDefects', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useJiraDefects')>(),
  useJiraDefectMetadata: vi.fn(() => ({ metadata: undefined, isLoading: false, isError: false })),
}))
vi.mock('@/hooks/useAnalysisLookup', () => ({
  useAnalysisLookup: vi.fn(() => ({ lookup: undefined, isLoading: true, isError: false })),
}))
vi.mock('@/components/reports/catalogue/FailureGroupsSection', () => ({ default: () => <div data-testid="section-groups" /> }))
vi.mock('@/components/reports/catalogue/FailuresDrill', () => ({ default: () => <div data-testid="section-drill" /> }))
vi.mock('@/components/reports/catalogue/ScatterSection', () => ({ default: () => <div data-testid="section-scatter" /> }))
vi.mock('@/hooks/useCommitAttribution', () => ({
  useSuspects: vi.fn(() => ({
    ranking: { source: 'unavailable' }, suspects: [], available: false, caveat: '',
    hasLocationSignal: false, isLoading: false, isError: false, refresh: vi.fn(),
  })),
}))

async function seedCategories() {
  const { useFailureCategories } = await import('@/hooks/useMetrics')
  ;(useFailureCategories as ReturnType<typeof vi.fn>).mockReturnValue({
    data: {
      items: [
        { category: 'PRODUCT_BUG', count: 5, kind: 'product' },
        { category: 'TIMEOUT', count: 2, kind: 'infrastructure' },
        { category: 'NETWORK', count: 1, kind: 'infrastructure' },
        { category: 'INFRASTRUCTURE', count: 2, kind: 'infrastructure' },
        { category: 'TEST_DATA', count: 1, kind: 'test_code' },
        { category: 'UNKNOWN', count: 3, kind: 'unknown' },
      ],
      by_kind: [
        { kind: 'product', count: 5 },
        { kind: 'test_code', count: 1 },
        { kind: 'infrastructure', count: 5 },
        { kind: 'unknown', count: 3 },
      ],
    },
    isLoading: false,
  })
}

function renderCategories() {
  return render(
    <MemoryRouter initialEntries={['/failure-analysis?tab=categories']}>
      <Routes>
        <Route path="/failure-analysis" element={<FailureAnalysisPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

/** The card's row whose accessible name starts with `label`. */
function categoryRow(grid: HTMLElement, label: string): HTMLElement {
  const row = Array.from(grid.querySelectorAll<HTMLElement>('[role="row"]'))
    .find((el) => (el.getAttribute('aria-label') ?? '').startsWith(`${label}:`))
  if (!row) throw new Error(`no category row "${label}"`)
  return row
}

/** The row's category mark: its taxon, decal and pattern fill colour. */
function markOf(row: HTMLElement) {
  const svg = row.querySelector<SVGElement>('[data-category-name] svg[data-failure-category]')
  if (!svg) throw new Error('no category mark')
  return {
    taxon: svg.getAttribute('data-failure-category'),
    decal: svg.getAttribute('data-decal'),
    color: svg.querySelector('pattern rect')?.getAttribute('fill') ?? null,
  }
}

/** The row's kind badge (FailureKindBadge): its dot's colour. */
function badgeDotOf(row: HTMLElement): string {
  const badge = row.querySelector<HTMLElement>('[data-category-badge] > span')
  const dot = badge?.querySelector<HTMLElement>('span[aria-hidden]')
  if (!dot) throw new Error('no kind badge dot')
  return dot.style.background
}

describe('FailureAnalysisPage — the category card follows the one taxonomy map (W29a UIX-02)', () => {
  beforeEach(() => {
    useAuthStore.setState({ user: { id: 'eng', role: 'QA_ENGINEER' } as never })
    try { localStorage.removeItem('testlookup-time-window') } catch { /* ignore */ }
    useTimeWindowStore.setState({ days: DEFAULT_TIME_WINDOW_DAYS })
  })

  it("draws each row's mark in its taxon's colour and shape", async () => {
    await seedCategories()
    renderCategories()
    await screen.findByText('Failure category distribution')
    const grid = document.querySelector<HTMLElement>('[data-category-grid]') as HTMLElement

    const expected: [string, keyof typeof FAILURE_CATEGORY_PATTERNS, string][] = [
      ['Unknown', 'unknown', FAILURE_CATEGORY_PATTERNS.unknown.decal],
      ['Assertion / product', 'product_bug', FAILURE_CATEGORY_PATTERNS.product_bug.decal],
      ['Test code / data', 'test_data', FAILURE_CATEGORY_PATTERNS.test_data.decal],
      ['Timeout', 'infrastructure', INFRASTRUCTURE_DETAIL_DECALS.timeout],
      ['Network / 5xx', 'infrastructure', INFRASTRUCTURE_DETAIL_DECALS.network],
      ['Infra / runner', 'infrastructure', INFRASTRUCTURE_DETAIL_DECALS.runner],
    ]
    for (const [label, taxon, decal] of expected) {
      const row = categoryRow(grid, label)
      expect(markOf(row), label).toEqual({ taxon, decal, color: failureCategoryColor(taxon) })
      // The bar is the row's colour too (the unknown bar is the token hatch, below).
      if (taxon !== 'unknown') expect(row.querySelector<HTMLElement>('i')?.style.background, label).toBe(failureCategoryColor(taxon))
    }
    // The category words are neutral text: no row paints its label with the category colour.
    for (const name of grid.querySelectorAll<HTMLElement>('[data-category-name]')) {
      expect(name.getAttribute('style')).toBeNull()
    }
  })

  it('gives "Assertion / product" and its PRODUCT badge one colour, and each infrastructure row its badge\'s', async () => {
    await seedCategories()
    renderCategories()
    await screen.findByText('Failure category distribution')
    const grid = document.querySelector<HTMLElement>('[data-category-grid]') as HTMLElement

    const assertion = categoryRow(grid, 'Assertion / product')
    expect(markOf(assertion).color).toBe(failureKindColor('product'))
    expect(badgeDotOf(assertion)).toBe(markOf(assertion).color)
    const testCode = categoryRow(grid, 'Test code / data')
    expect(badgeDotOf(testCode)).toBe(markOf(testCode).color)
    for (const label of ['Timeout', 'Network / 5xx', 'Infra / runner']) {
      const row = categoryRow(grid, label)
      expect(badgeDotOf(row), label).toBe(markOf(row).color)
      expect(badgeDotOf(row), label).toBe(failureKindColor('infrastructure'))
    }
  })

  it('draws the three infrastructure rows in one colour, told apart by their decal', async () => {
    await seedCategories()
    renderCategories()
    await screen.findByText('Failure category distribution')
    const grid = document.querySelector<HTMLElement>('[data-category-grid]') as HTMLElement

    const marks = ['Timeout', 'Network / 5xx', 'Infra / runner'].map((label) => markOf(categoryRow(grid, label)))
    expect(new Set(marks.map((m) => m.color))).toEqual(new Set([failureCategoryColor('infrastructure')]))
    expect(new Set(marks.map((m) => m.decal)).size).toBe(3)
    // The bars carry the row colour too.
    const bar = categoryRow(grid, 'Timeout').querySelector<HTMLElement>('i')
    expect(bar?.style.background).toBe(failureCategoryColor('infrastructure'))
  })

  it("hatches the unknown bar with tokens only and colours its share with the warning token", async () => {
    await seedCategories()
    renderCategories()
    await screen.findByText('Failure category distribution')
    const grid = document.querySelector<HTMLElement>('[data-category-grid]') as HTMLElement

    const unknown = categoryRow(grid, 'Unknown')
    const style = unknown.querySelector('i')?.getAttribute('style') ?? ''
    expect(style).toContain('repeating-linear-gradient')
    expect(style).toContain('var(--color-text-muted)')
    expect(style).not.toContain('#')
    const share = unknown.lastElementChild as HTMLElement
    expect(share.style.color).toBe('var(--ui-warning)')
  })

  it("writes the active kind chip's count in --color-text, not the kind colour", async () => {
    await seedCategories()
    renderCategories()
    const group = await screen.findByRole('group', { name: /Filter by failure kind/i })
    const product = within(group).getByRole('button', { name: /Product\s*5/i })
    fireEvent.click(product)
    expect(product).toHaveAttribute('aria-pressed', 'true')
    const count = within(product).getByText('5')
    expect(count.style.color).toBe('var(--color-text)')
    // The chip's colour stays a mark: its swatch and its tint.
    const swatch = product.querySelector<HTMLElement>('span[aria-hidden]')
    expect(swatch?.style.background).toBe(failureKindColor('product'))
    // An inactive chip's count stays faint.
    const infra = within(group).getByRole('button', { name: /Infrastructure\s*5/i })
    expect(within(infra).getByText('5').style.color).toBe('var(--color-text-faint)')
  })
})
