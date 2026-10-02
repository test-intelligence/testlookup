/**
 * VIZ-106: the presentation-mode switch is one toggle button with a constant
 * name and an `aria-pressed` state, its state also worded for sighted users.
 */
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { usePresentationStore } from '@/store/presentationStore'
import PresentationToggle from './PresentationToggle'

afterEach(() => {
  act(() => usePresentationStore.getState().setEnabled(false))
  localStorage.clear()
})

describe('PresentationToggle', () => {
  it('is a toggle button named "Presentation mode", off until pressed', () => {
    render(<PresentationToggle />)
    const button = screen.getByRole('button', { name: 'Presentation mode' })
    expect(button).toHaveAttribute('aria-pressed', 'false')
    expect(button).toHaveAttribute('type', 'button')
    expect(button).toHaveTextContent('Off')
  })

  it('pressing it turns the mode on (store, <html> attribute) and again turns it off; the name never changes', () => {
    render(<PresentationToggle />)
    const button = screen.getByRole('button', { name: 'Presentation mode' })

    fireEvent.click(button)
    expect(usePresentationStore.getState().enabled).toBe(true)
    expect(document.documentElement.getAttribute('data-presentation')).toBe('on')
    expect(screen.getByRole('button', { name: 'Presentation mode' })).toHaveAttribute('aria-pressed', 'true')
    expect(button).toHaveTextContent('On')
    expect(button).toHaveClass('active')

    fireEvent.click(button)
    expect(usePresentationStore.getState().enabled).toBe(false)
    expect(document.documentElement.hasAttribute('data-presentation')).toBe(false)
    expect(button).toHaveAttribute('aria-pressed', 'false')
    expect(button).not.toHaveClass('active')
  })

  it('reflects a mode turned on elsewhere (another tab of the store, a test seam)', () => {
    render(<PresentationToggle />)
    act(() => usePresentationStore.getState().setEnabled(true))
    expect(screen.getByRole('button', { name: 'Presentation mode' })).toHaveAttribute('aria-pressed', 'true')
  })
})
