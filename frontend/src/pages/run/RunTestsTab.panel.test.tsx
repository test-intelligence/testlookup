/**
 * UX redesign P4 item 2: a test opens from the Run page beside the list — the
 * test case body (compact), "Open full page", and the previous / next failure
 * on the page — instead of leaving the run. Ctrl / ⌘-click still opens the
 * full page.
 *
 * W27a PAN-03: the pane is IN THE PAGE (a `DetailPane` in the /test-management
 * split, an `aside` named by the test, so `complementary` still finds it), and
 * the open test rides the URL (`?test=`): opening pushes (Back closes), a
 * switch replaces, a malformed value never reaches `TestCaseBody`.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useEffect } from 'react'
import { MemoryRouter, Route, RouterProvider, Routes, createMemoryRouter, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import RunTestsTab from './RunTestsTab'
import type { TestRun } from '@/types/runs'

const { useTestCases } = vi.hoisted(() => ({ useTestCases: vi.fn() }))
vi.mock('@/hooks/useRuns', () => ({
  useTestCases,
  useRunAttribution: vi.fn(() => ({ data: { items: [] } })),
}))
// The body is TestCaseBody's own concern (TestCaseBody.test.tsx): here, which test, how. Every render's props are
// recorded: a value that is not an id must never reach it (it would read `/runs/{run}/tests/{value}`).
const bodies = vi.hoisted(() => ({ props: [] as { runId: string; testId: string }[] }))
vi.mock('@/pages/testCase/TestCaseBody', () => ({
  default: ({ runId, testId, compact }: { runId: string; testId: string; compact?: boolean }) => {
    bodies.props.push({ runId, testId })
    return <div data-testid="test-case-body">{`${runId}/${testId}${compact ? ' compact' : ''}`}</div>
  },
}))

const ROWS = [
  { id: 'p1', test_name: 'login works', status: 'PASSED' },
  { id: 'f1', test_name: 'card declined shows reason', status: 'FAILED' },
  { id: 'p2', test_name: 'search paginates', status: 'PASSED' },
  { id: 'f2', test_name: 'refund webhook retried', status: 'BROKEN' },
]
const RUN = { id: 'r1', total_tests: 4, passed_tests: 2, failed_tests: 1, broken_tests: 1, skipped_tests: 0 } as unknown as TestRun

let path = ''
function Where() {
  const location = useLocation()
  useEffect(() => { path = location.pathname }, [location])
  return null
}

function renderTab() {
  return render(
    <MemoryRouter initialEntries={['/runs/r1']}>
      <Routes>
        <Route
          path="*"
          element={
            <>
              <RunTestsTab runId="r1" run={RUN} statusFilter="" suiteFilter="" page={1} onStatusFilter={vi.fn()} onSuiteFilter={vi.fn()} onPage={vi.fn()} />
              <Where />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  )
}

const row = (id: string) => document.querySelector(`[data-test-row="${id}"]`) as HTMLElement
const panel = () => screen.getByRole('complementary', { name: /./ })

describe('Run page › Tests: a test opens in a side panel', () => {
  beforeEach(() => {
    path = ''
    bodies.props = []
    useTestCases.mockReturnValue({ data: { items: ROWS, total: 4, pages: 1 }, isLoading: false, error: undefined })
  })

  it('a row opens its test beside the list, compact, and stays on the run', () => {
    renderTab()
    fireEvent.click(row('f1'))
    expect(path).toBe('/runs/r1')
    expect(within(panel()).getByRole('heading', { name: 'card declined shows reason' })).toBeInTheDocument()
    expect(within(panel()).getByTestId('test-case-body')).toHaveTextContent('r1/f1 compact')
    expect(row('f1')).toHaveAttribute('aria-selected', 'true')
    expect(within(panel()).getByRole('link', { name: /Open full page/ })).toHaveAttribute('href', '/runs/r1/tests/f1')
  })

  it('walks the failures on the page in the table order: failed and broken first', () => {
    renderTab()
    fireEvent.click(row('f1'))
    const prev = () => within(panel()).getByRole('button', { name: /Previous failure/ })
    const next = () => within(panel()).getByRole('button', { name: /Next failure/ })
    expect(panel()).toHaveTextContent('1 of 2 failures on this page')
    expect(prev()).toBeDisabled()
    fireEvent.click(next())
    expect(within(panel()).getByTestId('test-case-body')).toHaveTextContent('r1/f2 compact')
    expect(panel()).toHaveTextContent('2 of 2 failures on this page')
    expect(next()).toBeDisabled()
    fireEvent.click(prev())
    expect(within(panel()).getByTestId('test-case-body')).toHaveTextContent('r1/f1 compact')
  })

  it('from a passing test, Next goes to the first failure', () => {
    renderTab()
    fireEvent.click(row('p2'))
    expect(within(panel()).queryByText(/failures on this page/)).toBeNull()
    expect(within(panel()).getByRole('button', { name: /Previous failure/ })).toBeDisabled()
    fireEvent.click(within(panel()).getByRole('button', { name: /Next failure/ }))
    expect(within(panel()).getByTestId('test-case-body')).toHaveTextContent('r1/f1 compact')
  })

  it('closes, and Ctrl-click opens the full page instead', () => {
    renderTab()
    fireEvent.click(row('f2'))
    fireEvent.click(within(panel()).getByRole('button', { name: 'Close panel' }))
    expect(screen.queryByTestId('test-case-body')).toBeNull()
    fireEvent.click(row('f2'), { ctrlKey: true })
    expect(path).toBe('/runs/r1/tests/f2')
    expect(screen.queryByTestId('test-case-body')).toBeNull()
  })
})

// ── W27a PAN-03: in the page, on the URL ─────────────────────────────────────

const UUID = '9b1c2d3e-4f5a-4b6c-8d7e-0f1a2b3c4d5e'

function routed(entries: string[]) {
  const router = createMemoryRouter(
    [
      { path: '/start', element: <p>START</p> },
      {
        path: '/runs/:runId',
        element: <RunTestsTab runId="r1" run={RUN} statusFilter="" suiteFilter="" page={1} onStatusFilter={vi.fn()} onSuiteFilter={vi.fn()} onPage={vi.fn()} />,
      },
    ],
    { initialEntries: entries, initialIndex: entries.length - 1 },
  )
  render(<RouterProvider router={router} />)
  return router
}

/** The COMMITTED location, read inside the same waitFor (W08). */
async function expectAt(router: ReturnType<typeof createMemoryRouter>, search: string, action?: 'PUSH' | 'REPLACE' | 'POP') {
  await waitFor(() => {
    expect(router.state.location.pathname).toBe('/runs/r1')
    expect(router.state.location.search).toBe(search)
    if (action) expect(router.state.historyAction).toBe(action)
  })
}

