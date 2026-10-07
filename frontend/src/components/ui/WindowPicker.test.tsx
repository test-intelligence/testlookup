import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
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

  it('snaps a window the page does not offer to its nearest option, in the store too', () => {
    useTimeWindowStore.setState({ days: 14 })
    render(<WindowPicker options={[7, 30, 90, 365]} />)
    expect(screen.getByRole('radio', { name: '7d' })).toHaveAttribute('aria-checked', 'true')
    expect(useTimeWindowStore.getState().days).toBe(7)
    expect(screen.getByRole('radio', { name: '365d' })).toBeInTheDocument()
  })
})
