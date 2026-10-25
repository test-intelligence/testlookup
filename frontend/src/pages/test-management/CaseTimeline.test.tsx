/**
 * W13 TCL-05: the case Timeline — chips → `kinds`, Load more → the next page,
 * the end / empty / error lines, "by not recorded", the three link kinds, and
 * a `<script>` detail rendered as text.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { TimelineEntry } from '@/types/test-library'

interface TimelineState {
  items: TimelineEntry[]
  automationLinked?: boolean
  hasMore: boolean
  isLoading: boolean
  isLoadingMore: boolean
  error?: unknown
}

const loadMore = vi.fn()
const mutate = vi.fn()
let state: TimelineState
const useTestCaseTimeline = vi.fn((..._args: unknown[]) => ({ ...state, pages: [], loadMore, mutate }))

vi.mock('@/hooks/useTestLibrary', () => ({
  useTestCaseTimeline: (...args: unknown[]) => useTestCaseTimeline(...args),
}))

import CaseTimeline from './CaseTimeline'
import { NO_AUTOMATION_TEXT, TIMELINE_EMPTY_TEXT, TIMELINE_END_TEXT, TIMELINE_ERROR_TEXT } from './caseTimelineModel'

const SCRIPT = '<script>alert("x")</script>'

function entry(id: string, kind: string, over: Partial<TimelineEntry> = {}): TimelineEntry {
  return { id, kind, occurred_at: '2026-10-20T10:00:00Z', title: `${kind} ${id}`, actor_name: 'Priya Shah', ...over }
}

const ENTRIES: TimelineEntry[] = [
  entry('r1', 'automated_execution', {
    title: 'Automated run · FAILED', actor_name: null, actor_source: 'automation', status: 'FAILED', detail: 'Build 412 · main',
    link: { type: 'run_result', run_id: 'run 1', test_case_id: 'res/2' },
  }),
  entry('p1', 'plan_execution', { title: 'Executed in plan Sprint 42 · PASSED (v3)', status: 'passed', link: { type: 'plan', plan_id: 'plan-1' } }),
  entry('d1', 'defect', { title: 'Defect PAY-123 opened', status: 'open', detail: SCRIPT, link: { type: 'defect', defect_id: 'def-9', jira_key: 'PAY-123' } }),
  entry('c1', 'comment', { title: 'Comment', actor_name: null, detail: 'Looks good' }),
  entry('v1', 'version', { title: 'Saved as v4 (person)', status: 'active', version: 4 }),
  entry('q1:decided', 'review_decided', { title: 'Approved', status: 'approved' }),
]

function renderTimeline() {
  return render(
    <MemoryRouter>
      <CaseTimeline caseId="case-1" />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  loadMore.mockReset()
  mutate.mockReset()
  useTestCaseTimeline.mockClear()
  state = { items: ENTRIES, automationLinked: true, hasMore: true, isLoading: false, isLoadingMore: false, error: undefined }
})

describe('CaseTimeline', () => {
  it('reads the whole timeline for the case, then one preset per chip', () => {
    renderTimeline()
    expect(useTestCaseTimeline).toHaveBeenLastCalledWith('case-1', [], true)
    const chips = within(screen.getByRole('group', { name: 'Show on the timeline' })).getAllByRole('button')
    expect(chips.map((chip) => chip.textContent)).toEqual(['All', 'Changes', 'Reviews', 'Comments', 'Executions', 'Defects'])
    expect(screen.getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: 'Reviews' }))
    expect(useTestCaseTimeline).toHaveBeenLastCalledWith('case-1', ['review_requested', 'review_decided'], true)
    fireEvent.click(screen.getByRole('button', { name: 'Changes' }))
    expect(useTestCaseTimeline).toHaveBeenLastCalledWith('case-1', ['version', 'requirement'], true)
    fireEvent.click(screen.getByRole('button', { name: 'Executions' }))
    expect(useTestCaseTimeline).toHaveBeenLastCalledWith('case-1', ['plan_execution', 'automated_execution'], true)
    fireEvent.click(screen.getByRole('button', { name: 'All' }))
    expect(useTestCaseTimeline).toHaveBeenLastCalledWith('case-1', [], true)
  })

  it('renders one row per entry with its time, title, actor, status and detail', () => {
    renderTimeline()
    const rows = document.querySelectorAll('[data-timeline-entry]')
    expect(rows).toHaveLength(6)
    expect(Array.from(rows).map((row) => row.getAttribute('data-kind'))).toEqual([
      'automated_execution', 'plan_execution', 'defect', 'comment', 'version', 'review_decided',
    ])
    const comment = document.querySelector('[data-timeline-entry="c1"]') as HTMLElement
    expect(within(comment).getByText('by not recorded')).toBeInTheDocument()
    const run = document.querySelector('[data-timeline-entry="r1"]') as HTMLElement
    expect(within(run).getByText('by automation')).toBeInTheDocument()
    expect(within(run).getByText('Failed')).toBeInTheDocument()
    expect(within(document.querySelector('[data-timeline-entry="p1"]') as HTMLElement).getByText('by Priya Shah')).toBeInTheDocument()
    expect(within(document.querySelector('[data-timeline-entry="q1:decided"]') as HTMLElement).getByText('Approved', { selector: 'span' })).toBeInTheDocument()
    expect(run.querySelector('time')?.getAttribute('title')).toBe('2026-10-20T10:00:00Z')
  })

  it('links a result, a plan and a defect by id only', () => {
    renderTimeline()
    expect(screen.getByRole('link', { name: 'Open the result' }).getAttribute('href')).toBe('/runs/run%201/tests/res%2F2')
    expect(screen.getByRole('link', { name: 'Open the plans' }).getAttribute('href')).toBe('/test-management?tab=plans')
    expect(screen.getByRole('link', { name: 'Open the defect' }).getAttribute('href')).toBe('/defects?defect=def-9')
    expect(screen.getAllByRole('link')).toHaveLength(3)
  })

  it('shows a <script> detail as text', () => {
    const { container } = renderTimeline()
    expect(screen.getByText(SCRIPT)).toBeInTheDocument()
    expect(container.querySelector('script')).toBeNull()
  })

  it('asks for the next page with Load more, and says where the stream starts', () => {
    const { rerender } = renderTimeline()
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
    expect(loadMore).toHaveBeenCalledTimes(1)
    expect(screen.queryByText(TIMELINE_END_TEXT)).toBeNull()
    state = { ...state, hasMore: false }
    rerender(<MemoryRouter><CaseTimeline caseId="case-1" /></MemoryRouter>)
    expect(screen.getByText(TIMELINE_END_TEXT)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Load more' })).toBeNull()
  })

  it('says when nothing is recorded, and when the read failed (with Retry)', () => {
    state = { ...state, items: [], hasMore: false }
    const { rerender } = renderTimeline()
    expect(screen.getByText(TIMELINE_EMPTY_TEXT)).toBeInTheDocument()
    state = { ...state, error: new Error('500') }
    rerender(<MemoryRouter><CaseTimeline caseId="case-1" /></MemoryRouter>)
    expect(screen.getByRole('alert')).toHaveTextContent(TIMELINE_ERROR_TEXT)
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(mutate).toHaveBeenCalledTimes(1)
  })

  it('says under Executions when no automated test is linked', () => {
    state = { ...state, automationLinked: false }
    renderTimeline()
    expect(screen.queryByText(NO_AUTOMATION_TEXT)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Executions' }))
    expect(screen.getByRole('note')).toHaveTextContent(NO_AUTOMATION_TEXT)
  })
})
