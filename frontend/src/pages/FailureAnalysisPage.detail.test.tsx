/**
 * W07 FAIL-01: a Top failing row expands to its latest failure — the first
 * error line, the stack head, the owner and "Open in run".
 *
 * Pins: rows start collapsed (no `[data-failure-detail]`, the fold budget is the
 * collapsed table's); the toggle opens and closes one row; the error and the
 * stack render as `<pre>` TEXT (a hostile `<img onerror>` stays text); "Open in
 * run" links the test's page in that run, built from the two ids only; the
 * owner chip says where the owner came from, or "Unassigned"; a missing
 * message reads "No message recorded"; All Projects reads one sentence.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import FailureAnalysisPage from './FailureAnalysisPage'
import { ownerChipText } from './failures/failureInsights'
import { DEFAULT_TIME_WINDOW_DAYS, useTimeWindowStore } from '@/store/timeWindowStore'
import { useAuthStore } from '@/store/authStore'
import type { TopFailingItem } from '@/types/analytics'

vi.mock('@/services/savedViewsService', () => ({
  listSavedViews: vi.fn(async () => []),
  createSavedView: vi.fn(),
  updateSavedView: vi.fn(),
  deleteSavedView: vi.fn(),
}))
vi.mock('@/hooks/useMetrics', () => ({
  useFlakyTests: vi.fn(),
  useFailureCategories: vi.fn(),
  useTopFailing: vi.fn(),
  useTrendData: vi.fn(),
  useKindEvidence: vi.fn(() => ({ data: undefined, isLoading: false })),
}))
vi.mock('@/hooks/useFailureInsights', () => ({
  TIME_TO_FIX_KEY: 'analytics-time-to-fix',
  useTimeToFix: vi.fn(() => ({ data: undefined, isLoading: false, error: undefined })),
}))
vi.mock('@/hooks/useClassificationHints', () => ({
  PREVIEW_MIN_CHARS: 3,
  useClassificationHints: vi.fn(() => ({ data: undefined, error: undefined, isLoading: false, mutate: vi.fn() })),
  useClassificationHintPreview: vi.fn(() => ({ data: undefined, error: undefined })),
}))
vi.mock('@/hooks/useFeatureFlags', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useFeatureFlags')>(),
  useFeatureFlagStatus: () => true,
}))
vi.mock('@/hooks/useRuns', () => ({ useRuns: vi.fn(() => ({ data: { items: [] }, isLoading: false })) }))
const store = vi.hoisted(() => ({
  state: { activeProjectId: 'proj-1', activeProject: { id: 'proj-1', name: 'Project One' } as { id: string; name: string } | null },
}))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: typeof store.state) => unknown) => selector(store.state)),
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

const RUN_ID = '9a1f0c2e-1111-4222-8333-444455556666'
const CASE_ID = '7b2e1d3f-aaaa-4bbb-8ccc-ddddeeeeffff'

const DETAIL: TopFailingItem = {
  test_name: 'checkout_flow', fail_count: 4, test_fingerprint: 'fp-top', suite_name: 'Checkout',
  last_failed: new Date(Date.now() - 3 * 3600_000).toISOString(), last_failed_run_id: RUN_ID, last_failed_build: '4312',
  detail_available: true,
  latest_run_id: RUN_ID,
  latest_test_case_id: CASE_ID,
  latest_failed_at: new Date(Date.now() - 3 * 3600_000).toISOString(),
  latest_error: 'AssertionError: expected 3 but was 4',
  latest_stack_head: 'Traceback (most recent call last):\n  File "tests/test_checkout.py", line 42, in test_flow\n    assert total == 3',
  owner: 'Priya Nair', owner_source: 'assignee', owner_team: 'Payments',
}

async function seed(items: TopFailingItem[], flakyItems: unknown[] = []) {
  const { useFlakyTests, useFailureCategories, useTopFailing, useTrendData } = await import('@/hooks/useMetrics')
  ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: flakyItems }, isLoading: false })
  ;(useFailureCategories as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
  ;(useTopFailing as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items }, isLoading: false })
  ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
    data: { data: [{ date: '2026-07-01', passed: 6, failed: 4, skipped: 0, broken: 0, total: 10, pass_rate: 60 }] },
    isLoading: false,
  })
}

function renderFailures() {
  return render(
    <MemoryRouter initialEntries={['/failures']}>
      <Routes>
        <Route path="/failures" element={<FailureAnalysisPage />} />
        <Route path="/runs/:runId/tests/:testId" element={<div data-testid="test-page" />} />
      </Routes>
    </MemoryRouter>,
  )
}

const toggleFor = (name: string) => screen.getByRole('button', { name: `Show failure detail for ${name}` })
const detailRows = () => document.querySelectorAll('[data-failure-detail]')

beforeEach(() => {
  store.state = { activeProjectId: 'proj-1', activeProject: { id: 'proj-1', name: 'Project One' } }
  useAuthStore.setState({ user: { id: 'eng', role: 'QA_ENGINEER' } as never })
  try { localStorage.removeItem('testlookup-time-window') } catch { /* ignore */ }
  useTimeWindowStore.setState({ days: DEFAULT_TIME_WINDOW_DAYS })
})

