import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { usePermissions } from '@/hooks/usePermissions'
import { refreshSuites } from '@/hooks/useSuites'
import { suitesService } from '@/services/suitesService'
import CanonicalDetailPage from './CanonicalDetailPage'

// We mock the hooks rather than the SWR layer so tests don't need to
// stand up an SWR cache or a fake fetcher. Same pattern as
// ``TestCasePage.test.tsx`` in this directory.
vi.mock('@/hooks/useSuites', () => ({
  refreshSuites: vi.fn().mockResolvedValue(undefined),
  useCanonicalCase: vi.fn(),
  useCanonicalRuns: vi.fn(),
  useSuite: vi.fn(),
}))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: vi.fn() }))
vi.mock('@/services/suitesService', () => ({
  suitesService: {
    unlinkManagedCase: vi.fn(),
    promoteCanonical: vi.fn(),
  },
}))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

const PATH = '/canonical-test-cases/:canonicalId'

function renderAt(canonicalId: string) {
  return render(
    <MemoryRouter initialEntries={[`/canonical-test-cases/${canonicalId}`]}>
      <Routes>
        <Route path="/suites" element={<div>Suites List</div>} />
        <Route path="/suites/:suiteId" element={<div>Suite Detail</div>} />
        <Route path={PATH} element={<CanonicalDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('CanonicalDetailPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    ;(usePermissions as ReturnType<typeof vi.fn>).mockReturnValue({ isQaEngineer: true })
    ;(refreshSuites as ReturnType<typeof vi.fn>).mockResolvedValue(undefined)
  })

  it('renders the identity header + run-history rows', async () => {
    const { useCanonicalCase, useCanonicalRuns, useSuite } = await import('@/hooks/useSuites')

    ;(useCanonicalCase as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        id: 'canon-1',
        project_id: 'proj-1',
        test_suite_id: 'suite-1',
        test_suite_name: 'Regression',
        test_fingerprint: 'abcdef0123456789',
        test_name: 'should sign in',
        class_name: 'auth.LoginTest',
        status: 'active',
        source: 'execution',
        first_seen_run_id: null,
        last_seen_run_id: 'run-aaaa1111',
        last_seen_test_case_id: 'tc-9',
        deleted_at_run_id: null,
        managed_test_case_id: null,
        review_tag: null,
        tags: null,
        run_count: 2,
        created_at: '2026-05-15T10:00:00Z',
        updated_at: null,
      },
      isLoading: false,
      mutate: vi.fn(),
    })
    ;(useSuite as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { id: 'suite-1', project_id: 'proj-1', name: 'Regression' },
    })
    ;(useCanonicalRuns as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        items: [
          {
            test_case_id: 'tc-9',
            test_run_id: 'run-aaaa1111',
            status: 'PASSED',
            duration_ms: 1500,
            suite_name: 'Regression',
            created_at: '2026-05-16T10:00:00Z',
          },
          {
            test_case_id: 'tc-8',
            test_run_id: 'run-bbbb2222',
            status: 'FAILED',
            duration_ms: 250,
            suite_name: 'Regression',
            created_at: '2026-05-15T10:00:00Z',
          },
        ],
        total: 2,
      },
      isLoading: false,
      mutate: vi.fn(),
    })

    renderAt('canon-1')

    // Header carries the test name + class.
    expect(
      await screen.findByRole('heading', { name: /should sign in/i }),
    ).toBeInTheDocument()
    expect(screen.getByText(/auth\.LoginTest/)).toBeInTheDocument()

    // Status pill rendered from the canonical's lifecycle state.
    expect(screen.getByText('active')).toBeInTheDocument()

    // Both run-history rows are visible with their run-id prefixes.
    // The first 8 chars of each run id, mirroring the table contract.
    expect(screen.getByText('run-aaaa')).toBeInTheDocument()
    expect(screen.getByText('run-bbbb')).toBeInTheDocument()
    // Status pill per row uses upper-case status text.
    expect(screen.getByText('PASSED')).toBeInTheDocument()
    expect(screen.getByText('FAILED')).toBeInTheDocument()

    // Duration formatter: 1500ms → "1.5s", 250ms → "250ms" (per
    // ``utils/formatters.ts:formatDuration``).
    expect(screen.getByText('1.5s')).toBeInTheDocument()
    expect(screen.getByText('250ms')).toBeInTheDocument()

    // "Open latest run" CTA points at the per-run test page when we
    // have both ids.
    const cta = screen.getByRole('link', { name: /Open latest run/i })
    expect(cta).toHaveAttribute('href', '/runs/run-aaaa1111/tests/tc-9')
  })

  it('shows an empty state when the canonical has no run history yet', async () => {
    const { useCanonicalCase, useCanonicalRuns, useSuite } = await import('@/hooks/useSuites')

    ;(useCanonicalCase as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        id: 'canon-2',
        project_id: 'proj-1',
        test_suite_id: 'suite-1',
        test_suite_name: 'Regression',
        test_fingerprint: 'fp-no-runs-yet',
        test_name: 'recently authored, never run',
        class_name: null,
        status: 'active',
        source: 'managed',
        first_seen_run_id: null,
        last_seen_run_id: null,
        last_seen_test_case_id: null,
        deleted_at_run_id: null,
        managed_test_case_id: 'mtc-1',
        review_tag: null,
        tags: null,
        run_count: null,
        created_at: '2026-05-17T10:00:00Z',
        updated_at: null,
      },
      isLoading: false,
      mutate: vi.fn(),
    })
    ;(useSuite as ReturnType<typeof vi.fn>).mockReturnValue({ data: undefined })
    ;(useCanonicalRuns as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [], total: 0 },
      isLoading: false,
    })

    renderAt('canon-2')

    expect(
      await screen.findByRole('heading', { name: /recently authored, never run/i }),
    ).toBeInTheDocument()
    // Empty-state copy from EmptyState component.
    expect(screen.getByText(/No runs yet/i)).toBeInTheDocument()
    // No "Open latest run" CTA when last_seen_run_id is null — saves an
    // ambiguous nav target.
    expect(screen.queryByRole('link', { name: /Open latest run/i })).toBeNull()
  })

  it('renders not-found state when the canonical lookup fails', async () => {
    const { useCanonicalCase, useCanonicalRuns, useSuite } = await import('@/hooks/useSuites')

    ;(useCanonicalCase as ReturnType<typeof vi.fn>).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error('not found'),
      mutate: vi.fn(),
    })
    ;(useSuite as ReturnType<typeof vi.fn>).mockReturnValue({ data: undefined })
    ;(useCanonicalRuns as ReturnType<typeof vi.fn>).mockReturnValue({
      data: undefined,
      isLoading: false,
    })

    renderAt('canon-missing')

    expect(
      await screen.findByText(/Test case not found/i),
    ).toBeInTheDocument()
  })

  it('requires a reason before unlinking the managed case', async () => {
    const { useCanonicalCase, useCanonicalRuns, useSuite } = await import('@/hooks/useSuites')
    const mutateCanonical = vi.fn().mockResolvedValue(undefined)
    const canonical = {
      id: 'canon-linked', project_id: 'proj-1', test_suite_id: 'suite-1',
      test_suite_name: 'Regression', test_fingerprint: 'fingerprint-linked',
      test_name: 'linked test', class_name: null, status: 'active', source: 'linked',
      first_seen_run_id: null, last_seen_run_id: null, last_seen_test_case_id: null,
      deleted_at_run_id: null, managed_test_case_id: 'managed-1', review_tag: null,
      tags: null, run_count: 0, created_at: '2026-05-17T10:00:00Z', updated_at: null,
    }
    ;(useCanonicalCase as ReturnType<typeof vi.fn>).mockReturnValue({
      data: canonical,
      isLoading: false,
      mutate: mutateCanonical,
    })
    ;(useSuite as ReturnType<typeof vi.fn>).mockReturnValue({ data: undefined })
    ;(useCanonicalRuns as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [], total: 0 },
      isLoading: false,
    })
    ;(suitesService.unlinkManagedCase as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...canonical,
      managed_test_case_id: null,
    })

    renderAt('canon-linked')
    fireEvent.click(screen.getByRole('button', { name: 'Unlink managed case' }))
    const dialog = screen.getByRole('dialog', { name: 'Unlink managed case' })
    const confirm = within(dialog).getByRole('button', { name: 'Unlink' })
    expect(confirm).toBeDisabled()
    fireEvent.change(within(dialog).getByRole('textbox', { name: /Reason/ }), {
      target: { value: '  Incorrect automation mapping  ' },
    })
    fireEvent.click(confirm)

    await waitFor(() => {
      expect(suitesService.unlinkManagedCase).toHaveBeenCalledWith(
        'canon-linked',
        'Incorrect automation mapping',
      )
    })
  })

  it('does not expose promote or unlink controls below QA engineer', async () => {
    const { useCanonicalCase, useCanonicalRuns, useSuite } = await import('@/hooks/useSuites')
    ;(usePermissions as ReturnType<typeof vi.fn>).mockReturnValue({ isQaEngineer: false })
    ;(useCanonicalCase as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        id: 'canon-viewer', project_id: 'proj-1', test_suite_id: 'suite-1',
        test_suite_name: 'Regression', test_fingerprint: 'fingerprint-viewer',
        test_name: 'viewer test', class_name: null, status: 'active', source: 'linked',
        first_seen_run_id: null, last_seen_run_id: null, last_seen_test_case_id: null,
        deleted_at_run_id: null, managed_test_case_id: 'managed-1', review_tag: null,
        tags: null, run_count: 0, created_at: '2026-05-17T10:00:00Z', updated_at: null,
      },
      isLoading: false,
      mutate: vi.fn(),
    })
    ;(useSuite as ReturnType<typeof vi.fn>).mockReturnValue({ data: undefined })
    ;(useCanonicalRuns as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [], total: 0 },
      isLoading: false,
    })

    renderAt('canon-viewer')

    expect(screen.queryByRole('button', { name: 'Unlink managed case' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Promote to managed case' })).not.toBeInTheDocument()
  })

  it('keeps unlink disabled locally when cache refresh fails after success', async () => {
    const { useCanonicalCase, useCanonicalRuns, useSuite } = await import('@/hooks/useSuites')
    const canonical = {
      id: 'canon-refresh', project_id: 'proj-1', test_suite_id: 'suite-1',
      test_suite_name: 'Regression', test_fingerprint: 'fingerprint-refresh',
      test_name: 'refresh test', class_name: null, status: 'active', source: 'linked',
      first_seen_run_id: null, last_seen_run_id: null, last_seen_test_case_id: null,
      deleted_at_run_id: null, managed_test_case_id: 'managed-1', review_tag: null,
      tags: null, run_count: 0, created_at: '2026-05-17T10:00:00Z', updated_at: null,
    }
    const mutateCanonical = vi.fn().mockRejectedValue(new Error('cache unavailable'))
    ;(useCanonicalCase as ReturnType<typeof vi.fn>).mockReturnValue({
      data: canonical,
      isLoading: false,
      mutate: mutateCanonical,
    })
    ;(useSuite as ReturnType<typeof vi.fn>).mockReturnValue({ data: undefined })
    ;(useCanonicalRuns as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [], total: 0 },
      isLoading: false,
    })
    ;(suitesService.unlinkManagedCase as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...canonical,
      managed_test_case_id: null,
    })

    renderAt('canon-refresh')
    fireEvent.click(screen.getByRole('button', { name: 'Unlink managed case' }))
    fireEvent.change(screen.getByRole('textbox', { name: /Reason/ }), {
      target: { value: 'Incorrect mapping' },
    })
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Unlink' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.queryByRole('button', { name: 'Unlink managed case' })).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('managed link was removed')
    expect(suitesService.unlinkManagedCase).toHaveBeenCalledTimes(1)
  })
})

