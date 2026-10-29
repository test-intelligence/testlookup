/**
 * W27a PAN-04 (`W27a-contract.md` §2.4, assertion 2): /failures' forms and
 * details open IN THE PAGE.
 *
 *  - a row's Mute / Jira / Suspects open in a full-width row RIGHT AFTER that
 *    row (`td[colspan="6"]`), above the row's W07 detail when both are open;
 *  - one row form per table: another row's action while Mute has typed text
 *    asks first (Keep editing keeps it; Discard changes opens the other), a
 *    clean one is replaced at once;
 *  - a Mute whose request answers after another form replaced it does not
 *    close that form;
 *  - Correct / Classification hints / Classify open right after the page
 *    header, one at a time;
 *  - Escape inside Mute closes it (it had none as a dialog: D-13);
 *  - no `aria-modal`, no `role="dialog"` anywhere.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import FailureAnalysisPage from './FailureAnalysisPage'
import { DEFAULT_TIME_WINDOW_DAYS, useTimeWindowStore } from '@/store/timeWindowStore'
import { useAuthStore } from '@/store/authStore'

vi.mock('@/services/savedViewsService', () => ({
  listSavedViews: vi.fn(async () => []),
  createSavedView: vi.fn(),
  updateSavedView: vi.fn(),
  deleteSavedView: vi.fn(),
}))
const metrics = vi.hoisted(() => ({
  top: [] as unknown[],
  categories: [] as unknown[],
}))
vi.mock('@/hooks/useMetrics', () => ({
  useFlakyTests: () => ({ data: { items: [] }, isLoading: false }),
  useFailureCategories: () => ({ data: { items: metrics.categories }, isLoading: false }),
  useTopFailing: () => ({ data: { items: metrics.top }, isLoading: false }),
  useTrendData: () => ({
    data: { data: [{ date: '2026-07-01', passed: 6, failed: 4, skipped: 0, broken: 0, total: 10, pass_rate: 60 }] },
    isLoading: false,
  }),
  useKindEvidence: () => ({ data: undefined, isLoading: false }),
}))
vi.mock('@/hooks/useFailureInsights', () => ({
  TIME_TO_FIX_KEY: 'analytics-time-to-fix',
  useTimeToFix: () => ({ data: undefined, isLoading: false, error: undefined }),
}))
vi.mock('@/hooks/useClassificationHints', () => ({
  PREVIEW_MIN_CHARS: 3,
  useClassificationHints: () => ({ data: { items: [], total: 0, max_hints: 50 }, error: undefined, isLoading: false, mutate: vi.fn() }),
  useClassificationHintPreview: () => ({ data: undefined, error: undefined }),
}))
vi.mock('@/hooks/useFeatureFlags', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useFeatureFlags')>(),
  useFeatureFlagStatus: (key: string) => (key === 'flaky_auto_quarantine' ? true : undefined),
}))
vi.mock('@/hooks/useRuns', () => ({ useRuns: () => ({ data: { items: [] }, isLoading: false }) }))
vi.mock('@/hooks/useAnalyticsView', () => ({
  useAnalyticsView: () => ({ widgetIds: [], setWidgets: vi.fn(), save: vi.fn() }),
}))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: (state: { activeProjectId: string; activeProject: { id: string; name: string } }) => unknown) =>
    selector({ activeProjectId: 'proj-1', activeProject: { id: 'proj-1', name: 'Project One' } }),
}))
const propose = vi.hoisted(() => vi.fn())
vi.mock('@/services/flakyQuarantineService', () => ({ flakyQuarantineService: { propose } }))
vi.mock('@/services/aiFeedbackService', () => ({ aiFeedbackService: { submitFeedback: vi.fn() } }))
vi.mock('@/hooks/useJiraDefects', () => ({
  useJiraDefectMetadata: () => ({ metadata: undefined, isLoading: true, isError: false }),
  useJiraDefectPreview: () => ({ preview: undefined, isLoading: true, isError: false }),
}))
vi.mock('@/hooks/useAnalysisLookup', () => ({
  useAnalysisLookup: () => ({ lookup: undefined, isLoading: true, isError: false }),
}))
vi.mock('@/components/reports/catalogue/FailureGroupsSection', () => ({ default: () => <div /> }))
vi.mock('@/components/reports/catalogue/FailuresDrill', () => ({ default: () => <div /> }))
vi.mock('@/components/reports/catalogue/ScatterSection', () => ({ default: () => <div /> }))
vi.mock('@/hooks/useCommitAttribution', () => ({
  useSuspects: () => ({
    ranking: { source: 'unavailable' }, suspects: [], available: false, caveat: '',
    hasLocationSignal: false, isLoading: false, isError: false, refresh: vi.fn(),
  }),
}))
// W23b FAIL-12: the narrowing block in the Suspects section reads nothing in a unit test (the network guard).
vi.mock('@/hooks/useBisectNarrowing', async () => {
  const { narrowing } = await import('@/test/bisectDriftMocks')
  return {
    bisectKey: () => null,
    useBisectNarrowing: () => ({ data: narrowing(), error: undefined, isLoading: false, retry: vi.fn() }),
  }
})
const toast = vi.hoisted(() => Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), loading: vi.fn(), dismiss: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast }))

const CHECKOUT = {
  test_name: 'checkout_flow', fail_count: 4, test_fingerprint: 'fp-top', suite_name: 'Checkout',
  last_failed_run_id: 'run-9',
}
const LOGIN = { test_name: 'login_flow', fail_count: 2, test_fingerprint: 'fp-login', suite_name: 'Auth', last_failed_run_id: 'run-7' }

function renderFailures(search = '') {
  return render(
    <MemoryRouter initialEntries={[`/failure-analysis${search}`]}>
      <Routes>
        <Route path="/failure-analysis" element={<FailureAnalysisPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

const rowOf = (name: string) =>
  (screen.getByRole('button', { name: `Mute test ${name}` }).closest('tr') as HTMLElement)
const MUTE = 'Mute test (propose quarantine)'
const reasonBox = (form: HTMLElement) => within(form).getByPlaceholderText(/Why should this test stop gating runs/)

function expectNoModal() {
  expect(document.querySelector('[aria-modal]')).toBeNull()
  expect(screen.queryByRole('dialog')).toBeNull()
}

beforeEach(() => {
  vi.clearAllMocks()
  try { localStorage.removeItem('testlookup-time-window') } catch { /* ignore */ }
  useTimeWindowStore.setState({ days: DEFAULT_TIME_WINDOW_DAYS })
  useAuthStore.setState({ user: { id: 'lead', role: 'QA_LEAD' } as never })
  metrics.top = [CHECKOUT, LOGIN]
  metrics.categories = []
})

