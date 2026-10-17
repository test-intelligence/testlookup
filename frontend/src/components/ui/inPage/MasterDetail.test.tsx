/**
 * W27 PAN-01: the list and the open item's detail, side by side in the page.
 * Closed, the row is exactly what it was (no class, no style); open, the
 * /test-management split; expanded, the list folds away but stays mounted.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { DETAIL_TRACK, MasterDetail } from './MasterDetail'

const row = () => document.querySelector('[data-master-detail]') as HTMLElement
const listWrapper = () => row().firstElementChild as HTMLElement

function Host({ rail }: { rail?: boolean }) {
  const [open, setOpen] = useState(false)
  const [expanded, setExpanded] = useState(false)
  return (
    <MasterDetail
      list={(
        <>
          <input aria-label="Filter" />
          <button type="button" onClick={() => setOpen(true)}>Open item</button>
        </>
      )}
      detail={open ? (
        <aside aria-label="Item">
          <button type="button" onClick={() => setExpanded((v) => !v)}>Toggle expand</button>
          <button type="button" onClick={() => { setOpen(false); setExpanded(false) }}>Close item</button>
        </aside>
      ) : null}
      expanded={expanded}
      rail={rail ? <div data-testid="rail"><h3>Shipping this week</h3></div> : undefined}
      railTrack="340px"
      gapClassName={rail ? 'gap-[18px]' : undefined}
    />
  )
}

describe('MasterDetail', () => {
  it('closed: no grid class and no style on the row; the list wrapper is min-w-0', () => {
    render(<Host />)
    expect(row()).toHaveAttribute('data-master-detail', 'list')
    expect(row()).not.toHaveAttribute('class')
    expect(row()).not.toHaveAttribute('style')
    expect(listWrapper().className).toBe('min-w-0')
    expect(screen.queryByRole('complementary')).toBeNull()
  })

  it('open: grid gap-4 items-start and exactly the TM track', () => {
    render(<Host />)
    fireEvent.click(screen.getByRole('button', { name: 'Open item' }))
    expect(row()).toHaveAttribute('data-master-detail', 'split')
    expect(row().className).toBe('grid gap-4 items-start')
    expect(row().style.gridTemplateColumns).toBe('minmax(0, 1fr) minmax(440px, 46%)')
    expect(DETAIL_TRACK).toBe('minmax(440px, 46%)')
    expect(screen.getByRole('complementary', { name: 'Item' })).toBeInTheDocument()
    // The detail follows the list.
    expect(row().lastElementChild).toBe(screen.getByRole('complementary', { name: 'Item' }))
  })

  it('expanded: the list wrapper is hidden but stays mounted, its typed value kept', () => {
    render(<Host />)
    fireEvent.change(screen.getByRole('textbox', { name: 'Filter' }), { target: { value: 'checkout' } })
    fireEvent.click(screen.getByRole('button', { name: 'Open item' }))
    fireEvent.click(screen.getByRole('button', { name: 'Toggle expand' }))
    expect(row()).toHaveAttribute('data-master-detail', 'expanded')
    expect(row()).not.toHaveAttribute('class')
    // React leaves an empty style attribute behind once the grid style goes.
    expect(row().getAttribute('style') ?? '').toBe('')
    expect(listWrapper()).toHaveAttribute('hidden')
    const filter = listWrapper().querySelector('input') as HTMLInputElement
    expect(filter.value).toBe('checkout')

    fireEvent.click(screen.getByRole('button', { name: 'Toggle expand' }))
    expect(listWrapper()).not.toHaveAttribute('hidden')
    expect(screen.getByRole('textbox', { name: 'Filter' })).toHaveValue('checkout')
  })

  it('rowProps and listProps land on their elements; a host className is used as is', () => {
    render(
      <MasterDetail
        list={<p>rows</p>}
        detail={null}
        rowProps={{ 'data-cases-layout': 'list' }}
        listProps={{ 'data-primary': '', className: 'rounded-xl overflow-hidden min-w-0', style: { border: '1px solid red' } }}
      />,
    )
    expect(row()).toHaveAttribute('data-cases-layout', 'list')
    expect(listWrapper()).toHaveAttribute('data-primary', '')
    expect(listWrapper().className).toBe('rounded-xl overflow-hidden min-w-0')
    expect(listWrapper().style.border).toBe('1px solid red')
  })

  it('takes a custom detail track', () => {
    render(<MasterDetail list={<p>rows</p>} detail={<aside aria-label="x" />} detailTrack="minmax(480px, 50%)" />)
    expect(row().style.gridTemplateColumns).toBe('minmax(0, 1fr) minmax(480px, 50%)')
  })

  it('with a rail: closed renders it in its track; open folds it and the detail takes its column', () => {
    render(<Host rail />)
    expect(row().className).toBe('grid gap-[18px]')
    expect(row().style.gridTemplateColumns).toBe('minmax(0, 1fr) 340px')
    expect(screen.getByTestId('rail')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Open item' }))
    expect(screen.queryByTestId('rail')).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Shipping this week' })).toBeNull()
    expect(row().className).toBe('grid gap-[18px] items-start')
    expect(row().style.gridTemplateColumns).toBe('minmax(0, 1fr) minmax(440px, 46%)')

    fireEvent.click(screen.getByRole('button', { name: 'Close item' }))
    expect(screen.getByTestId('rail')).toBeInTheDocument()
  })
})
