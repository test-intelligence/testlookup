import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import Pagination from './Pagination'

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

  it('disables prev button on first page and next button on last page', () => {
    const { rerender } = render(<Pagination page={1} pages={3} total={20} onChange={vi.fn()} />)
    let buttons = screen.getAllByRole('button')
    expect(buttons[0]).toBeDisabled()
    expect(buttons[1]).not.toBeDisabled()

    rerender(<Pagination page={3} pages={3} total={20} onChange={vi.fn()} />)
    buttons = screen.getAllByRole('button')
    expect(buttons[0]).not.toBeDisabled()
    expect(buttons[1]).toBeDisabled()
  })

  it('calls onChange with previous and next pages', () => {
    const onChange = vi.fn()
    render(<Pagination page={3} pages={5} total={100} onChange={onChange} />)

    const [prev, next] = screen.getAllByRole('button')
    fireEvent.click(prev)
    fireEvent.click(next)

    expect(onChange).toHaveBeenNthCalledWith(1, 2)
    expect(onChange).toHaveBeenNthCalledWith(2, 4)
  })
})
