import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import SearchChipsRow from './SearchChipsRow'
import type { SearchParsed } from '@/types/search'

const PARSED: SearchParsed = {
  text: 'checkout',
  filters: [
    { key: 'status', value: 'failed', raw: 'status:failed', start: 0, end: 13 },
    { key: 'suite', value: 'API Regression', raw: 'suite:"API Regression"', start: 14, end: 36 },
  ],
  ignored_tokens: [{ token: 'since:abc', key: 'since', reason: 'invalid_value', message: 'since: takes 7d, 24h or a date (YYYY-MM-DD) within the last 365 days', start: 46, end: 55 }],
  unsupported_by: { defect: ['status', 'suite'] },
  searched_entity_types: ['test_case'],
  notes: [
    'since: takes 7d, 24h or a date (YYYY-MM-DD) within the last 365 days',
    'status: and suite: are not supported for Defects, so no defects were searched.',
  ],
}

describe('SearchChipsRow (W12 SRCH-04)', () => {
  it('a chip per applied filter, its × reports that filter', () => {
    const onRemove = vi.fn()
    render(<SearchChipsRow parsed={PARSED} onRemove={onRemove} />)
    expect(screen.getByText('Filters')).toHaveClass('eyebrow')
    expect(screen.getByText('status: failed')).toBeInTheDocument()
    const suite = document.querySelector('[data-search-chip="suite"]') as HTMLElement
    expect(suite).toHaveTextContent('suite: API Regression')
    expect(suite.className).toContain('max-w-[260px]')
    fireEvent.click(within(suite).getByRole('button', { name: 'Remove suite: API Regression' }))
    expect(onRemove).toHaveBeenCalledWith(PARSED.filters[1])
  })

  it('the notes verbatim: ignored in muted ink, unsupported in warning ink', () => {
    render(<SearchChipsRow parsed={PARSED} onRemove={vi.fn()} />)
    const items = screen.getAllByRole('listitem').filter((li) => li.closest('[data-search-notes]'))
    expect(items.map((li) => li.textContent)).toEqual(PARSED.notes)
    expect(items[0].className).toContain('--color-text-muted')
    expect(items[1].className).toContain('--ui-warning-ink')
  })

  it('notes alone still show the row (no Filters label), with the Syntax help', () => {
    render(<SearchChipsRow parsed={{ ...PARSED, filters: [] }} onRemove={vi.fn()} />)
    expect(screen.queryByText('Filters')).toBeNull()
    expect(screen.getByRole('button', { name: /Syntax/ })).toHaveAttribute('aria-expanded', 'false')
  })

  it('no filter and no note (or no parsed block): no row at all', () => {
    const { container, rerender } = render(<SearchChipsRow parsed={{ ...PARSED, filters: [], notes: [] }} onRemove={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
    rerender(<SearchChipsRow parsed={undefined} onRemove={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('the Syntax help opens in the page with the eight keys and what each applies to', () => {
    render(<SearchChipsRow parsed={PARSED} onRemove={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /Syntax/ }))
    const rows = Array.from(document.querySelectorAll('[data-syntax-key]'))
    expect(rows).toHaveLength(8)
    expect(within(rows[0] as HTMLElement).getByText('Tests, Runs')).toBeInTheDocument()
    expect(document.querySelector('[role="dialog"]')).toBeNull()
  })
})
