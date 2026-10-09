/**
 * The test case body (UX redesign P4, `02-design-spec.md` §5 "Test case"):
 * chips → the error + stack trace + AI root cause (`data-primary`) → tabs
 * History · Steps · Details (`?tab=`), and the `compact` shape the Run page
 * opens in a side panel.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import TestCaseBody from './TestCaseBody'
import { useTestCase, useTestCaseHistory, useTestStepFlips, useTestSteps } from '@/hooks/useRuns'

vi.mock('@/hooks/useRuns', () => ({
  useTestCase: vi.fn(),
  useTestSteps: vi.fn(() => ({ data: undefined, isLoading: false })),
  useTestCaseHistory: vi.fn(() => ({ data: undefined, isLoading: false })),
  useTestStepFlips: vi.fn(() => ({ data: undefined, isLoading: false })),
}))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (s: unknown) => unknown) =>
    selector({ activeProjectId: 'proj-1', activeProject: { id: 'proj-1', name: 'Project One' } })),
}))
// The AI panel fetches its stored analysis; here it only has to be where it belongs.
vi.mock('@/components/ai/AIAnalysisPanel', () => ({
  default: ({ testCaseId }: { testCaseId: string }) => <div data-testid="ai-panel">AI analysis of {testCaseId}</div>,
}))

const ERROR = 'AssertionError: expected total 108.00 but was 100.00'
const TRACE = `${ERROR}\n    at com.example.checkout.TotalSpec.checkoutTotalIncludesTax(TotalSpec.java:42)`

function failedCase(over: Record<string, unknown> = {}) {
  return {
    id: 'test-1',
    test_name: 'checkout total includes tax',
    full_name: 'com.example.checkout.TotalSpec.checkoutTotalIncludesTax',
    class_name: 'TotalSpec',
    suite_name: 'Checkout',
    status: 'FAILED',
    duration_ms: 4_210,
    severity: 'critical',
    feature: 'Totals',
    owner: 'payments-team',
    created_at: '2026-09-18T10:00:00Z',
    tags: ['smoke', 'checkout'],
    error_message: ERROR,
    has_attachments: false,
    identity: { test_case_id: 'test-1', fingerprint: 'fp-checkout-total', history_id: 'hist-1' },
    execution: { status: 'FAILED', error_message: ERROR, stack_trace: TRACE, step_count: 3, failure_category: 'PRODUCT_BUG' },
    provenance: { parser_format: 'junit', parser_version: '2.4.1', format: 'junit' },
    ...over,
  }
}

function setCase(tc: unknown) {
  vi.mocked(useTestCase).mockReturnValue({ data: tc, isLoading: false } as never)
}

function Where() {
  const { search } = useLocation()
  return <output data-testid="search">{search}</output>
}

function renderBody(path = '/runs/run-1/tests/test-1', compact = false) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/runs/:runId/tests/:testId"
          element={<><TestCaseBody runId="run-1" testId="test-1" compact={compact} /><Where /></>}
        />
      </Routes>
    </MemoryRouter>,
  )
}

const primary = () => document.querySelector('[data-primary]') as HTMLElement
const tablist = () => screen.getByRole('tablist', { name: 'Test case sections' })
const follows = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)

beforeEach(() => {
  setCase(failedCase())
  vi.mocked(useTestSteps).mockClear()
  vi.mocked(useTestCaseHistory).mockClear()
  vi.mocked(useTestStepFlips).mockClear()
})

describe('TestCaseBody — the answer first (P4)', () => {
  it('puts the chips, then the error + stack trace + AI root cause, before the tabs', () => {
    renderBody()
    const chips = document.querySelector('[data-test-case-chips]') as HTMLElement
    expect(document.querySelectorAll('[data-primary]')).toHaveLength(1)
    expect(follows(chips, primary())).toBe(true)
    expect(follows(primary(), tablist())).toBe(true)
    // The chips: suite, class, status, duration, tags.
    for (const text of ['Checkout', 'TotalSpec', 'FAILED', '4.2s', 'smoke', 'checkout']) {
      expect(within(chips).getAllByText(text, { exact: true }).length, text).toBeGreaterThan(0)
    }
    // The answer: the error, the trace (under a plain title, not `<Class>.java`), the AI root cause.
    expect(within(primary()).getByText(ERROR, { selector: '[data-test-case-error]' })).toBeInTheDocument()
    expect(within(primary()).getByText('Stack trace')).toBeInTheDocument()
    expect(within(primary()).getByText(/TotalSpec\.java:42/)).toBeInTheDocument()
    expect(within(primary()).getByTestId('ai-panel')).toHaveTextContent('AI analysis of test-1')
    expect(screen.queryByText('TotalSpec.java')).toBeNull()
  })

  it('without a stack trace the error message is the log, under its own name', () => {
    setCase(failedCase({ execution: { status: 'FAILED', error_message: ERROR } }))
    renderBody()
    expect(within(primary()).getByText('Error message')).toBeInTheDocument()
    expect(primary().querySelector('[data-test-case-error]')).toBeNull()
    expect(within(primary()).getByText(ERROR)).toBeInTheDocument()
  })

  it('a passed test: no AI analysis is offered', () => {
    setCase(failedCase({ status: 'PASSED', error_message: null, execution: { status: 'PASSED' } }))
    renderBody()
    expect(within(primary()).queryByTestId('ai-panel')).toBeNull()
    expect(within(primary()).getByText('AI analysis is only available for failed or broken tests')).toBeInTheDocument()
  })

  it('deletes the metadata grid that repeated the chips (§5): its fields live in Details', () => {
    renderBody()
    expect(screen.queryByText('Run Date')).toBeNull()
    expect(screen.queryByText('payments-team')).toBeNull()
    fireEvent.click(within(tablist()).getByRole('tab', { name: /Details/ }))
    const result = screen.getByRole('region', { name: 'This result' })
    for (const text of ['critical', 'Totals', 'payments-team', 'PRODUCT_BUG', 'test-1']) {
      expect(within(result).getByText(text), text).toBeInTheDocument()
    }
    expect(within(result).getByRole('link', { name: 'run-1' })).toHaveAttribute('href', '/runs/run-1')
  })
})

describe('TestCaseBody — the tabs (?tab=)', () => {
  it('History is the default: the cross-run history and the step-flip report; the step tree is not asked for', () => {
    renderBody()
    expect(within(tablist()).getByRole('tab', { name: /History/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('No cross-run history available for this test.')).toBeInTheDocument()
    expect(screen.getByText('Cross-run step flakiness')).toBeInTheDocument()
    expect(useTestCaseHistory).toHaveBeenCalledWith('run-1', 'test-1')
    expect(useTestStepFlips).toHaveBeenCalledWith('run-1', 'test-1')
    expect(useTestSteps).not.toHaveBeenCalled()
  })

  it('?tab=steps opens Steps (the step count in its label), and asks for the step tree', () => {
    renderBody('/runs/run-1/tests/test-1?tab=steps')
    const steps = within(tablist()).getByRole('tab', { name: /Steps/ })
    expect(steps).toHaveAttribute('aria-selected', 'true')
    expect(steps).toHaveTextContent('Steps3')
    expect(screen.getByRole('tabpanel')).toHaveAttribute('data-test-case-tab', 'steps')
    expect(useTestSteps).toHaveBeenCalledWith('run-1', 'test-1')
    expect(screen.queryByText('Cross-run step flakiness')).toBeNull()
  })

  it('?tab=details opens Details: the ids, the fingerprint, the parser', () => {
    renderBody('/runs/run-1/tests/test-1?tab=details')
    expect(within(tablist()).getByRole('tab', { name: /Details/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('fp-checkout-total')).toBeInTheDocument()
    expect(screen.getByText('hist-1')).toBeInTheDocument()
    expect(screen.getByText('2.4.1')).toBeInTheDocument()
  })

  it('a click writes ?tab=, and the default tab has the clean URL', () => {
    renderBody()
    fireEvent.click(within(tablist()).getByRole('tab', { name: /Details/ }))
    expect(screen.getByTestId('search')).toHaveTextContent('?tab=details')
    fireEvent.click(within(tablist()).getByRole('tab', { name: /History/ }))
    expect(screen.getByTestId('search')).toHaveTextContent(/^$/)
  })

  it('an unknown ?tab= reads as History', () => {
    renderBody('/runs/run-1/tests/test-1?tab=analysis')
    expect(within(tablist()).getByRole('tab', { name: /History/ })).toHaveAttribute('aria-selected', 'true')
  })
})

describe('TestCaseBody — compact (the Run page\'s side panel)', () => {
  it('has no data-primary (the host page has its own), shows the full name, and keeps the tab out of the URL', () => {
    renderBody('/runs/run-1/tests/test-1?tab=tests', true)
    expect(document.querySelector('[data-primary]')).toBeNull()
    expect(document.querySelector('[data-test-case-body]')).toHaveAttribute('data-compact', 'true')
    expect(screen.getByText('com.example.checkout.TotalSpec.checkoutTotalIncludesTax')).toBeInTheDocument()
    // The host's ?tab=tests is not ours: History is selected, and a click leaves the URL alone.
    expect(within(tablist()).getByRole('tab', { name: /History/ })).toHaveAttribute('aria-selected', 'true')
    fireEvent.click(within(tablist()).getByRole('tab', { name: /Steps/ }))
    expect(within(tablist()).getByRole('tab', { name: /Steps/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByTestId('search')).toHaveTextContent('?tab=tests')
  })

  it('still answers first: the error and the AI root cause precede the tabs', () => {
    renderBody('/runs/run-1/tests/test-1', true)
    const answer = screen.getByRole('region', { name: 'Failure and root cause' })
    expect(follows(answer, tablist())).toBe(true)
    expect(within(answer).getByTestId('ai-panel')).toBeInTheDocument()
  })

  it('renders its own loading and not-found states (no page around it)', () => {
    vi.mocked(useTestCase).mockReturnValue({ data: undefined, isLoading: false } as never)
    renderBody('/runs/run-1/tests/test-1', true)
    expect(screen.getByText('Test case not found')).toBeInTheDocument()
  })
})

// Browser E2E pass (2026-10-08): "📎 Attachments available — open via Allure
// report link", and no such link exists anywhere in the app.
describe('TestCaseBody — attachments', () => {
  const notice = () => document.querySelector('[data-test-case-attachments]')

  it('points to the Steps tab when attachments are stored, and opens it', () => {
    setCase(failedCase({
      has_attachments: true,
      attachments: [{ id: 'a1', name: 'screenshot.png' }, { id: 'a2', name: 'console.log' }],
    }))
    renderBody()
    expect(notice()).toHaveTextContent('📎 2 attachments, in the Steps tab')
    expect(notice()).not.toHaveTextContent(/Allure/)
    fireEvent.click(screen.getByRole('button', { name: 'in the Steps tab' }))
    expect(screen.getByTestId('search')).toHaveTextContent('tab=steps')
  })

  it('says none were stored when the report listed attachments but none came with the result', () => {
    setCase(failedCase({ has_attachments: true }))
    renderBody()
    expect(notice()).toHaveTextContent("📎 Its report listed attachments; none were stored with this result.")
  })

  it('says nothing when there are none', () => {
    setCase(failedCase())
    renderBody()
    expect(notice()).toBeNull()
  })
})
