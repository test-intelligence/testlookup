/**
 * W08 TRND-07 "Fix tagging": the side panel over the REAL hooks and guard,
 * with the services mocked. The copy (moving never relabels an execution),
 * the select-and-move body, `missing_ids` reported, the default-suite option
 * only for a QA lead, a test with no catalogue row listed but not selectable,
 * a viewer read-only, Load more, and a malformed page degrading to text.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore } from '@/store/authStore'

const pages = vi.hoisted(() => ({ getUntaggedTests: vi.fn(), getCoverageHistory: vi.fn() }))
vi.mock('@/services/coverageHistoryService', () => ({ UNTAGGED_PAGE_SIZE: 50, coverageHistoryService: pages }))

const suites = vi.hoisted(() => ({ bulkLinkCanonicals: vi.fn(), setDefault: vi.fn() }))
vi.mock('@/services/suitesService', () => ({ suitesService: suites }))

vi.mock('@/hooks/useSuites', () => ({
  useSuites: () => ({
    data: {
      items: [
        { id: 's-checkout', name: 'Checkout', project_id: 'proj-1' },
        { id: 's-auth', name: 'Auth <b>', project_id: 'proj-1' },
      ],
      total: 2,
    },
  }),
}))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

import FixTaggingPanel, { FIX_TAGGING_COPY, NOT_IN_CATALOGUE_TITLE, REPORTER_DOCS_HREF } from './FixTaggingPanel'

const item = (n: number, over: Record<string, unknown> = {}) => ({
  canonical_id: `c-${n}`,
  test_fingerprint: `fp-${n}`,
  test_name: `test_${n}`,
  class_name: 'tests.checkout',
  catalogue_suite_id: 's-default',
  catalogue_suite: 'Default Suite (Project One)',
  executions: 3,
  last_executed_at: '2026-10-15T10:00:00Z',
  ...over,
})

function page(items: unknown[], total = items.length, n = 1) {
  return { items, total, page: n, size: 50, window_days: 30 }
}

function renderPanel(role: string) {
  act(() => useAuthStore.setState({ user: { id: 'u-1', role } as never }))
  return render(
    <MemoryRouter>
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false }}>
        <FixTaggingPanel projectId="proj-1" days={30} onClose={vi.fn()} />
      </SWRConfig>
    </MemoryRouter>,
  )
}

describe('FixTaggingPanel', () => {
  beforeEach(() => {
    pages.getUntaggedTests.mockReset()
    suites.bulkLinkCanonicals.mockReset()
    suites.setDefault.mockReset()
  })
  afterEach(() => {
    act(() => useAuthStore.setState({ user: null }))
  })

  it('always says moving files a test in the catalogue and never relabels its executions, and links the reporter docs', async () => {
    pages.getUntaggedTests.mockResolvedValue(page([item(1)]))
    renderPanel('VIEWER')
    const panel = screen.getByRole('complementary', { name: 'Fix tagging' })
    expect(panel).toHaveTextContent(FIX_TAGGING_COPY)
    expect(within(panel).getByRole('link', { name: 'How the reporter labels a run' })).toHaveAttribute('href', REPORTER_DOCS_HREF)
    expect(await screen.findByText('test_1')).toBeInTheDocument()
  })

  it('a viewer reads the list: no checkboxes, no target, no move', async () => {
    pages.getUntaggedTests.mockResolvedValue(page([item(1)]))
    renderPanel('VIEWER')
    await screen.findByText('test_1')
    expect(screen.queryByRole('checkbox')).toBeNull()
    expect(screen.queryByRole('combobox')).toBeNull()
    expect(screen.queryByRole('button', { name: /^Move/ })).toBeNull()
  })

  it('a QA engineer selects tests and moves them: the existing bulk-link body; missing ids reported; the list refetched', async () => {
    pages.getUntaggedTests.mockResolvedValue(page([item(1), item(2), item(3)]))
    suites.bulkLinkCanonicals.mockResolvedValue({ moved: 2, skipped_already_in_target: 0, missing_ids: ['c-3'] })
    renderPanel('QA_ENGINEER')
    await screen.findByText('test_1')
    const move = () => screen.getByRole('button', { name: /^Move/ })
    expect(move()).toBeDisabled()
    for (const n of [1, 2, 3]) fireEvent.click(screen.getByRole('checkbox', { name: `Select test_${n}` }))
    expect(move()).toBeDisabled() // no target yet
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 's-checkout' } })
    expect(move()).toHaveTextContent('Move 3 tests to Checkout')
    // A QA engineer is not offered the default-suite change.
    expect(screen.queryByText(/default suite for new unlabelled tests/)).toBeNull()
    const callsBefore = pages.getUntaggedTests.mock.calls.length
    fireEvent.click(move())
    await waitFor(() => expect(suites.bulkLinkCanonicals).toHaveBeenCalledWith('s-checkout', ['c-1', 'c-2', 'c-3']))
    expect(await screen.findByRole('status')).toHaveTextContent('Moved 2 tests to Checkout · 1 was no longer in the catalogue')
    expect(suites.setDefault).not.toHaveBeenCalled()
    await waitFor(() => expect(pages.getUntaggedTests.mock.calls.length).toBeGreaterThan(callsBefore))
  })

  it('a test with no catalogue row is listed, not selectable, and says why', async () => {
    pages.getUntaggedTests.mockResolvedValue(page([item(1, { canonical_id: null, catalogue_suite: null })]))
    renderPanel('QA_ENGINEER')
    await screen.findByText('test_1')
    const box = screen.getByRole('checkbox', { name: 'Select test_1' })
    expect(box).toBeDisabled()
    expect(box).toHaveAttribute('title', NOT_IN_CATALOGUE_TITLE)
    expect(screen.getByText(/not in the catalogue yet/)).toBeInTheDocument()
  })

  it('a QA lead may also make the target the default suite; the option names it as text', async () => {
    pages.getUntaggedTests.mockResolvedValue(page([item(1)]))
    suites.bulkLinkCanonicals.mockResolvedValue({ moved: 1, skipped_already_in_target: 0, missing_ids: [] })
    suites.setDefault.mockResolvedValue({})
    renderPanel('QA_LEAD')
    await screen.findByText('test_1')
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select test_1' }))
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 's-auth' } })
    const option = screen.getByRole('checkbox', { name: 'Also make Auth <b> the default suite for new unlabelled tests' })
    expect(document.querySelector('[data-fix-tagging-panel] b')).toBeNull()
    fireEvent.click(option)
    fireEvent.click(screen.getByRole('button', { name: /^Move 1 test to Auth/ }))
    await waitFor(() => expect(suites.setDefault).toHaveBeenCalledWith('s-auth'))
    expect(await screen.findByRole('status')).toHaveTextContent('Moved 1 test to Auth <b> · Auth <b> is now the default suite')
  })

  it('Load more asks for the next page of 50', async () => {
    pages.getUntaggedTests.mockImplementation(async (_p: string, _d: number, n: number) =>
      n === 1 ? page([item(1)], 51, 1) : page([item(51)], 51, 2),
    )
    renderPanel('QA_ENGINEER')
    await screen.findByText('test_1')
    // 51 in all, 50 a page: one more after the first page.
    fireEvent.click(screen.getByRole('button', { name: 'Load more (1 more test)' }))
    expect(await screen.findByText('test_51')).toBeInTheDocument()
    expect(pages.getUntaggedTests).toHaveBeenCalledWith('proj-1', 30, 2, 50)
  })

  it('a malformed answer says the list is not available (no crash)', async () => {
    pages.getUntaggedTests.mockResolvedValue({ items: 'nope' })
    renderPanel('QA_ENGINEER')
    expect(await screen.findByText('The untagged tests are not available.')).toBeInTheDocument()
  })

  it('a refused move shows the server text, not a crash', async () => {
    pages.getUntaggedTests.mockResolvedValue(page([item(1)]))
    suites.bulkLinkCanonicals.mockRejectedValue({ response: { data: { detail: 'Every id must be in the suite project.' } } })
    renderPanel('QA_ENGINEER')
    await screen.findByText('test_1')
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select test_1' }))
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 's-checkout' } })
    fireEvent.click(screen.getByRole('button', { name: /^Move/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Every id must be in the suite project.')
  })
})
