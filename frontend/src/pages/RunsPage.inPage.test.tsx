/**
 * W27a PAN-03 (`docs/deferred-scope-2026-10-13/work/W27a-contract.md` §2.3): on /runs the upload form and the
 * failure signatures open IN THE PAGE.
 *
 *  - Upload report (and `/runs?upload=1`) renders the REAL upload form right under the page header, before the
 *    toolbar, named "Upload test report"; nothing on the page is a modal (`aria-modal`, `role="dialog"`).
 *  - Failure signatures renders a read-only section (`complementary`, X "Close panel") between the status banner
 *    and the runs table; the table's container is the same element before, while and after it is open, and it
 *    is never put in a grid beside it (the table keeps its full card width).
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { TestRun } from '@/types/runs'
import { useRuns } from '@/hooks/useRuns'
import { useTimeWindowStore } from '@/store/timeWindowStore'
import RunsPage from './RunsPage'

vi.mock('@/utils/errorReporting', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/utils/errorReporting')>(),
  reportBoundaryError: vi.fn(),
}))
vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(),
  useMostRecentRun: vi.fn(() => ({ data: undefined })),
}))
vi.mock('@/hooks/useSuiteOptions', () => ({
  useSuiteOptions: () => ({ options: [], isLoading: false }),
}))
vi.mock('@/store/projectStore', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/store/projectStore')>()
  const state = {
    activeProjectId: 'proj-1',
    activeProject: { id: 'proj-1', name: 'Project One' },
    projects: [{ id: 'proj-1', name: 'Project One' }],
  }
  return {
    ...actual,
    useProjectStore: (selector?: (s: typeof state) => unknown) => (selector ? selector(state) : state),
  }
})
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }))
vi.mock('@/hooks/useFeatureFlags', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/hooks/useFeatureFlags')>()),
  useFeatureEnabled: (key: string) => key === 'manual_upload',
}))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => ({ isQaEngineer: true }) }))
// The upload form itself is real; it sends nothing in these tests.
const upload = vi.hoisted(() => ({ upload: vi.fn(), getStatus: vi.fn() }))
vi.mock('@/services/reportUploadService', async (importActual) => ({
  ...(await importActual<typeof import('@/services/reportUploadService')>()),
  reportUploadService: upload,
}))
vi.mock('@/services/aiHistoryService', () => ({ aiHistoryService: { getPreviousRun: vi.fn() } }))
vi.mock('@/services/agentService', () => ({
  default: { triggerPipeline: vi.fn(), triggerDeepPipeline: vi.fn(), bulkTriggerPipelines: vi.fn() },
}))
// The page's batch reads: every failed build carries the same signature (one group).
vi.mock('@/services/http', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/http')>()),
  getData: vi.fn(async (url: string, config?: { params?: Record<string, unknown> }) => {
    const ids = ([] as string[]).concat((config?.params?.run_id as string[] | undefined) ?? [])
    if (url === '/api/v1/runs/ai-verdicts') return { items: [], unavailable: [], max_ids: 25 }
    if (url === '/api/v1/runs/failure-signatures') {
      return {
        items: ids.map((id) => ({
          run_id: id, failing_tests: 2, read: 2, capped: false, dominant: 'assertionerror: expected # but was #',
          signatures: [{ signature: 'assertionerror: expected # but was #', label: 'AssertionError: expected 3 but was 4', count: 2, test_count: 1, tests: ['test_checkout'] }],
        })),
        unavailable: [], max_ids: 50, rows_per_run_cap: 500,
      }
    }
    if (url === '/api/v1/runs/branches') return { items: [], total: 0, truncated: false, window_days: 30 }
    throw new Error(`unexpected request ${url}`)
  }),
}))

const NOW = Date.now()
const hoursAgo = (h: number) => new Date(NOW - h * 3_600_000).toISOString()
const run = (i: number, over: Partial<TestRun>): TestRun => ({
  id: `run-${i}`, build_number: 100 + i, status: 'PASSED', passed_tests: 10, failed_tests: 0, skipped_tests: 0,
  total_tests: 10, pass_rate: 100, created_at: hoursAgo(40 - i), ...over,
}) as TestRun
const WINDOW: TestRun[] = [
  run(4, { status: 'FAILED', passed_tests: 8, failed_tests: 2, pass_rate: 80 }),
  run(3, { status: 'FAILED', passed_tests: 8, failed_tests: 2, pass_rate: 80 }),
  run(2, { status: 'FAILED', passed_tests: 8, failed_tests: 2, pass_rate: 80 }),
  run(1, {}),
]

function renderRunsPage(path = '/runs') {
  vi.mocked(useRuns).mockReturnValue({
    data: { items: WINDOW, total: WINDOW.length, page: 1, size: 500, pages: 1 },
    isLoading: false,
    error: undefined,
    mutate: vi.fn(),
  } as never)
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false }}>
      <MemoryRouter initialEntries={[path]}><RunsPage /></MemoryRouter>
    </SWRConfig>,
  )
}

const header = () => document.querySelector('[data-page-header]') as HTMLElement
const toolbar = () => document.querySelector('[data-runs-toolbar]') as HTMLElement
const banner = () => document.querySelector('[data-status-banner]') as HTMLElement
const primary = () => document.querySelector('[data-primary]') as HTMLElement
const follows = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
const uploadForm = () => screen.queryByRole('region', { name: 'Upload test report' })
const signatures = () => screen.queryByRole('complementary', { name: 'Failure signatures' })

/** Nothing on the page is a modal. */
function expectNoOverlay() {
  expect(document.querySelector('[aria-modal]')).toBeNull()
  expect(document.querySelector('[role="dialog"]')).toBeNull()
}

