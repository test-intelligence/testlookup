/**
 * The ONE table (W29b UIX-06): `Table`, `Th`, `ThSort`, `Td`, `Tr` only apply the `index.css` classes
 * (`src/test/uiStandardCss.test.ts` pins the classes themselves).
 */
import { createRef, type ReactNode } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Table, Td, Th, ThSort, Tr } from './Table'
import type { SortDir } from '@/hooks/useTableSort'

const classes = (el: Element | null) => (el?.getAttribute('class') ?? '').split(/\s+/).filter(Boolean)

function inHead(cells: ReactNode) {
  return render(
    <Table>
      <thead>
        <tr>{cells}</tr>
      </thead>
    </Table>,
  )
}

describe('Table', () => {
  it('sets data-density (default comfortable) and the base classes beside the caller\'s', () => {
    const { container, rerender } = render(<Table aria-label="Runs" className="min-w-[640px]" />)
    const table = container.querySelector('table') as HTMLTableElement
    expect(table).toHaveAttribute('data-density', 'comfortable')
    expect(table).toHaveAttribute('aria-label', 'Runs')
    expect(classes(table)).toEqual(expect.arrayContaining(['w-full', 'border-collapse', 'text-sm', 'min-w-[640px]']))
    rerender(<Table density="dense" />)
    expect(container.querySelector('table')).toHaveAttribute('data-density', 'dense')
    rerender(<Table density="compact" />)
    expect(container.querySelector('table')).toHaveAttribute('data-density', 'compact')
  })
})

describe('Th', () => {
  it('is a column header with the .th class; numeric adds .th-num; scope can be a colgroup', () => {
    inHead(
      <>
        <Th>Name</Th>
        <Th numeric className="w-20">Failures</Th>
        <Th scope="colgroup" colSpan={2}>Timing</Th>
      </>,
    )
    const [name, failures, timing] = screen.getAllByRole('columnheader')
    expect(name).toHaveAttribute('scope', 'col')
    expect(classes(name)).toEqual(['th'])
    expect(classes(failures)).toEqual(['th', 'th-num', 'w-20'])
    expect(timing).toHaveAttribute('scope', 'colgroup')
    expect(timing).toHaveAttribute('colspan', '2')
  })
})

