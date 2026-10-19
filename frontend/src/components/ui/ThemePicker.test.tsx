/**
 * Multi-theme picker + store: seven named themes, each a `data-theme` block of
 * CSS tokens in src/index.css.
 *
 * Locks in: the registry has the seven themes, the picker lists them all and
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
  it('registry has the seven expected themes with unique ids (Paper right after Lab)', () => {
    expect(THEMES.map((t) => t.id)).toEqual([
      'signal', 'console', 'slate', 'ember', 'lab', 'paper', 'midnight',
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

  // W29a UIX-01: Paper, the soft light theme, beside Lab.
  it('choosing Paper sets data-theme="paper", persists it, and names it with its hint', () => {
    render(<ThemePicker />)
    const paper = screen.getByRole('option', { name: 'Paper' })
    expect(paper).toHaveAttribute('title', 'Paper: Soft light · low glare')
    fireEvent.click(paper)
    expect(document.documentElement.getAttribute('data-theme')).toBe('paper')
    expect(useThemeStore.getState().theme).toBe('paper')
    expect(JSON.parse(localStorage.getItem('testlookup-theme') ?? '{}').state?.theme).toBe('paper')
    expect(screen.getByText('Paper')).toBeInTheDocument()
  })

  it('fits seven chips in the account menu: the swatch row is gap-1', () => {
    render(<ThemePicker />)
    const row = screen.getByRole('listbox', { name: 'Color theme' })
    expect(row).toHaveClass('flex', 'gap-1')
    expect(row).not.toHaveClass('gap-1.5')
    expect(screen.getAllByRole('option').map((o) => o.getAttribute('aria-label'))).toEqual(THEMES.map((t) => t.label))
  })
})
