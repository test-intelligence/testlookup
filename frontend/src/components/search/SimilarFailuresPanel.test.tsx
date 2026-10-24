/**
 * W12 SRCH-01 (contract §2.1, test 11): the Similar failures tab's panel —
 * five links of the same project, each `semantic_status` empty state, and the
 * HTTP error line. Through the real hook over a mocked service.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SimilarFailuresPanel from './SimilarFailuresPanel'
import type { SearchResult } from '@/types/search'

const mockFindSimilar = vi.fn()
vi.mock('@/services/searchService', () => ({
  searchService: { findSimilar: (...args: unknown[]) => mockFindSimilar(...args) },
}))

function item(n: number, overrides: Partial<SearchResult> = {}): SearchResult {
  return {
    test_case_id: `tc-${n}`, test_run_id: `run-${n}`, test_name: `checkout fails ${n}`, suite_name: 'Checkout',
    status: n % 2 ? 'FAILED' : 'BROKEN', failure_count: 1, last_run_date: new Date().toISOString(), relevance_score: 0.9 - n / 100,
    ...overrides,
  }
}

function renderPanel() {
  return render(
    <SWRConfig value={{ provider: () => new Map() }}>
      <MemoryRouter><SimilarFailuresPanel testId="src-1" /></MemoryRouter>
    </SWRConfig>,
  )
}

// Braces: a function returned from beforeEach runs as its teardown.
beforeEach(() => { mockFindSimilar.mockReset() })

describe('SimilarFailuresPanel', () => {
  it('asks for five, and lists each as a link with suite · status · relevance · last run', async () => {
    mockFindSimilar.mockResolvedValue({ items: [1, 2, 3, 4, 5].map((n) => item(n)), total: 5, query: 'x', semantic_status: 'ok' })
    renderPanel()
    const links = await screen.findAllByRole('link')
    expect(links).toHaveLength(5)
    expect(mockFindSimilar).toHaveBeenCalledTimes(1)
    expect(mockFindSimilar).toHaveBeenCalledWith('src-1', 5)
    expect(links[0]).toHaveTextContent('checkout fails 1')
    expect(links[0]).toHaveAttribute('href', '/runs/run-1/tests/tc-1')
    expect(screen.getByText('Checkout · FAILED · rel 0.89 · 0s ago')).toBeInTheDocument()
    expect(screen.getByText('Checkout · BROKEN · rel 0.88 · 0s ago')).toBeInTheDocument()
  })

  it.each([
    ['ok', "No similar failures in this project's semantic index."],
    [undefined, "No similar failures in this project's semantic index."],
    ['not_configured', 'Similar failures need the semantic index: no ChromaDB server answers here.'],
    ['unavailable', 'The semantic index answered with an error. Try again later.'],
  ])('an empty answer with semantic_status %s says why', async (status, sentence) => {
    mockFindSimilar.mockResolvedValue({ items: [], total: 0, query: 'x', ...(status ? { semantic_status: status } : {}) })
    renderPanel()
    expect(await screen.findByText(sentence)).toBeInTheDocument()
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('a refused request: "Could not load similar failures."', async () => {
    mockFindSimilar.mockRejectedValue(new Error('403'))
    renderPanel()
    expect(await screen.findByText('Could not load similar failures.')).toBeInTheDocument()
  })

  it('names are text, never markup', async () => {
    mockFindSimilar.mockResolvedValue({ items: [item(1, { test_name: '<img src=x onerror=alert(1)>', suite_name: undefined })], total: 1, query: 'x' })
    renderPanel()
    const link = await screen.findByRole('link')
    expect(link.querySelector('img')).toBeNull()
    expect(link).toHaveTextContent('<img src=x onerror=alert(1)>')
    expect(screen.getByText(/^No suite · FAILED/)).toBeInTheDocument()
  })
})
