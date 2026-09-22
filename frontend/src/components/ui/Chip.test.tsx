/**
 * VIZ-109: chips name what they remove, collapse past a limit, and removing
 * one never drops keyboard focus to <body>.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { useRef, useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import ChipList, { CHIP_VALUE_MAX_CHARS, Chip, type ChipItem } from './Chip'

describe('Chip', () => {
  it('shows label and value, and names its remove button after both', () => {
    const onRemove = vi.fn()
    render(<Chip label="Release" value="R2" onRemove={onRemove} />)
    expect(screen.getByText('Release:')).toBeInTheDocument()
    expect(screen.getByText('R2')).toBeInTheDocument()
    const remove = screen.getByRole('button', { name: 'Remove filter Release R2' })
    fireEvent.click(remove)
    expect(onRemove).toHaveBeenCalledTimes(1)
  })

  it('has a 24 px remove target', () => {
    render(<Chip label="Suite" value="checkout" onRemove={() => {}} />)
    const cls = screen.getByRole('button').className
    expect(cls).toMatch(/\bh-6\b/)
    expect(cls).toMatch(/\bw-6\b/)
    expect(cls).toMatch(/\bmin-h-6\b/)
    expect(cls).toMatch(/\bmin-w-6\b/)
  })

  it('is read-only without onRemove, and takes a custom remove label', () => {
    const { rerender } = render(<Chip label="Suite" value="checkout" />)
    expect(screen.queryByRole('button')).toBeNull()
    rerender(<Chip label="Suite" value="checkout" onRemove={() => {}} removeLabel="Clear suite" />)
    expect(screen.getByRole('button', { name: 'Clear suite' })).toBeInTheDocument()
  })

  it('shows a warning icon with an accessible name', () => {
    render(<Chip label="Release" value="R9" warning="No runs in this window" />)
    expect(screen.getByRole('img', { name: 'Warning: No runs in this window' })).toBeInTheDocument()
  })

  it('renders markup in a value as text and middle-truncates long values', () => {
    const { rerender } = render(<Chip label="Suite" value={'<img src=x onerror="alert(1)">'} />)
    expect(document.querySelector('img')).toBeNull()
    expect(screen.getByText('<img src=x onerror="alert(1)">')).toBeInTheDocument()

    const long = `start-${'x'.repeat(200)}-end`
    rerender(<Chip label="Suite" value={long} />)
    const shown = screen.getByTitle(long)
    expect(Array.from(shown.textContent ?? '')).toHaveLength(CHIP_VALUE_MAX_CHARS)
    expect(shown.textContent?.endsWith('-end')).toBe(true)
  })
})

const ITEMS: ChipItem[] = [
  { id: 'release:r1', label: 'Release', value: 'R1' },
  { id: 'release:r2', label: 'Release', value: 'R2' },
  { id: 'suite:checkout', label: 'Suite', value: 'checkout', warning: 'Renamed last week' },
  { id: 'suite:payments', label: 'Suite', value: 'payments' },
]

function Harness({ initial = ITEMS, limit, refuse = false }: { initial?: ChipItem[]; limit?: number; refuse?: boolean }) {
  const [items, setItems] = useState(initial)
  const fallback = useRef<HTMLButtonElement>(null)
  return (
    <>
      <button ref={fallback} type="button">
        Filters
      </button>
      <ChipList
        items={items}
        limit={limit}
        fallbackFocusRef={fallback}
        onRemove={(id) => {
          if (!refuse) setItems((prev) => prev.filter((item) => item.id !== id))
        }}
      />
    </>
  )
}

const removeButton = (name: string) => screen.getByRole('button', { name: `Remove filter ${name}` })

describe('ChipList', () => {
  it('is a named list of chips', () => {
    render(<Harness />)
    const list = screen.getByRole('list', { name: 'Active filters' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(4)
  })

  it('removing a chip moves focus to the next chip', () => {
    render(<Harness />)
    removeButton('Release R2').focus()
    fireEvent.click(removeButton('Release R2'))
    expect(screen.queryByRole('button', { name: 'Remove filter Release R2' })).toBeNull()
    expect(removeButton('Suite checkout')).toHaveFocus()
  })

  it('removing the last chip in the row moves focus to the previous one', () => {
    render(<Harness />)
    fireEvent.click(removeButton('Suite payments'))
    expect(removeButton('Suite checkout')).toHaveFocus()
  })

  it('removing the only chip moves focus to the fallback', () => {
    render(<Harness initial={[ITEMS[0]]} />)
    fireEvent.click(removeButton('Release R1'))
    expect(screen.queryByRole('list')).toBeNull()
    expect(screen.getByRole('button', { name: 'Filters' })).toHaveFocus()
  })

  it('leaves focus alone when the parent keeps the chip', () => {
    render(<Harness refuse />)
    const button = removeButton('Release R1')
    button.focus()
    fireEvent.click(button)
    expect(button).toHaveFocus()
  })

  it('collapses past the limit into "+N more" and expands', () => {
    render(<Harness limit={2} />)
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
    const more = screen.getByRole('button', { name: '+2 more' })
    expect(more).toHaveAttribute('aria-expanded', 'false')
    expect(more.className).toMatch(/\bmin-h-6\b/)
    fireEvent.click(more)
    expect(screen.getAllByRole('listitem')).toHaveLength(4)
    expect(screen.getByRole('button', { name: 'Show fewer' })).toHaveAttribute('aria-expanded', 'true')
  })

  it('a chip hidden behind "+N more" slides in and takes focus when its neighbour goes', () => {
    render(<Harness limit={2} />)
    fireEvent.click(removeButton('Release R2'))
    expect(removeButton('Suite checkout')).toHaveFocus()
    expect(screen.getByRole('button', { name: '+1 more' })).toBeInTheDocument()
  })

  it('keeps the focus request across a re-render that still holds the removed chip (review fix 12)', () => {
    // A parent that re-renders with a NEW array before it actually drops the
    // chip (an optimistic copy, a store round-trip): the first render must not
    // use up the focus request.
    function Deferred() {
      const [items, setItems] = useState(ITEMS)
      const [removing, setRemoving] = useState<string | null>(null)
      return (
        <>
          <button type="button" onClick={() => setItems((prev) => prev.filter((item) => item.id !== removing))}>
            Commit
          </button>
          <ChipList
            items={items}
            onRemove={(id) => {
              setRemoving(id)
              setItems((prev) => [...prev])
            }}
          />
        </>
      )
    }
    render(<Deferred />)
    removeButton('Release R2').focus()
    fireEvent.click(removeButton('Release R2'))
    expect(removeButton('Release R2')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Commit' }))
    expect(screen.queryByRole('button', { name: 'Remove filter Release R2' })).toBeNull()
    expect(removeButton('Suite checkout')).toHaveFocus()
  })

  it('renders nothing for no items', () => {
    const { container } = render(<ChipList items={[]} onRemove={() => {}} />)
    expect(container).toBeEmptyDOMElement()
  })
})
