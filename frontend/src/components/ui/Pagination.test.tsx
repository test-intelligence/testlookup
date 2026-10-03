import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import Pagination from './Pagination'

// The previous/next controls are icon-only chevrons. Without an accessible
// name a screen reader announces them as "button" and axe flags
// `button-name`; these tests find each control by the name it announces, so
// removing either label fails them.
const previous = () => screen.getByRole('button', { name: /^previous page$/i })
const next = () => screen.getByRole('button', { name: /^next page$/i })

describe('Pagination', () => {
  it('does not render when there is a single page', () => {
    const { container } = render(<Pagination page={1} pages={1} total={10} onChange={vi.fn()} />)
    expect(container.firstChild).toBeNull()
  })

  it('shows totals and current page text', () => {
    render(<Pagination page={2} pages={5} total={42} onChange={vi.fn()} />)

    expect(screen.getByText('42 total results')).toBeInTheDocument()
    expect(screen.getByText('Page 2 of 5')).toBeInTheDocument()
  })

  it('names both icon-only buttons', () => {
    render(<Pagination page={2} pages={5} total={42} onChange={vi.fn()} />)

    expect(previous()).toHaveAccessibleName('Previous page')
    expect(next()).toHaveAccessibleName('Next page')
    // Every button the pager renders has a name: none announces as a bare "button".
    const buttons = screen.getAllByRole('button')
    expect(buttons).toHaveLength(2)
    for (const button of buttons) {
      expect(button).not.toHaveAccessibleName('')
    }
  })

  it('keeps the names on disabled buttons', () => {
    const { rerender } = render(<Pagination page={1} pages={3} total={20} onChange={vi.fn()} />)
    expect(previous()).toBeDisabled()
    expect(next()).toBeEnabled()

    rerender(<Pagination page={3} pages={3} total={20} onChange={vi.fn()} />)
    expect(previous()).toBeEnabled()
    expect(next()).toBeDisabled()
  })

  it('calls onChange with previous and next pages', () => {
    const onChange = vi.fn()
    render(<Pagination page={3} pages={5} total={100} onChange={onChange} />)

    fireEvent.click(previous())
    fireEvent.click(next())

    expect(onChange).toHaveBeenNthCalledWith(1, 2)
    expect(onChange).toHaveBeenNthCalledWith(2, 4)
  })
})
