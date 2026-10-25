/**
 * W13 TCL-03: a Strategy-gaps row's cases — Show 5 from props (no request),
 * Show all asking `useEvidenceGapPage(projectId, kind, 1, true)` only then and
 * paging it, the unmeasured reason (never 0), a row opening its case.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { TestCaseEvidenceGapResponse } from '@/types/test-management'
import type { LibraryCaseList } from '@/types/test-library'

let pageAnswer: { data?: TestCaseEvidenceGapResponse; error?: unknown; isLoading: boolean }
const mutate = vi.fn()
const useEvidenceGapPage = vi.fn((..._args: unknown[]) => ({ ...pageAnswer, mutate }))

vi.mock('@/hooks/useTestLibrary', () => ({
  useEvidenceGapPage: (...args: unknown[]) => useEvidenceGapPage(...args),
}))

import LibraryCaseListRows from './LibraryCaseListRows'

const FLAKY: LibraryCaseList = {
  total: 30,
  measured: true,
  items: Array.from({ length: 5 }, (_, i) => ({
    id: `f-${i}`, title: i === 0 ? '<img src=x onerror=alert(1)> flaky' : `Flaky case ${i}`, status: 'active',
    owner: 'Priya Shah', detail: `flaky score 0.7${i} (high confidence)`,
  })),
}

function page(n: number, count: number, total = 30): TestCaseEvidenceGapResponse {
  return {
    total,
    items: Array.from({ length: count }, (_, i) => ({
      id: `p${n}-${i}`, project_id: 'p-1', title: `Page ${n} case ${i}`, status: 'active',
      flaky_score: 0.725, flaky_confidence: 'high',
    })),
  }
}

const onOpenCase = vi.fn()

beforeEach(() => {
  mutate.mockReset()
  onOpenCase.mockReset()
  useEvidenceGapPage.mockClear()
  pageAnswer = { data: page(1, 25), isLoading: false }
})

describe('LibraryCaseListRows', () => {
  it('opens the first five from the health answer without a request', () => {
    const { container } = render(<LibraryCaseListRows projectId="p-1" kind="flaky_active" list={FLAKY} onOpenCase={onOpenCase} />)
    expect(useEvidenceGapPage).toHaveBeenLastCalledWith('p-1', 'flaky_active', 1, false)
    fireEvent.click(screen.getByRole('button', { name: 'Show 5' }))
    expect(container.querySelectorAll('[data-library-case]')).toHaveLength(5)
    expect(screen.getByText('Priya Shah · flaky score 0.71 (high confidence)')).toBeInTheDocument()
    expect(screen.getByText('<img src=x onerror=alert(1)> flaky')).toBeInTheDocument()
    expect(container.querySelector('img')).toBeNull()
    expect(useEvidenceGapPage.mock.calls.every((call) => call[3] === false)).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Flaky case 2' }))
    expect(onOpenCase).toHaveBeenCalledWith('f-2')
  })

  it('pages every match behind Show all, asked only once opened', () => {
    render(<LibraryCaseListRows projectId="p-1" kind="no_owner" list={{ ...FLAKY, items: FLAKY.items.slice(1) }} onOpenCase={onOpenCase} />)
    expect(useEvidenceGapPage).not.toHaveBeenCalledWith('p-1', 'no_owner', 1, true)
    fireEvent.click(screen.getByRole('button', { name: 'Show all 30' }))
    expect(useEvidenceGapPage).toHaveBeenLastCalledWith('p-1', 'no_owner', 1, true)
    const first = document.querySelector('[data-library-case-page="1"]') as HTMLElement
    expect(within(first).getAllByRole('button')).toHaveLength(25)
    expect(screen.getByText('1-25 of 30')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
    pageAnswer = { data: page(2, 5), isLoading: false }
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(useEvidenceGapPage).toHaveBeenLastCalledWith('p-1', 'no_owner', 2, true)
    expect(screen.getByText('26-30 of 30')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Page 2 case 3' }))
    expect(onOpenCase).toHaveBeenCalledWith('p2-3')
  })

  it('words a page row as the preview does', () => {
    pageAnswer = {
      data: { total: 2, items: [
        { id: 'a', project_id: 'p-1', title: 'A', status: 'active', owner_state: 'unassigned' },
        { id: 'b', project_id: 'p-1', title: 'B', status: 'draft', owner_state: 'deactivated' },
      ] },
      isLoading: false,
    }
    render(<LibraryCaseListRows projectId="p-1" kind="no_owner" list={{ total: 12, measured: true, items: [] }} onOpenCase={onOpenCase} />)
    fireEvent.click(screen.getByRole('button', { name: 'Show all 12' }))
    expect(screen.getByText('unassigned')).toBeInTheDocument()
    expect(screen.getByText('owner deactivated')).toBeInTheDocument()
  })

  it('reads the reason of an unmeasured list, never a zero', () => {
    const reason = 'No flaky scores have been computed for this project yet.'
    const { container } = render(
      <LibraryCaseListRows projectId="p-1" kind="flaky_active" list={{ total: 0, items: [], measured: false, reason }} onOpenCase={onOpenCase} />,
    )
    expect(screen.getByText(reason)).toBeInTheDocument()
    expect(container.textContent).not.toMatch(/\b0\b/)
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('says when a page could not be read, with Retry', () => {
    pageAnswer = { data: undefined, error: new Error('500'), isLoading: false }
    render(<LibraryCaseListRows projectId="p-1" kind="flaky_active" list={FLAKY} onOpenCase={onOpenCase} />)
    fireEvent.click(screen.getByRole('button', { name: 'Show all 30' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Could not load the cases.')
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(mutate).toHaveBeenCalledTimes(1)
  })
})
