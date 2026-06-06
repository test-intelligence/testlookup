/**
 * Multi-theme picker + store (design_handoff_theming).
 *
 * Locks in: the registry has the six themes, the picker lists them all and
 * switching applies `data-theme` on <html> (which is what swaps every CSS
 * token). Guards against an accidental revert to the old binary toggle.
 */
import { describe, expect, it, beforeEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import ThemePicker from './ThemePicker'
import { THEMES, useThemeStore } from '@/store/themeStore'

beforeEach(() => {
  useThemeStore.getState().setTheme('signal')
})

describe('ThemePicker', () => {
  it('registry has the six expected themes with unique ids', () => {
    expect(THEMES.map((t) => t.id)).toEqual([
      'signal', 'console', 'slate', 'ember', 'lab', 'midnight',
    ])
    expect(new Set(THEMES.map((t) => t.id)).size).toBe(THEMES.length)
  })

  it('shows the active theme label and opens a list of all themes', () => {
    render(<ThemePicker />)
    expect(screen.getByText('Signal')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /signal/i }))
    const options = screen.getAllByRole('option')
    expect(options).toHaveLength(THEMES.length)
  })

  it('selecting a theme applies data-theme on <html> and persists in the store', () => {
    render(<ThemePicker />)
    fireEvent.click(screen.getByRole('button', { name: /signal/i }))
    fireEvent.click(screen.getByRole('option', { name: /Console/ }))

    expect(document.documentElement.getAttribute('data-theme')).toBe('console')
    expect(useThemeStore.getState().theme).toBe('console')
  })
})
