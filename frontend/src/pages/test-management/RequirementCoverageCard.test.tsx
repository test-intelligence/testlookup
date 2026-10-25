/**
 * W13 TCL-06: Insights' Requirement coverage card — "12 of 15" (both numbers),
 * the reasons and sources, not measured, truncated, and Show all gaps asking
 * `useTraceabilityGaps` only once opened and paging it.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { RequirementCoverageSummary, RequirementGap, TraceabilityGaps } from '@/types/test-library'

let gapsAnswer: { data?: TraceabilityGaps; error?: unknown; isLoading: boolean }
const mutate = vi.fn()
const useTraceabilityGaps = vi.fn((..._args: unknown[]) => ({ ...gapsAnswer, mutate }))

vi.mock('@/hooks/useTestLibrary', () => ({
  useTraceabilityGaps: (...args: unknown[]) => useTraceabilityGaps(...args),
}))

import RequirementCoverageCard from './RequirementCoverageCard'
import { GAPS_ERROR, TRUNCATED_TEXT, UNMEASURED_TEXT } from './requirementsModel'

function gap(key: string, reason: string, over: Partial<RequirementGap> = {}): RequirementGap {
  return { key, normalized_key: key.toUpperCase(), reason, sources: ['link'], linked_cases: 0, batch_claimed_cases: 0, ...over }
}

const SUMMARY: RequirementCoverageSummary = {
  measured: true, known: 15, verified: 12, partly_verified: 2, gaps: 3, truncated: false,
  top_gaps: [
    gap('REQ-PAY-09', 'no_link', { sources: ['generation_batch'], batch_claimed_cases: 3 }),
    gap('req-pay-11', 'related_only', { sources: ['link', 'knowledge_source'] }),
    gap('REQ-PAY-12', 'partly_verified'),
  ],
}

function gapsPage(page: number, total: number, count: number): TraceabilityGaps {
  return {
    project_id: 'p-1', summary: { ...SUMMARY, top_gaps: [] },
    gaps: Array.from({ length: count }, (_, i) => gap(`REQ-${page}-${i}`, 'no_link')),
    gaps_total: total, page, size: 25,
    cases_without_requirements_total: 4,
    cases_without_requirements: [
      { id: 'c-1', title: 'Checkout preserves cart', status: 'active' },
      { id: 'c-2', title: '<b>bold</b> case', status: 'approved' },
    ],
  }
}

const onFilterRequirement = vi.fn()
const onOpenCase = vi.fn()

function renderCard(summary: RequirementCoverageSummary = SUMMARY) {
  return render(
    <RequirementCoverageCard projectId="p-1" summary={summary} onFilterRequirement={onFilterRequirement} onOpenCase={onOpenCase} />,
  )
}

beforeEach(() => {
  mutate.mockReset()
  onFilterRequirement.mockReset()
  onOpenCase.mockReset()
  useTraceabilityGaps.mockClear()
  gapsAnswer = { data: gapsPage(1, 40, 25), isLoading: false }
})

describe('RequirementCoverageCard', () => {
  it('states both numbers and the first gaps with their reasons and sources', () => {
    renderCard()
    expect(screen.getByRole('heading', { name: 'Requirement coverage' })).toBeInTheDocument()
    const stat = document.querySelector('[data-requirements-verified]') as HTMLElement
    expect(within(stat).getByText('Requirements verified')).toBeInTheDocument()
    expect(within(stat).getByText('12 of 15')).toBeInTheDocument()
    expect(within(stat).getByText('2 partly verified')).toBeInTheDocument()
    expect(screen.queryByText(/%/)).toBeNull()
    const rows = document.querySelectorAll('[data-requirement-top-gaps] [data-requirement-gap]')
    expect(Array.from(rows).map((row) => row.textContent)).toEqual([
      'REQ-PAY-09 · No case links it.known from a generation batch (claims 3 cases)',
      'req-pay-11 · Only "related" links: no case verifies it.known from a link, a knowledge source',
      'REQ-PAY-12 · Only partly verified.known from a link',
    ])
    // Opening the card asks nothing.
    expect(useTraceabilityGaps).not.toHaveBeenCalled()
  })

  it('filters the list by a gap key', () => {
    renderCard()
    fireEvent.click(screen.getByRole('button', { name: 'req-pay-11' }))
    expect(onFilterRequirement).toHaveBeenCalledWith('REQ-PAY-11')
  })

  it('says when nothing is measured, and when the counts are lower bounds', () => {
    const { rerender } = renderCard({ measured: false, known: 0, verified: 0, partly_verified: 0, gaps: 0, top_gaps: [] })
    expect(screen.getByText(UNMEASURED_TEXT)).toBeInTheDocument()
    expect(screen.queryByText('0 of 0')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Show all gaps' })).toBeNull()
    rerender(<RequirementCoverageCard projectId="p-1" summary={{ ...SUMMARY, truncated: true }} onFilterRequirement={onFilterRequirement} onOpenCase={onOpenCase} />)
    expect(screen.getByRole('note')).toHaveTextContent(TRUNCATED_TEXT)
  })

  it('pages every gap behind Show all gaps, then the cases that link none', () => {
    renderCard()
    fireEvent.click(screen.getByRole('button', { name: 'Show all gaps' }))
    expect(useTraceabilityGaps).toHaveBeenLastCalledWith('p-1', 1, true)
    const all = document.querySelector('[data-requirement-gaps-all]') as HTMLElement
    expect(all.querySelectorAll('[data-requirement-gap]')).toHaveLength(25)
    expect(within(all).getByText('1-25 of 40')).toBeInTheDocument()
    expect(within(all).getByRole('button', { name: 'Previous' })).toBeDisabled()
    gapsAnswer = { data: gapsPage(2, 40, 15), isLoading: false }
    fireEvent.click(within(all).getByRole('button', { name: 'Next' }))
    expect(useTraceabilityGaps).toHaveBeenLastCalledWith('p-1', 2, true)
    expect(screen.getByText('26-40 of 40')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()

    expect(screen.getByText('4 approved or active cases link no requirement')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Show' }))
    expect(screen.getByText('<b>bold</b> case')).toBeInTheDocument()
    expect(document.querySelector('[data-requirement-gaps-all] b')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Checkout preserves cart' }))
    expect(onOpenCase).toHaveBeenCalledWith('c-1')
  })

  it('says when the gaps could not be read, with Retry', () => {
    gapsAnswer = { data: undefined, error: new Error('500'), isLoading: false }
    renderCard()
    fireEvent.click(screen.getByRole('button', { name: 'Show all gaps' }))
    expect(screen.getByRole('alert')).toHaveTextContent(GAPS_ERROR)
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(mutate).toHaveBeenCalledTimes(1)
  })
})
