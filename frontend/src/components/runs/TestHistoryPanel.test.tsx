/**
 * A partial history payload must not take the page down with it.
 *
 * Found by an e2e drill-down test: the panel destructured `flakiness` and
 * `metadata` out of the response and dereferenced both unguarded. The
 * `flakiness.classification?.` optional chain guards the PROPERTY, not the
 * object, so a response that simply lacked the section threw
 *
 *     Cannot read properties of undefined (reading 'classification')
 *
 * which the page's error boundary turned into "Something went wrong loading
 * this page" -- the whole test-case view replaced because one side panel had
 * nothing to show.
 */
import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'

const historyState: { data: unknown; isLoading: boolean } = { data: undefined, isLoading: false }

vi.mock('@/hooks/useRuns', () => ({
  useTestCaseHistory: () => historyState,
}))

import TestHistoryPanel from './TestHistoryPanel'

const EMPTY = /no cross-run history available/i

describe('TestHistoryPanel — partial payloads', () => {
  beforeEach(() => {
    historyState.isLoading = false
    historyState.data = undefined
  })

  it('renders the empty state when the response has no flakiness section', () => {
    // The exact shape that crashed: a valid object, missing the section.
    historyState.data = { history: [], metadata: { first_seen_at: null } }
    render(<TestHistoryPanel runId="r1" testId="t1" />)
    expect(screen.getByText(EMPTY)).toBeInTheDocument()
  })

  it('renders the empty state when the response has no metadata section', () => {
    historyState.data = { history: [], flakiness: { classification: 'STABLE' } }
    render(<TestHistoryPanel runId="r1" testId="t1" />)
    expect(screen.getByText(EMPTY)).toBeInTheDocument()
  })

  it('still renders the empty state for a null response', () => {
    historyState.data = undefined
    render(<TestHistoryPanel runId="r1" testId="t1" />)
    expect(screen.getByText(EMPTY)).toBeInTheDocument()
  })

  it('renders real content when both sections are present', () => {
    // The counterpart: the guard must not swallow a healthy payload, or the
    // panel would show "no history" forever and every test above would pass.
    historyState.data = {
      history: [],
      flakiness: { classification: 'FLAKY', flip_count: 3 },
      metadata: { first_seen_at: '2026-06-01T10:00:00Z', last_seen_at: '2026-06-02T10:00:00Z' },
    }
    render(<TestHistoryPanel runId="r1" testId="t1" />)
    expect(screen.queryByText(EMPTY)).toBeNull()
  })
})
