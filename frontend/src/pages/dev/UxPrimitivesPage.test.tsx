/**
 * `/__ux-primitives` (dev only): every section the visual spec shoots renders
 * once, in order — the six UX-redesign primitives and the W29a standard's
 * three (`buttons`, `badges`, `states`; contract §2.8) — and each of the new
 * three shows what it is the reading surface for.
 */
import { render, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import UxPrimitivesPage from './UxPrimitivesPage'

const SECTIONS = [
  'page-header', 'route-tabs', 'status-banner', 'kpi-strip', 'window-picker', 'disclosure',
  'buttons', 'badges', 'states',
]

function renderAt(search = '') {
  return render(
    <MemoryRouter initialEntries={[`/__ux-primitives${search}`]}>
      <UxPrimitivesPage />
    </MemoryRouter>,
  )
}

const sectionOf = (container: HTMLElement, id: string) => container.querySelector(`[data-ux-primitive="${id}"]`) as HTMLElement

describe('UxPrimitivesPage', () => {
  afterEach(() => {
    document.documentElement.removeAttribute('data-theme')
  })

  it('renders the nine sections once each, in order, each named by its heading', () => {
    const { container } = renderAt()
    const ids = Array.from(container.querySelectorAll('[data-ux-primitive]')).map((n) => n.getAttribute('data-ux-primitive'))
    expect(ids).toEqual(SECTIONS)
    for (const id of SECTIONS) {
      expect(within(sectionOf(container, id)).getByRole('heading', { level: 2 })).toBeInTheDocument()
    }
  })

  it('buttons: the four variants at md and sm, one disabled, every one an 8 px primitive', () => {
    const { container } = renderAt()
    const section = sectionOf(container, 'buttons')
    const md = section.querySelector('[data-button-size="md"]') as HTMLElement
    const sm = section.querySelector('[data-button-size="sm"]') as HTMLElement
    for (const row of [md, sm]) {
      for (const variant of ['btn-primary', 'btn-secondary', 'btn-ghost', 'btn-danger']) {
        expect(row.querySelector(`.${variant}`), variant).not.toBeNull()
      }
    }
    expect(Array.from(sm.querySelectorAll('button')).every((b) => b.classList.contains('btn-sm'))).toBe(true)
    expect(Array.from(md.querySelectorAll('button')).some((b) => b.classList.contains('btn-sm'))).toBe(false)
    expect(within(section).getAllByRole('button').filter((b) => (b as HTMLButtonElement).disabled)).toHaveLength(1)
  })

  it('badges: sentence-case results, the three decisions, P0-P3, a mark per category beside its words, an avatar per tone', () => {
    const { container } = renderAt()
    const section = sectionOf(container, 'badges')
    const words = (row: string) =>
      Array.from(section.querySelectorAll(`[data-badge-row="${row}"] > *`)).map((n) => n.textContent)
    expect(words('status')).toEqual(['Passed', 'Failed', 'Broken', 'Skipped', 'Flaky'])
    expect(words('decision')).toEqual(['Go', 'Conditional', 'No-Go'])
    expect(words('severity')).toEqual(['P0', 'P1', 'P2', 'P3'])
    const categories = section.querySelectorAll('[data-badge-row="category"] [data-category]')
    expect(categories).toHaveLength(7)
    for (const category of Array.from(categories)) {
      const mark = category.querySelector('svg[data-failure-category]')
      expect(mark?.getAttribute('data-failure-category')).toBe(category.getAttribute('data-category'))
      // The words are neutral text; the colour is the mark's.
      expect(category.className).toContain('text-[var(--color-text-secondary)]')
    }
    const avatars = Array.from(section.querySelectorAll<HTMLElement>('[data-avatar]'))
    expect(avatars.map((a) => a.getAttribute('data-avatar'))).toEqual(['slate', 'red', 'orange', 'teal', 'blue', 'violet'])
    for (const avatar of avatars) {
      expect(avatar.className).not.toMatch(/text-white/)
      expect(avatar.style.color).toMatch(/^var\(--/)
    }
  })

  it('states: empty, warning, compact, loading and the unavailable panel with a 503', () => {
    const { container } = renderAt()
    const section = sectionOf(container, 'states')
    expect(within(section).getByText('No runs in this window')).toBeInTheDocument()
    expect(within(section).getByText('Results are partial')).toBeInTheDocument()
    expect(within(section).getByText('No comments yet.').tagName).toBe('P')
    expect(within(section).getByText('Loading…')).toBeInTheDocument()
    const unavailable = within(section).getByTestId('ux-primitives-unavailable')
    expect(unavailable).toHaveAttribute('role', 'alert')
    expect(within(unavailable).getByText('HTTP 503')).toBeInTheDocument()
    expect(within(unavailable).getByRole('button', { name: /Retry/ })).toBeInTheDocument()
  })

  it('applies a registry theme, Paper included, and ignores anything else', () => {
    const { unmount } = renderAt('?theme=paper')
    expect(document.documentElement).toHaveAttribute('data-theme', 'paper')
    unmount()
    renderAt('?theme=%3Cscript%3E')
    expect(document.documentElement).not.toHaveAttribute('data-theme')
  })
})