// W03 AIH-05: the run-history table's "AI category" column (contract §7.3, §9.8).
describe('CanonicalDetailPage — AI category per run', () => {
  const CANONICAL = {
    id: 'canon-ai', project_id: 'proj-1', test_suite_id: 'suite-1',
    test_suite_name: 'Regression', test_fingerprint: 'fingerprint-ai',
    test_name: 'ai test', class_name: null, status: 'active', source: 'execution',
    first_seen_run_id: null, last_seen_run_id: null, last_seen_test_case_id: null,
    deleted_at_run_id: null, managed_test_case_id: null, review_tag: null,
    tags: null, run_count: 3, created_at: '2026-05-17T10:00:00Z', updated_at: null,
  }
  const row = (n: number, extra: Record<string, unknown>) => ({
    test_case_id: `tc-${n}`,
    test_run_id: `run-${n}000000`,
    status: 'FAILED',
    duration_ms: 1000,
    suite_name: 'Regression',
    created_at: `2026-05-1${n}T10:00:00Z`,
    ...extra,
  })
  const AI = {
    category: 'PRODUCT_BUG', confidence: 85, analysis_mode: 'llm', model: 'llama3.1',
    requires_human_review: true, root_cause_excerpt: 'tax skipped', version_count: 3,
  }

  async function renderWith(data: unknown) {
    const { useCanonicalCase, useCanonicalRuns, useSuite } = await import('@/hooks/useSuites')
    ;(usePermissions as ReturnType<typeof vi.fn>).mockReturnValue({ isQaEngineer: false })
    ;(useCanonicalCase as ReturnType<typeof vi.fn>).mockReturnValue({ data: CANONICAL, isLoading: false, mutate: vi.fn() })
    ;(useSuite as ReturnType<typeof vi.fn>).mockReturnValue({ data: undefined })
    ;(useCanonicalRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data, isLoading: false })
    const utils = renderAt('canon-ai')
    await screen.findByRole('heading', { name: /ai test/i })
    return utils
  }

  it('adds the column after Duration: chip + confidence, the engine in the title, "—" without an analysis', async () => {
    const { container } = await renderWith({
      items: [
        row(3, { ai: AI }),
        row(2, { ai: { ...AI, category: null, confidence: null, analysis_mode: null, model: null, requires_human_review: false } }),
        row(1, { status: 'PASSED', ai: null }),
      ],
      total: 3,
      ai_truncated: false,
    })
    const headers = Array.from(container.querySelectorAll('thead th')).map((th) => th.textContent)
    expect(headers.slice(0, 4)).toEqual(['Status', 'Run', 'Duration', 'AI category'])

    const cells = Array.from(container.querySelectorAll<HTMLElement>('td[data-canonical-ai]'))
    expect(cells.map((td) => td.dataset.canonicalAi)).toEqual(['PRODUCT_BUG', 'uncategorized', ''])
    expect(cells[0]).toHaveTextContent('PRODUCT_BUG85%')
    expect(cells[0]).toHaveAttribute('title', 'LLM llama3.1 - needs human review')
    // A missing confidence / engine reads "not reported", never 0.
    expect(cells[1]).toHaveTextContent('uncategorizedconfidence not reported')
    expect(cells[1]).toHaveAttribute('title', 'engine not reported')
    expect(cells[2]).toHaveTextContent('—')
    expect(container.querySelector('[data-canonical-ai-truncated]')).toBeNull()
  })

  it('an older server (no ai, no ai_truncated) still renders, every AI cell "—"', async () => {
    const { container } = await renderWith({ items: [row(1, {}), row(2, {})], total: 2 })
    const cells = Array.from(container.querySelectorAll('td[data-canonical-ai]'))
    expect(cells).toHaveLength(2)
    cells.forEach((td) => expect(td).toHaveTextContent('—'))
    expect(container.querySelector('[data-canonical-ai-truncated]')).toBeNull()
  })

  it('says when only the newest rows carry the AI category', async () => {
    const { container } = await renderWith({ items: [row(1, { ai: AI }), row(2, { ai: null })], total: 2500, ai_truncated: true })
    expect(container.querySelector('[data-canonical-ai-truncated]')).toHaveTextContent(
      'AI category is shown for the newest 2,000 runs only',
    )
  })

  it('renders a hostile category and model as text', async () => {
    const hostile = '<img src=x onerror=alert(1)>'
    const { container } = await renderWith({
      items: [row(1, { ai: { ...AI, category: hostile, model: hostile } })],
      total: 1,
    })
    expect(container.querySelector('img')).toBeNull()
    const cell = container.querySelector('td[data-canonical-ai]') as HTMLElement
    expect(cell).toHaveTextContent(hostile)
    expect(cell.title).toContain(hostile)
  })
})
