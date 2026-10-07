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

  it('names the active theme and shows one swatch per theme, the active one selected', () => {
    render(<ThemePicker />)
    expect(screen.getByText('Signal')).toBeInTheDocument()

    const options = screen.getAllByRole('option')
    expect(options).toHaveLength(THEMES.length)
    expect(screen.getByRole('option', { name: 'Signal' })).toHaveAttribute('aria-selected', 'true')
  })

  it('selecting a theme applies data-theme on <html> and persists in the store', () => {
    render(<ThemePicker />)
    fireEvent.click(screen.getByRole('option', { name: 'Console' }))

    expect(document.documentElement.getAttribute('data-theme')).toBe('console')
    expect(useThemeStore.getState().theme).toBe('console')
    expect(screen.getByRole('option', { name: 'Console' })).toHaveAttribute('aria-selected', 'true')
  })
})