const pane = () => screen.queryByRole('complementary', { name: /./ })

describe('Run page › Tests: the test pane is in the page and rides ?test= (W27a PAN-03)', () => {
  beforeEach(() => {
    bodies.props = []
    useTestCases.mockReturnValue({ data: { items: ROWS, total: 4, pages: 1 }, isLoading: false, error: undefined })
  })

  it('a row click PUSHES ?test=<id> and docks the test beside the list (no modal); Back closes it', async () => {
    const router = routed(['/start', '/runs/r1?tab=tests'])
    fireEvent.click(row('f1'))
    await expectAt(router, '?tab=tests&test=f1', 'PUSH')
    const opened = pane() as HTMLElement
    expect(within(opened).getByRole('heading', { name: 'card declined shows reason' })).toBeInTheDocument()
    expect(within(opened).getByTestId('test-case-body')).toHaveTextContent('r1/f1 compact')
    expect(opened.tagName).toBe('ASIDE')
    expect(document.querySelector('[aria-modal]')).toBeNull()
    expect(document.querySelector('[role="dialog"]')).toBeNull()
    // Beside the list in the split; the status chips stay above it, full width.
    const split = document.querySelector('[data-master-detail="split"]') as HTMLElement
    expect(split.contains(opened)).toBe(true)
    expect(split.contains(document.querySelector('section[data-primary]'))).toBe(true)
    expect(split.contains(screen.getByRole('group', { name: 'Filter by status' }))).toBe(false)

    await act(() => router.navigate(-1))
    await expectAt(router, '?tab=tests', 'POP')
    expect(pane()).toBeNull()
    expect(document.querySelector('[data-master-detail="list"]')).not.toBeNull()
  })

  it('Next failure REPLACES (Back from it lands on the closed list); Close pops the pushed entry', async () => {
    const router = routed(['/start', '/runs/r1'])
    fireEvent.click(row('f1'))
    await expectAt(router, '?test=f1', 'PUSH')
    fireEvent.click(within(pane() as HTMLElement).getByRole('button', { name: /Next failure/ }))
    await expectAt(router, '?test=f2', 'REPLACE')
    await act(() => router.navigate(-1))
    await expectAt(router, '', 'POP')
    expect(pane()).toBeNull()

    fireEvent.click(row('f2'))
    await expectAt(router, '?test=f2', 'PUSH')
    fireEvent.click(within(pane() as HTMLElement).getByRole('button', { name: 'Close panel' }))
    await expectAt(router, '', 'POP')
    expect(pane()).toBeNull()
  })

  it('a deep link (a refresh) reopens the test: one of the rows, read by TestCaseBody', () => {
    routed(['/runs/r1?test=f2'])
    expect(within(pane() as HTMLElement).getByRole('heading', { name: 'refund webhook retried' })).toBeInTheDocument()
    expect(bodies.props[bodies.props.length - 1]).toEqual({ runId: 'r1', testId: 'f2' })
  })

  it('a UUID that is not on this page opens titled "Test", and TestCaseBody reads it (its own not-found line)', () => {
    routed([`/runs/r1?test=${UUID}`])
    const opened = pane() as HTMLElement
    expect(within(opened).getByRole('heading', { name: 'Test' })).toBeInTheDocument()
    expect(bodies.props[bodies.props.length - 1]).toEqual({ runId: 'r1', testId: UUID })
    expect(within(opened).getByRole('link', { name: /Open full page/ })).toHaveAttribute('href', `/runs/r1/tests/${UUID}`)
  })

  it.each(['not-a-uuid', '../../admin', 'a%2Fb'])('?test=%s: "Test case not found", and TestCaseBody is never rendered (no request)', async (raw) => {
    const router = routed([`/runs/r1?test=${raw}`])
    const opened = pane() as HTMLElement
    expect(within(opened).getByRole('heading', { name: 'Test' })).toBeInTheDocument()
    expect(within(opened).getByText('Test case not found')).toBeInTheDocument()
    expect(within(opened).queryByRole('link', { name: /Open full page/ })).toBeNull()
    expect(bodies.props).toEqual([])
    expect(screen.queryByTestId('test-case-body')).toBeNull()
    // Closing a deep-linked (not pushed) entry REPLACES with the key removed.
    fireEvent.click(within(opened).getByRole('button', { name: 'Close panel' }))
    await expectAt(router, '', 'REPLACE')
    expect(pane()).toBeNull()
  })

  it('Escape inside the pane closes it', async () => {
    const router = routed(['/start', '/runs/r1'])
    fireEvent.click(row('p1'))
    await expectAt(router, '?test=p1', 'PUSH')
    fireEvent.keyDown(within(pane() as HTMLElement).getByRole('button', { name: 'Close panel' }), { key: 'Escape' })
    await expectAt(router, '', 'POP')
    expect(pane()).toBeNull()
  })
})
