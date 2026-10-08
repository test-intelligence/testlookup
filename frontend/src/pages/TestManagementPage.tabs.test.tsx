/**
 * UX redesign P4 item 7: Test Cases, 8 → 5 tabs.
 *
 *   Cases · Suites · Plans · Approvals (was Reviews) · More ▾ (Strategy,
 *   Duplicates, Audit log, Knowledge)
 *
 * The cases table is the page's primary content (`[data-primary]`), under a
 * one-line health banner; the former right rail is the "Insights" drawer;
 * "Generate test cases" is the AI Generate modal's "From documents" source.
 * Every `?tab=` value selects its tab, and the values links still carry (the
 * old labels, `reviews`) read as the tab that has the content now.
 *
 * The long-tail tab bodies are stubbed (each has its own tests next to it);
 * Cases, Suites and Plans render for real over mocked hooks.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useEffect } from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { testManagementService } from '@/services/testManagementService'
import type { ManagedTestCase } from '@/types/test-management'
import TestManagementPage from './TestManagementPage'

vi.mock('@/store/projectStore', () => {
  const state = { activeProjectId: 'project-1', activeProject: { id: 'project-1', name: 'Project One' } }
  return {
    ALL_PROJECTS_ID: '__ALL__',
    useProjectStore: (selector: (s: typeof state) => unknown) => selector(state),
  }
})

vi.mock('@/hooks/useFeatureFlags', () => ({ useFeatureEnabled: () => false }))

const CASES: ManagedTestCase[] = [
  ['case-1', 'Checkout preserves cart', 'active', true],
  ['case-2', 'Sign in with SSO', 'review_requested', false],
  ['case-3', 'Refund a partial order', 'draft', false],
].map(([id, title, status, auto]) => ({
  id: id as string,
  project_id: 'project-1',
  title: title as string,
  test_type: 'functional',
  priority: 'high',
  severity: 'major',
  test_suite_id: null,
  status: status as ManagedTestCase['status'],
  version: 1,
  is_automated: auto as boolean,
  automation_status: auto ? 'automated' : 'manual',
  ai_generated: false,
  source: 'managed',
  allowed_actions: ['deprecate'],
  created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-15T00:00:00Z',
}))

vi.mock('@/hooks/useTestManagement', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useTestManagement')>(),
  useTestCases: () => ({
    data: { items: CASES, total: CASES.length, page: 1, size: 25, pages: 1 },
    error: undefined,
    isLoading: false,
    mutate: vi.fn(),
  }),
  useAuditLog: () => ({ data: { items: [], total: 0, page: 1, size: 5, pages: 0 } }),
  useTestPlans: () => ({ data: { items: [], total: 0, page: 1, size: 10, pages: 0 }, isLoading: false, mutate: vi.fn() }),
  useUsers: () => ({ data: [] }),
}))

vi.mock('@/hooks/useSuites', () => ({
  useSuites: () => ({
    data: {
      items: [{ id: 'suite-1', project_id: 'project-1', name: 'Checkout', description: null, is_default: false, tags: null, test_case_count: 4, created_at: '2026-08-01T00:00:00Z', updated_at: null }],
      total: 1,
    },
  }),
}))

const projectMembers = vi.hoisted(() => ({ data: [] as unknown[] }))
vi.mock('@/hooks/useUserManagement', () => ({ useProjectMembers: () => ({ data: projectMembers.data }) }))
vi.mock('@/components/testManagement/EvidenceGapLists', () => ({ default: () => null }))

// The long-tail bodies: each is tested beside its own file.
vi.mock('@/pages/test-management/StrategyTab', () => ({ default: () => <p>strategy body</p> }))
vi.mock('@/pages/test-management/DuplicatesTab', () => ({ default: () => <p>duplicates body</p> }))
vi.mock('@/pages/test-management/AuditTab', () => ({ default: () => <p>audit body</p> }))
vi.mock('@/pages/test-management/ApprovalsTab', () => ({ default: () => <p>approvals body</p> }))
vi.mock('@/pages/test-management/KnowledgeGenerationTab', () => ({ default: () => <p>knowledge generation body</p> }))

vi.mock('@/services/testManagementService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/testManagementService')>()
  return {
    ...actual,
    testManagementService: {
      ...actual.testManagementService,
      listSuites: vi.fn(),
      listReviewsForRun: vi.fn(),
      aiGenerateAsync: vi.fn(),
      createCase: vi.fn(async () => ({})),
    },
  }
})

vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}))

const SUITES = [
  { suite_name: 'Checkout', test_count: 4, passed_count: 3, failed_count: 1, last_run_at: null, last_run_id: null, pass_rate: 75 },
  { suite_name: 'Runs only', test_count: 2, passed_count: 2, failed_count: 0, last_run_at: null, last_run_id: null, pass_rate: 100 },
]

function Where({ onChange }: { onChange: (where: string) => void }) {
  const location = useLocation()
  useEffect(() => onChange(location.pathname + location.search), [location, onChange])
  return null
}

function renderAt(url: string) {
  const where = { current: '' }
  const track = (next: string) => { where.current = next }
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter initialEntries={[url]}>
        <Where onChange={track} />
        <Routes>
          <Route path="/test-management" element={<TestManagementPage />} />
        </Routes>
      </MemoryRouter>
    </SWRConfig>,
  )
  return where
}

const tabBar = () => screen.getByRole('tablist', { name: 'Test case sections' })
const follows = (a: Node, b: Node) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)

beforeEach(() => {
  vi.mocked(testManagementService.listSuites).mockResolvedValue(SUITES as never)
  vi.mocked(testManagementService.listReviewsForRun).mockResolvedValue([])
  vi.mocked(testManagementService.aiGenerateAsync).mockReset()
  localStorage.clear()
})

describe('Test Cases — five tabs (P4 item 7)', () => {
  it('has Cases · Suites · Plans · Approvals · More, Cases selected by default', () => {
    renderAt('/test-management')
    expect(within(tabBar()).getAllByRole('tab').map((t) => t.textContent)).toEqual(['Cases', 'Suites', 'Plans', 'Approvals', 'More'])
    expect(within(tabBar()).getByRole('tab', { name: 'Cases' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByRole('tab', { name: 'Reviews' })).toBeNull()
  })

  it('Cases: the table is the one primary element, after the tab bar and the health banner, before anything else', () => {
    renderAt('/test-management')
    const primaries = document.querySelectorAll('[data-primary]')
    expect(primaries).toHaveLength(1)
    const primary = primaries[0] as HTMLElement
    expect(within(primary).getByRole('table')).toBeInTheDocument()
    expect(within(primary).getByText('Checkout preserves cart')).toBeInTheDocument()
    expect(follows(tabBar(), primary)).toBe(true)
    const banner = document.querySelector('[data-status-banner]') as HTMLElement
    expect(follows(banner, primary)).toBe(true)
    // No other tab bar and no disclosure above the table.
    for (const el of document.querySelectorAll('[role="tablist"], [data-disclosure]')) {
      if (el === tabBar()) continue
      expect(follows(primary, el), 'a tab bar or disclosure above the primary content').toBe(true)
    }
  })

  it('the health banner carries the library verdict and four facts, singular for one case', () => {
    renderAt('/test-management')
    const banner = document.querySelector('[data-status-banner]') as HTMLElement
    expect(banner).toHaveTextContent(/Library health \d+\/100/)
    // The pill names the library verdict in its own words: "AT RISK" was shown
    // as the state's "FAILING", a word for builds, not for a test library.
    const pill = banner.querySelector('[data-banner-pill]') as HTMLElement
    expect(pill.textContent).toMatch(/^(HEALTHY|NEEDS ATTENTION|AT RISK)$/)
    const facts = Array.from(banner.querySelectorAll('[data-banner-fact]')).map((f) => f.textContent)
    expect(facts).toEqual(['Cases 3', 'Awaiting review 1', 'Automated 33%', 'Stale drafts 0'])
  })

  it.each([
    ['cases', 'Cases', () => screen.getByRole('table')],
    ['suites', 'Suites', () => screen.findByText('2 suites')],
    ['plans', 'Plans', () => screen.getByRole('button', { name: /AI Create Plan/ })],
    ['approvals', 'Approvals', () => screen.getByText('approvals body')],
    ['strategy', 'More: Strategy', () => screen.getByText('strategy body')],
    ['duplicates', 'More: Duplicates', () => screen.getByText('duplicates body')],
    ['audit', 'More: Audit log', () => screen.getByText('audit body')],
    ['knowledge', 'More: Knowledge', () => screen.getByText('knowledge generation body')],
  ] as const)('?tab=%s selects "%s" and renders its section', async (id, label, section) => {
    renderAt(`/test-management?tab=${id}`)
    const selected = within(tabBar()).getAllByRole('tab').filter((t) => t.getAttribute('aria-selected') === 'true')
    expect(selected.map((t) => t.textContent)).toEqual([label])
    expect(await section()).toBeTruthy()
    expect(document.querySelector(`[data-tab-panel="${id}"]`)).not.toBeNull()
  })

  it.each([
    ['reviews', 'Approvals', 'approvals body'],
    ['Reviews', 'Approvals', 'approvals body'],
    ['Test+Cases', 'Cases', 'Checkout preserves cart'],
    ['Test+Plans', 'Plans', 'AI Create Plan'],
    ['Strategy', 'More: Strategy', 'strategy body'],
    ['Knowledge+Generation', 'More: Knowledge', 'knowledge generation body'],
    ['Duplicates', 'More: Duplicates', 'duplicates body'],
    ['Audit+Log', 'More: Audit log', 'audit body'],
  ])('the old link ?tab=%s still opens %s', async (raw, label, text) => {
    renderAt(`/test-management?tab=${raw}`)
    expect(within(tabBar()).getByRole('tab', { name: label })).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByText(text)).toBeInTheDocument()
  })

  it('the old ?tab=Test+Suites&suite=<name> deep link opens Suites with that suite expanded and marked', async () => {
    vi.mocked(testManagementService.listSuites).mockResolvedValue(SUITES as never)
    const getSuiteCases = vi.spyOn(testManagementService, 'getSuiteCases').mockResolvedValue({ items: [], total: 0, page: 1, size: 25, pages: 1 } as never)
    vi.spyOn(testManagementService, 'getSuiteDeleted').mockResolvedValue([])
    vi.spyOn(testManagementService, 'getSuiteChanges').mockResolvedValue([])
    // jsdom lays nothing out: the deep link's one-time scroll is a no-op here.
    const scrollIntoView = vi.fn()
    Element.prototype.scrollIntoView = scrollIntoView
    renderAt('/test-management?tab=Test+Suites&suite=Checkout')
    expect(within(tabBar()).getByRole('tab', { name: 'Suites' })).toHaveAttribute('aria-selected', 'true')
    await waitFor(() => expect(getSuiteCases).toHaveBeenCalledWith('Checkout', 'project-1', { page: 1, size: 25 }))
    expect(document.getElementById('suite-card-Checkout')?.className).toContain('ring-2')
    expect(scrollIntoView).toHaveBeenCalled()
  })

  it('choosing a tab writes its new id to ?tab= (Cases, the default, clears it)', () => {
    const where = renderAt('/test-management?tab=reviews')
    fireEvent.click(within(tabBar()).getByRole('tab', { name: 'Plans' }))
    expect(where.current).toBe('/test-management?tab=plans')
    fireEvent.click(within(tabBar()).getByRole('tab', { name: 'Cases' }))
    expect(where.current).toBe('/test-management')
  })
})

describe('Test Cases — the More ▾ menu', () => {
  it('opens a menu of Strategy, Duplicates, Audit log and Knowledge; picking one selects it', () => {
    const where = renderAt('/test-management')
    fireEvent.click(within(tabBar()).getByRole('tab', { name: 'More' }))
    const menu = screen.getByRole('menu', { name: 'More sections' })
    expect(within(menu).getAllByRole('menuitem').map((m) => m.textContent)).toEqual(['Strategy', 'Duplicates', 'Audit log', 'Knowledge'])
    // The selection does not move until something is picked.
    expect(within(tabBar()).getByRole('tab', { name: 'Cases' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Duplicates' }))
    expect(screen.queryByRole('menu', { name: 'More sections' })).toBeNull()
    expect(where.current).toBe('/test-management?tab=duplicates')
    expect(within(tabBar()).getByRole('tab', { name: 'More: Duplicates' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('duplicates body')).toBeInTheDocument()
  })

  it('a second click on More closes the menu', () => {
    renderAt('/test-management')
    const more = within(tabBar()).getByRole('tab', { name: 'More' })
    fireEvent.click(more)
    expect(screen.getByRole('menu', { name: 'More sections' })).toBeInTheDocument()
    fireEvent.click(more)
    expect(screen.queryByRole('menu', { name: 'More sections' })).toBeNull()
  })
})

describe('Test Cases — the Insights drawer (the former right rail)', () => {
  it('opens from the banner with the library health and the rail cards, and closes', () => {
    renderAt('/test-management')
    expect(screen.queryByRole('complementary', { name: 'Insights' })).toBeNull()
    // Not beside the table any more.
    expect(screen.queryByText(/Review queue/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /^Insights/ }))
    const drawer = screen.getByRole('complementary', { name: 'Insights' })
    for (const heading of ['Library health', 'Review queue · 1', 'Strategy gaps', 'Recent activity', 'Automation coverage']) {
      expect(within(drawer).getByRole('heading', { name: heading })).toBeInTheDocument()
    }
    expect(within(drawer).getByText('Sign in with SSO')).toBeInTheDocument()
    expect(within(drawer).getByText(/Library refreshed/)).toBeInTheDocument()
    fireEvent.click(within(drawer).getByRole('button', { name: 'Close panel' }))
    expect(screen.queryByRole('complementary', { name: 'Insights' })).toBeNull()
  })
})

describe('Test Cases — AI Generate, from a description or from documents', () => {
  it('opens on a description and generates through the service', async () => {
    vi.mocked(testManagementService.aiGenerateAsync).mockResolvedValue({} as never)
    renderAt('/test-management')
    fireEvent.click(screen.getByRole('button', { name: /AI Generate/ }))
    const dialog = screen.getByRole('dialog', { name: 'AI Generate Test Cases' })
    const sources = within(dialog).getByRole('tablist', { name: 'Generate from' })
    expect(within(sources).getByRole('tab', { name: 'From a description' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'What to generate test cases for' }), {
      target: { value: 'Regression cases for the refund rounding defect' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: /Generate & Save/ }))
    await waitFor(() => expect(testManagementService.aiGenerateAsync).toHaveBeenCalledWith({
      project_id: 'project-1',
      requirements: 'Regression cases for the refund rounding defect',
      persist: true,
    }))
  })

  it('"From documents" is the knowledge-generation flow', () => {
    renderAt('/test-management')
    fireEvent.click(screen.getByRole('button', { name: /AI Generate/ }))
    const dialog = screen.getByRole('dialog', { name: 'AI Generate Test Cases' })
    expect(within(dialog).queryByText('knowledge generation body')).toBeNull()
    fireEvent.click(within(dialog).getByRole('tab', { name: 'From documents' }))
    expect(within(dialog).getByText('knowledge generation body')).toBeInTheDocument()
    expect(within(dialog).queryByRole('textbox', { name: 'What to generate test cases for' })).toBeNull()
  })
})

describe('Test Cases — Suites link to the suite page', () => {
  it('a suite with a record links its name to /suites/:id and its trend to the Charts tab', async () => {
    renderAt('/test-management?tab=suites')
    const link = await screen.findByRole('link', { name: 'Checkout' })
    expect(link).toHaveAttribute('href', '/suites/suite-1')
    expect(screen.getByRole('link', { name: 'Trend →' })).toHaveAttribute('href', '/suites/suite-1?tab=charts')
    // A name aggregated from runs only has no suite page: text, no trend link.
    expect(screen.queryByRole('link', { name: 'Runs only' })).toBeNull()
    expect(screen.getAllByRole('link', { name: 'Trend →' })).toHaveLength(1)
    const hrefs = Array.from(document.querySelectorAll('a[href]')).map((a) => a.getAttribute('href') ?? '')
    expect(hrefs.filter((h) => h.startsWith('/coverage/suite'))).toEqual([])
  })
})

// Browser E2E pass (2026-10-08): the New Test Case form listed every user on
// the instance as an assignee, and the backend dropped the pick (its create
// schema had no assignee_id). It now lists the project's members, and sends
// the one chosen.
describe('Test Cases — New Test Case assignee', () => {
  afterEach(() => { projectMembers.data = [] })

  it('offers the project members, sorted by name, and sends the chosen one', async () => {
    projectMembers.data = [
      { id: 'm2', user_id: 'u-zoe', project_id: 'project-1', role: 'QA_ENGINEER', created_at: '', email: 'z@x.test', username: 'zoe', full_name: 'Zoe Park' },
      { id: 'm1', user_id: 'u-ann', project_id: 'project-1', role: 'QA_LEAD', created_at: '', email: 'a@x.test', username: 'ann', full_name: null },
    ]
    renderAt('/test-management')
    fireEvent.click(screen.getByRole('button', { name: 'New test case from catalog toolbar' }))
    const picker = screen.getByLabelText('Assignee (optional)')
    expect(within(picker).getAllByRole('option').map((o) => o.textContent)).toEqual(['Unassigned', 'ann', 'Zoe Park'])

    fireEvent.change(screen.getByLabelText('Title *'), { target: { value: 'Refund is idempotent' } })
    fireEvent.change(picker, { target: { value: 'u-zoe' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create Test Case' }))
    await waitFor(() => expect(testManagementService.createCase).toHaveBeenCalled())
    expect(vi.mocked(testManagementService.createCase).mock.calls[0][0]).toMatchObject({
      title: 'Refund is idempotent',
      assignee_id: 'u-zoe',
    })
  })
})