describe('/failures row actions open under their row (W27 PAN-04)', () => {
  it("Mute renders in the <tr> right after its row, across all six columns, above the row's W07 detail", async () => {
    renderFailures()
    fireEvent.click(await screen.findByRole('button', { name: 'Mute test checkout_flow' }))
    const form = screen.getByRole('region', { name: MUTE })
    const formRow = form.closest('tr') as HTMLElement
    expect(formRow).toHaveAttribute('data-in-page-row')
    expect(formRow.previousElementSibling).toBe(rowOf('checkout_flow'))
    expect(formRow.querySelectorAll('td')).toHaveLength(1)
    expect(formRow.querySelector('td')).toHaveAttribute('colspan', '6')
    // The first field takes focus, as the dialog's did.
    expect(document.activeElement).toBe(reasonBox(form))
    expectNoModal()

    // The W07 detail opens BELOW the form row.
    fireEvent.click(screen.getByRole('button', { name: 'Show failure detail for checkout_flow' }))
    expect(formRow.nextElementSibling).toHaveAttribute('data-failure-detail')
  })

  it('Escape inside Mute closes it; Cancel closes it; neither sends', async () => {
    renderFailures()
    fireEvent.click(await screen.findByRole('button', { name: 'Mute test checkout_flow' }))
    let form = screen.getByRole('region', { name: MUTE })
    fireEvent.change(reasonBox(form), { target: { value: 'typed' } })
    fireEvent.keyDown(reasonBox(form), { key: 'Escape' })
    expect(screen.queryByRole('region', { name: MUTE })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Mute test checkout_flow' }))
    form = screen.getByRole('region', { name: MUTE })
    expect(reasonBox(form)).toHaveValue('')
    fireEvent.click(within(form).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('region', { name: MUTE })).toBeNull()
    expect(propose).not.toHaveBeenCalled()
  })

  it("another row's Jira while Mute has typed text asks first; Keep editing keeps it, Discard changes opens the Jira form", async () => {
    renderFailures()
    fireEvent.click(await screen.findByRole('button', { name: 'Mute test checkout_flow' }))
    const mute = screen.getByRole('region', { name: MUTE })
    fireEvent.change(reasonBox(mute), { target: { value: 'Half a reason' } })

    fireEvent.click(screen.getByRole('button', { name: 'Create Jira issue for login_flow' }))
    expect(within(mute).getByRole('group', { name: `Discard your changes to "${MUTE}"?` })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Create Jira issue' })).toBeNull()
    fireEvent.click(within(mute).getByRole('button', { name: 'Keep editing' }))
    expect(reasonBox(mute)).toHaveValue('Half a reason')
    expect(screen.queryByRole('region', { name: 'Create Jira issue' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Create Jira issue for login_flow' }))
    fireEvent.click(within(mute).getByRole('button', { name: 'Discard changes' }))
    expect(screen.queryByRole('region', { name: MUTE })).toBeNull()
    const jira = screen.getByRole('region', { name: 'Create Jira issue' })
    expect((jira.closest('tr') as HTMLElement).previousElementSibling).toBe(rowOf('login_flow'))
    expect(propose).not.toHaveBeenCalled()
    expectNoModal()
  })

  it('a clean form is replaced at once (one per table): Mute, then Suspects on the same row', async () => {
    renderFailures()
    fireEvent.click(await screen.findByRole('button', { name: 'Mute test checkout_flow' }))
    expect(screen.getByRole('region', { name: MUTE })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Suspects for checkout_flow' }))
    expect(screen.queryByRole('region', { name: MUTE })).toBeNull()
    const suspects = screen.getByRole('complementary', { name: 'Suspects: checkout_flow' })
    expect((suspects.closest('tr') as HTMLElement).previousElementSibling).toBe(rowOf('checkout_flow'))
    expect(document.querySelectorAll('tr[data-in-page-row]')).toHaveLength(1)
    // W23b FAIL-12: the narrowing block renders inside the same section (no new row, panel or modal).
    expect(within(suspects).getByRole('region', { name: 'Narrowed range' })).toBeInTheDocument()
  })

  it('a Mute that answers after another form replaced it does not close that form', async () => {
    let answer: (value: unknown) => void = () => undefined
    propose.mockImplementation(() => new Promise((resolve) => { answer = resolve }))
    renderFailures()
    fireEvent.click(await screen.findByRole('button', { name: 'Mute test checkout_flow' }))
    const mute = screen.getByRole('region', { name: MUTE })
    fireEvent.change(reasonBox(mute), { target: { value: 'Flaky on the shared runner' } })
    fireEvent.click(within(mute).getByRole('button', { name: 'Propose quarantine' }))
    expect(propose).toHaveBeenCalledTimes(1)

    // In flight: Jira on another row, Discard changes.
    fireEvent.click(screen.getByRole('button', { name: 'Create Jira issue for login_flow' }))
    fireEvent.click(within(mute).getByRole('button', { name: 'Discard changes' }))
    expect(screen.getByRole('region', { name: 'Create Jira issue' })).toBeInTheDocument()

    await act(async () => {
      answer({ status: 'PROPOSED' })
    })
    await waitFor(() => expect(toast.success).toHaveBeenCalled())
    expect(screen.getByRole('region', { name: 'Create Jira issue' })).toBeInTheDocument()
  })
})

describe("/failures' own actions open under the page header (W27 PAN-04)", () => {
  it('Classify, Correct and Hints render right after [data-page-header], one at a time', async () => {
    metrics.categories = [{ category: 'UNKNOWN', count: 4 }]
    renderFailures('?tab=categories')
    const header = await waitFor(() => document.querySelector('[data-page-header]') as HTMLElement)
    const verdict = screen.getByRole('region', { name: 'Failure verdict' })
    const placedUnderHeader = (section: HTMLElement) => {
      expect(header.compareDocumentPosition(section) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
      expect(section.compareDocumentPosition(verdict) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
      expect(section.closest('[data-primary]')).toBeNull()
    }

    fireEvent.click(await screen.findByRole('button', { name: /Classify them so owners can be auto-routed/ }))
    const classify = screen.getByRole('region', { name: 'Classify uncategorised failures' })
    placedUnderHeader(classify)

    fireEvent.click(screen.getByRole('button', { name: /Correct the classification/ }))
    expect(screen.queryByRole('region', { name: 'Classify uncategorised failures' })).toBeNull()
    placedUnderHeader(screen.getByRole('region', { name: 'Correct classification' }))

    fireEvent.click(screen.getByTestId('open-classification-hints'))
    expect(screen.queryByRole('region', { name: 'Correct classification' })).toBeNull()
    placedUnderHeader(screen.getByRole('region', { name: 'Classification hints' }))
    expectNoModal()
  })

  it('opening Classify focuses its X, never a category button (one Enter there would tag every failure); Escape closes it', async () => {
    metrics.categories = [{ category: 'UNKNOWN', count: 4 }]
    renderFailures('?tab=categories')
    fireEvent.click(await screen.findByRole('button', { name: /Classify them so owners can be auto-routed/ }))
    const classify = screen.getByRole('region', { name: 'Classify uncategorised failures' })
    expect(document.activeElement).toBe(within(classify).getByRole('button', { name: 'Close' }))
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: 'Escape' })
    expect(screen.queryByRole('region', { name: 'Classify uncategorised failures' })).toBeNull()
  })
})