describe('ThSort', () => {
  function renderSort(props: { currentKey: string | null; dir: SortDir; onSort?: (key: string) => void; numeric?: boolean; title?: string; align?: 'left' | 'right' | 'center' }) {
    const onSort = props.onSort ?? vi.fn()
    const view = inHead(<ThSort label="Severity" sortKey="severity" {...props} onSort={onSort} />)
    const th = screen.getByRole('columnheader')
    const button = screen.getByRole('button', { name: 'Severity' })
    return { ...view, onSort, th, button }
  }

  it('is a <button class="th-sort"> inside the th, and clicking it sorts by its key', () => {
    const { th, button, onSort } = renderSort({ currentKey: null, dir: 'asc' })
    expect(th).toContainElement(button)
    expect(button).toHaveAttribute('type', 'button')
    expect(classes(button)).toContain('th-sort')
    expect(classes(th)).toContain('th')
    expect(th).toHaveAttribute('scope', 'col')
    fireEvent.click(button)
    expect(onSort).toHaveBeenCalledWith('severity')
  })

  it('sets aria-sort none / ascending / descending', () => {
    expect(renderSort({ currentKey: 'age', dir: 'asc' }).th).toHaveAttribute('aria-sort', 'none')
  })

  it.each([
    ['asc', 'ascending'],
    ['desc', 'descending'],
  ] as const)('an active %s sort is aria-sort="%s"', (dir, aria) => {
    expect(renderSort({ currentKey: 'severity', dir }).th).toHaveAttribute('aria-sort', aria)
  })

  it('draws the three lucide chevrons (inactive faint up-down, active up or down), never a text arrow', () => {
    const icon = (container: HTMLElement) => container.querySelector('button svg') as SVGElement
    let view = renderSort({ currentKey: null, dir: 'asc' })
    expect(icon(view.container)).toHaveAttribute('data-sort-icon', 'none')
    expect(icon(view.container).getAttribute('class')).toMatch(/lucide-chevrons-up-down/)
    expect(icon(view.container).getAttribute('class')).toContain('opacity-30')
    expect(icon(view.container)).toHaveAttribute('aria-hidden', 'true')
    view.unmount()
    view = renderSort({ currentKey: 'severity', dir: 'asc' })
    expect(icon(view.container)).toHaveAttribute('data-sort-icon', 'asc')
    expect(icon(view.container).getAttribute('class')).toMatch(/lucide-chevron-up\b/)
    expect(icon(view.container).getAttribute('class')).not.toContain('opacity-30')
    view.unmount()
    view = renderSort({ currentKey: 'severity', dir: 'desc' })
    expect(icon(view.container)).toHaveAttribute('data-sort-icon', 'desc')
    expect(icon(view.container).getAttribute('class')).toMatch(/lucide-chevron-down\b/)
    // The label's text is exactly the label: no ↕ ↑ ↓ character anywhere in the header.
    expect(view.button.textContent).toBe('Severity')
    expect(view.th.textContent).not.toMatch(/[↕↑↓▲▼]/)
  })

  it('numeric adds .th-num (right-aligned by CSS); title is the header\'s; align sets the button\'s justify', () => {
    const { th, button } = renderSort({ currentKey: null, dir: 'asc', numeric: true, title: 'Days since filed' })
    expect(classes(th)).toEqual(expect.arrayContaining(['th', 'th-num']))
    expect(th).toHaveAttribute('title', 'Days since filed')
    expect(classes(button)).toEqual(['th-sort'])
  })

  it.each([
    ['left', 'justify-start'],
    ['right', 'justify-end'],
    ['center', 'justify-center'],
  ] as const)('align %s puts %s on the button', (align, justify) => {
    expect(classes(renderSort({ currentKey: null, dir: 'asc', align }).button)).toEqual(['th-sort', justify])
  })
})

describe('Td and Tr', () => {
  function renderBody(row: ReactNode) {
    return render(
      <Table>
        <tbody>{row}</tbody>
      </Table>,
    )
  }

  it('Td has .td; numeric adds .td-num', () => {
    renderBody(
      <tr>
        <Td>run-12</Td>
        <Td numeric className="font-mono">42</Td>
      </tr>,
    )
    expect(classes(screen.getByText('run-12'))).toEqual(['td'])
    expect(classes(screen.getByText('42'))).toEqual(['td', 'td-num', 'font-mono'])
  })

  it('Tr is a .table-row; selected sets data-selected; interactive={false} adds cursor-default; ref reaches the <tr>', () => {
    const ref = createRef<HTMLTableRowElement>()
    const { container, rerender } = render(
      <Table>
        <tbody>
          <Tr ref={ref} data-defect-row="d1" selected data-highlighted="true">
            <Td>A</Td>
          </Tr>
        </tbody>
      </Table>,
    )
    const row = container.querySelector('tr') as HTMLTableRowElement
    expect(ref.current).toBe(row)
    expect(classes(row)).toEqual(['table-row'])
    expect(row).toHaveAttribute('data-selected', '')
    expect(row).toHaveAttribute('data-highlighted', 'true')
    expect(row).toHaveAttribute('data-defect-row', 'd1')
    rerender(
      <Table>
        <tbody>
          <Tr interactive={false}>
            <Td>A</Td>
          </Tr>
        </tbody>
      </Table>,
    )
    expect(container.querySelector('tr')).not.toHaveAttribute('data-selected')
    expect(classes(container.querySelector('tr'))).toEqual(['table-row', 'cursor-default'])
  })
})