describe('Top failing rows: the failure detail (W07 FAIL-01)', () => {
  it('rows start collapsed: a toggle before the name, and no detail row', async () => {
    await seed([DETAIL, { ...DETAIL, test_name: 'login_flow', test_fingerprint: 'fp-2' }])
    renderFailures()
    const toggle = await screen.findByRole('button', { name: 'Show failure detail for checkout_flow' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(toggle).toHaveAttribute('data-failure-detail-toggle')
    expect(detailRows()).toHaveLength(0)
    // BEFORE the name, in the Test cell (204 px + the 16 px toggle = the old 220 px).
    const cell = toggle.closest('td') as HTMLElement
    expect(toggle.compareDocumentPosition(within(cell).getByRole('link', { name: 'checkout_flow' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(cell.style.minWidth).toBe('204px')
  })

  it('expands one row to its error, stack head, owner and "Open in run", and collapses it again', async () => {
    await seed([DETAIL, { ...DETAIL, test_name: 'login_flow', test_fingerprint: 'fp-2' }])
    renderFailures()
    fireEvent.click(await screen.findByRole('button', { name: 'Show failure detail for checkout_flow' }))
    expect(toggleFor('checkout_flow')).toHaveAttribute('aria-expanded', 'true')
    expect(toggleFor('login_flow')).toHaveAttribute('aria-expanded', 'false')
    expect(detailRows()).toHaveLength(1)
    const detail = detailRows()[0] as HTMLElement
    // Right under its own row, across the whole table.
    expect(detail.previousElementSibling).toBe(toggleFor('checkout_flow').closest('tr'))
    expect(detail.querySelector('td')).toHaveAttribute('colspan', '6')
    expect(detail).toHaveTextContent('Latest failure 3h ago · build 4312')
    expect(detail.querySelector('pre[data-failure-error]')).toHaveTextContent('AssertionError: expected 3 but was 4')
    const stack = detail.querySelector('pre[data-failure-stack]') as HTMLElement
    expect(stack.textContent).toBe(DETAIL.latest_stack_head)
    expect(within(detail).getByText('first 20 lines')).toBeInTheDocument()
    expect(detail.querySelector('[data-failure-owner]')).toHaveTextContent('Priya Nair · assignee · Payments')
    expect(within(detail).getByRole('link', { name: 'Open in run' })).toHaveAttribute('href', `/runs/${RUN_ID}/tests/${CASE_ID}`)

    fireEvent.click(toggleFor('checkout_flow'))
    expect(detailRows()).toHaveLength(0)
    expect(toggleFor('checkout_flow')).toHaveAttribute('aria-expanded', 'false')
  })

  it('"Open in run" opens the test\'s page in that run', async () => {
    await seed([DETAIL])
    renderFailures()
    fireEvent.click(await screen.findByRole('button', { name: 'Show failure detail for checkout_flow' }))
    fireEvent.click(screen.getByRole('link', { name: 'Open in run' }))
    expect(await screen.findByTestId('test-page')).toBeInTheDocument()
  })

  it('a missing message reads "No message recorded"; no trace, no stack block; no ids, no link', async () => {
    await seed([{ ...DETAIL, latest_error: null, latest_stack_head: null, latest_test_case_id: null }])
    renderFailures()
    fireEvent.click(await screen.findByRole('button', { name: 'Show failure detail for checkout_flow' }))
    const detail = detailRows()[0] as HTMLElement
    expect(within(detail).getByText('No message recorded')).toBeInTheDocument()
    expect(detail.querySelector('[data-failure-error]')).toBeNull()
    expect(detail.querySelector('[data-failure-stack]')).toBeNull()
    expect(within(detail).queryByText('first 20 lines')).toBeNull()
    expect(within(detail).queryByRole('link', { name: 'Open in run' })).toBeNull()
  })

  it('All Projects: the detail row holds only "Select a project to see failure detail."', async () => {
    store.state = { activeProjectId: '__ALL__', activeProject: null }
    await seed([{
      test_name: 'checkout_flow', fail_count: 4, test_fingerprint: 'fp-top', detail_available: false,
      latest_run_id: null, latest_test_case_id: null, latest_failed_at: null, latest_error: null,
      latest_stack_head: null, owner: null, owner_source: null, owner_team: null,
    }])
    renderFailures()
    fireEvent.click(await screen.findByRole('button', { name: 'Show failure detail for checkout_flow' }))
    const detail = detailRows()[0] as HTMLElement
    expect(detail.textContent).toBe('Select a project to see failure detail.')
  })

  it('a hostile error and stack render as text inside their <pre>, never as markup', async () => {
    await seed([{
      ...DETAIL,
      latest_error: '<img src=x onerror=alert(1)>',
      latest_stack_head: '<script>alert(2)</script>\n<b>bold</b>',
    }])
    renderFailures()
    fireEvent.click(await screen.findByRole('button', { name: 'Show failure detail for checkout_flow' }))
    const detail = detailRows()[0] as HTMLElement
    expect(detail.querySelector('pre[data-failure-error]')?.textContent).toBe('<img src=x onerror=alert(1)>')
    expect(detail.querySelector('pre[data-failure-stack]')?.textContent).toBe('<script>alert(2)</script>\n<b>bold</b>')
    expect(detail.querySelector('img, script, b')).toBeNull()
  })

  it('a row only the flaky list names says it has no failure detail of its own', async () => {
    await seed([], [{
      test_name: 'flaky_only', test_fingerprint: 'fp-f', fail_count: 2, total_runs: 6, failure_rate_pct: 33, source: 'auto',
    }])
    renderFailures()
    fireEvent.click(await screen.findByRole('button', { name: 'Show failure detail for flaky_only' }))
    expect(detailRows()[0]).toHaveTextContent('No failure detail recorded for this test in this window.')
  })
})

describe('ownerChipText — where the owner came from', () => {
  it.each([
    [{ owner: 'Priya Nair', owner_source: 'assignee', owner_team: 'Payments' }, 'Priya Nair · assignee · Payments'],
    [{ owner: 'Priya Nair', owner_source: 'assignee', owner_team: null }, 'Priya Nair · assignee'],
    [{ owner: 'Payments', owner_source: 'rule', owner_team: 'Payments' }, 'Payments · ownership rule'],
    [{ owner: 'Checkout', owner_source: 'component_map', owner_team: 'Checkout' }, 'Checkout · component map'],
    [{ owner: 'qa-core', owner_source: 'test_label', owner_team: 'qa-core' }, 'qa-core · owner label'],
    [{ owner: 'Someone', owner_source: 'something_new', owner_team: null }, 'Someone'],
    [{ owner: null, owner_source: null, owner_team: null }, null],
    [{ owner: '   ', owner_source: 'assignee', owner_team: 'Payments' }, null],
  ] as const)('%j → %s', (input, expected) => {
    expect(ownerChipText(input)).toBe(expected)
  })

  it('the chip reads "Unassigned" when nobody owns the test', async () => {
    await seed([{ ...DETAIL, owner: null, owner_source: null, owner_team: null }])
    renderFailures()
    fireEvent.click(await screen.findByRole('button', { name: 'Show failure detail for checkout_flow' }))
    expect(detailRows()[0].querySelector('[data-failure-owner]')).toHaveTextContent(/^Unassigned$/)
  })

  it('a rule owner reads "· ownership rule"', async () => {
    await seed([{ ...DETAIL, owner: 'Payments', owner_source: 'rule', owner_team: 'Payments' }])
    renderFailures()
    fireEvent.click(await screen.findByRole('button', { name: 'Show failure detail for checkout_flow' }))
    expect(detailRows()[0].querySelector('[data-failure-owner]')).toHaveTextContent(/^Payments · ownership rule$/)
  })
})
