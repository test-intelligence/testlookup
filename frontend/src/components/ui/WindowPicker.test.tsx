import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useTimeWindowStore } from '@/store/timeWindowStore'
import WindowPicker from './WindowPicker'

beforeEach(() => useTimeWindowStore.setState({ days: 30 }))

describe('WindowPicker', () => {
  it('shows the global window as checked and writes a choice back to the store', () => {
    render(<WindowPicker />)
    expect(screen.getByRole('radio', { name: '30d' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('radio', { name: '24h' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: '7d' }))
    expect(useTimeWindowStore.getState().days).toBe(7)
    expect(screen.getByRole('radio', { name: '7d' })).toHaveAttribute('aria-checked', 'true')
  })

  // D-18: the snapped value was written back on mount, so opening a page with other options changed the
  // window of every other page. It is shown snapped; the store keeps the reader's choice.
  it('shows a window the page does not offer as its nearest option, and leaves the store alone', () => {
    useTimeWindowStore.setState({ days: 14 })
    render(<WindowPicker options={[7, 30, 90, 365]} />)
    expect(screen.getByRole('radio', { name: '7d' })).toHaveAttribute('aria-checked', 'true')
    expect(useTimeWindowStore.getState().days).toBe(14)
    expect(screen.getByRole('radio', { name: '365d' })).toBeInTheDocument()
  })

  it('stored 7 against options [14, 30, 90]: 14 is selected and the store still holds 7', () => {
    useTimeWindowStore.setState({ days: 7 })
    render(<WindowPicker options={[14, 30, 90]} />)
    expect(screen.getByRole('radio', { name: '14d' })).toHaveAttribute('aria-checked', 'true')
    expect(useTimeWindowStore.getState().days).toBe(7)
  })

  it('a page-held value is shown without touching the store; a click goes to onChange', () => {
    const onChange = vi.fn()
    render(<WindowPicker value={14} onChange={onChange} />)
    expect(screen.getByRole('radio', { name: '14d' })).toHaveAttribute('aria-checked', 'true')
    expect(useTimeWindowStore.getState().days).toBe(30)
    fireEvent.click(screen.getByRole('radio', { name: '90d' }))
    expect(onChange).toHaveBeenCalledWith(90)
    expect(useTimeWindowStore.getState().days).toBe(30)
  })
})