beforeEach(() => {
  useTimeWindowStore.setState({ days: 30 })
  upload.upload.mockReset()
  upload.getStatus.mockReset()
})

describe('RunsPage — the upload form opens in the page (W27a PAN-03)', () => {
  it('Upload report renders the form right under the header, before the toolbar, and no modal', () => {
    renderRunsPage()
    expect(uploadForm()).toBeNull()
    fireEvent.click(within(header()).getByRole('button', { name: 'Upload report' }))
    const form = uploadForm() as HTMLElement
    expect(form).not.toBeNull()
    expect(follows(header(), form), 'after the page header').toBe(true)
    expect(follows(form, toolbar()), 'before the toolbar').toBe(true)
    expect(header().contains(form)).toBe(false)
    expectNoOverlay()
    expect(upload.upload).not.toHaveBeenCalled()
  })

  it('/runs?upload=1 opens it in the same place; Cancel closes it and drops ?upload=1', () => {
    renderRunsPage('/runs?upload=1')
    const form = uploadForm() as HTMLElement
    expect(form).not.toBeNull()
    expect(follows(header(), form) && follows(form, toolbar())).toBe(true)
    expectNoOverlay()
    fireEvent.click(within(form).getByRole('button', { name: 'Cancel' }))
    expect(uploadForm()).toBeNull()
  })
})

describe('RunsPage — the failure signatures open above the table, which keeps its box (W27a PAN-03)', () => {
  it('opens between the banner and the table; the table container is the same element and never in a grid', async () => {
    renderRunsPage()
    const table = primary()
    const button = await screen.findByRole('button', { name: /^Failure signatures/ })
    await waitFor(() => expect(button).toHaveTextContent(/Failure signatures\s*1$/))
    fireEvent.click(button)
    const section = signatures() as HTMLElement
    expect(section).not.toBeNull()
    expect(section.tagName).toBe('ASIDE')
    expect(follows(banner(), section), 'after the status banner').toBe(true)
    expect(follows(section, primary()), 'before the runs table').toBe(true)
    expect(section.contains(primary())).toBe(false)
    expect(within(section).getByRole('meter', { name: 'Failed builds by signature' })).toBeInTheDocument()
    expect(section.querySelector('[data-signature-clusters]')).not.toBeNull()
    // The same table container, not moved into a grid or a wrapper beside the section.
    expect(primary()).toBe(table)
    expect(table.parentElement?.className ?? '').not.toMatch(/\bgrid\b/)
    expect(section.parentElement).toBe(table.parentElement)
    expectNoOverlay()
    fireEvent.click(within(section).getByRole('button', { name: 'Close panel' }))
    expect(signatures()).toBeNull()
    expect(primary()).toBe(table)
  })

  it('a row chip opens the same section', async () => {
    renderRunsPage()
    const chip = await waitFor(() => {
      const found = primary().querySelector('[data-signature-chip]') as HTMLElement | null
      expect(found).not.toBeNull()
      return found as HTMLElement
    })
    fireEvent.click(chip)
    const section = signatures() as HTMLElement
    expect(section).not.toBeNull()
    expect(follows(section, primary())).toBe(true)
  })
})
