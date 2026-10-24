import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import SuggestedSearchesCard, { SUGGESTED_LOAD_ERROR } from './SuggestedSearchesCard'
import type { SearchSuggestions } from '@/types/search'

const DATA: SearchSuggestions = {
  project_id: 'p1',
  window_days: 7,
  failing_tests: [
    { kind: 'failing_test', label: 'checkout total includes tax', q: '"checkout total includes tax"', scope: 'tests', detail: '12 failures in 7 days' },
  ],
  open_defects: [{ kind: 'open_defect', label: 'PAY-412', q: 'PAY-412', scope: 'defects', detail: 'Open · HIGH' }],
  active_release: { kind: 'active_release', label: '2026.11', q: 'release:"2026.11" status:failed', scope: 'tests', detail: 'Active release' },
}

describe('SuggestedSearchesCard (W12 SRCH-03)', () => {
  it('three groups; a row runs its query', () => {
    const onPick = vi.fn()
    render(<SuggestedSearchesCard suggestions={DATA} failed={false} onPick={onPick} />)
    expect(screen.getByRole('heading', { name: 'Suggested' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Failing in the last 7 days' })).toHaveTextContent('12 failures in 7 days')
    expect(within(screen.getByRole('region', { name: 'Open defects' })).getByText('PAY-412')).toBeInTheDocument()
    fireEvent.click(within(screen.getByRole('region', { name: 'Active release' })).getByRole('button', { name: /Failures in 2026\.11/ }))
    expect(onPick).toHaveBeenCalledWith(DATA.active_release)
  })

  it('a group with nothing is left out; all empty, no card', () => {
    const { container, rerender } = render(
      <SuggestedSearchesCard suggestions={{ ...DATA, open_defects: [], active_release: null }} failed={false} onPick={vi.fn()} />,
    )
    expect(screen.queryByRole('region', { name: 'Open defects' })).toBeNull()
    expect(screen.queryByRole('region', { name: 'Active release' })).toBeNull()
    rerender(<SuggestedSearchesCard suggestions={{ ...DATA, failing_tests: [], open_defects: [], active_release: null }} failed={false} onPick={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('a refused read says it could not load; labels are text', () => {
    const { rerender } = render(<SuggestedSearchesCard suggestions={undefined} failed onPick={vi.fn()} />)
    expect(screen.getByText(SUGGESTED_LOAD_ERROR)).toBeInTheDocument()
    rerender(
      <SuggestedSearchesCard
        suggestions={{ ...DATA, failing_tests: [{ ...DATA.failing_tests[0], label: '<img src=x onerror=alert(1)>' }] }}
        failed={false}
        onPick={vi.fn()}
      />,
    )
    expect(document.querySelector('img')).toBeNull()
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument()
  })
})
