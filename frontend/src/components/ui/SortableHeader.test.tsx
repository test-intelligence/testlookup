import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import SortableHeader from './SortableHeader'
import type { SortDir } from '@/hooks/useTableSort'

// SortableHeader renders a <th>, so it must live inside a table for valid DOM
// nesting and for the columnheader role / aria-sort to resolve.
function renderHeader(props: {
  currentKey?: string
  dir?: SortDir
  onSort?: (key: string) => void
}) {
  const onSort = props.onSort ?? vi.fn()
  render(
    <table>
      <thead>
        <tr>
          <SortableHeader
            label="Test Name"
            sortKey="test_name"
            currentKey={props.currentKey ?? ''}
            dir={props.dir ?? 'asc'}
            onSort={onSort}
          />
        </tr>
      </thead>
    </table>,
  )
  return { onSort }
}

describe('SortableHeader', () => {
  it('exposes a keyboard-operable button so keyboard users can sort', () => {
    // Regression: the header was a bare <th onClick> — mouse-only, unreachable
    // by keyboard. A native <button> restores Enter/Space activation for free.
    const { onSort } = renderHeader({})

    const button = screen.getByRole('button', { name: /test name/i })
    fireEvent.click(button)
    expect(onSort).toHaveBeenCalledWith('test_name')
  })

  it('announces the active sort direction to assistive tech via aria-sort', () => {
    const { rerender } = render(
      <table>
        <thead>
          <tr>
            <SortableHeader label="Test Name" sortKey="test_name" currentKey="test_name" dir="asc" onSort={vi.fn()} />
          </tr>
        </thead>
      </table>,
    )
    expect(screen.getByRole('columnheader')).toHaveAttribute('aria-sort', 'ascending')

    rerender(
      <table>
        <thead>
          <tr>
            <SortableHeader label="Test Name" sortKey="test_name" currentKey="test_name" dir="desc" onSort={vi.fn()} />
          </tr>
        </thead>
      </table>,
    )
    expect(screen.getByRole('columnheader')).toHaveAttribute('aria-sort', 'descending')
  })

  it('reports aria-sort="none" when this column is not the active sort key', () => {
    renderHeader({ currentKey: 'other_key', dir: 'asc' })
    expect(screen.getByRole('columnheader')).toHaveAttribute('aria-sort', 'none')
  })
})
