/**
 * `/__ux-primitives` (dev only): every section the visual spec shoots renders
 * once, in order — the six UX-redesign primitives, the W29a standard's three
 * (`buttons`, `badges`, `states`; contract §2.8) and W29b's three
 * (`type-scale`, `table`, `panel`; W29b contract §2.6) — and each of the
 * standard's sections shows what it is the reading surface for.
 */
import { fireEvent, render, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import UxPrimitivesPage from './UxPrimitivesPage'

const SECTIONS = [
  'page-header', 'route-tabs', 'status-banner', 'kpi-strip', 'window-picker', 'disclosure',
  'buttons', 'badges', 'states',
  'type-scale', 'table', 'panel',
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

  it('renders the twelve sections once each, in order, each named by its heading', () => {
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

  it('type-scale: one line per step 2xs … 2xl with its px and line-height, the eyebrow label, the three numbers', () => {
    const { container } = renderAt()
    const section = sectionOf(container, 'type-scale')
    const steps = Array.from(section.querySelectorAll('[data-type-step]'))
    expect(steps.map((s) => s.getAttribute('data-type-step'))).toEqual(['2xs', 'xs', 'sm', 'base', 'md', 'lg', 'xl', '2xl', 'eyebrow'])
    for (const step of steps.slice(0, 8)) {
      const id = step.getAttribute('data-type-step')
      const [spec, sample] = Array.from(step.children)
      expect(spec.textContent).toMatch(new RegExp(`^text-${id} · \\d+ px / [\\d.]+$`))
      expect(sample.className.split(/\s+/)).toContain(`text-${id}`)
      expect(sample.className).not.toMatch(/text-\[\d/)
    }
    expect(within(section).getByText('New failures')).toHaveClass('eyebrow')
    expect(Array.from(section.querySelectorAll('[data-type-number]'), (n) => n.getAttribute('data-type-number'))).toEqual([
      '--text-stat-sm', '--text-stat-lg', '--text-display-lg',
    ])
    for (const number of Array.from(section.querySelectorAll('[data-type-number]'))) {
      expect(number.querySelector('p')?.className).toContain(`text-[length:var(${number.getAttribute('data-type-number')})]`)
    }
  })

  it('table: the one table at each density, a sortable header active and inactive, a numeric column, a selected row', () => {
    const { container } = renderAt()
    const section = sectionOf(container, 'table')
    const tables = Array.from(section.querySelectorAll('table'))
    expect(tables.map((t) => t.getAttribute('data-density'))).toEqual(['comfortable', 'dense', 'compact'])
    for (const table of tables) {
      const headers = Array.from(table.querySelectorAll('th'))
      expect(headers.map((h) => h.textContent)).toEqual(['Test', 'Suite', 'Status', 'Duration', 'Failures'])
      expect(headers.map((h) => h.getAttribute('aria-sort'))).toEqual(['ascending', 'none', null, 'none', null])
      expect(headers.every((h) => h.classList.contains('th'))).toBe(true)
      expect(headers.filter((h) => h.classList.contains('th-num')).map((h) => h.textContent)).toEqual(['Duration', 'Failures'])
      expect(headers[0].querySelector('button.th-sort')).not.toBeNull()
      const rows = Array.from(table.querySelectorAll('tbody tr'))
      expect(rows.every((r) => r.classList.contains('table-row'))).toBe(true)
      expect(rows.filter((r) => r.hasAttribute('data-selected')).map((r) => r.getAttribute('data-row'))).toEqual(['r1'])
      for (const row of rows) {
        expect(Array.from(row.querySelectorAll('td')).filter((c) => c.classList.contains('td-num'))).toHaveLength(2)
      }
    }
    // The shared sort state: Duration sorts every table, numerically, and the Test header goes back to none.
    fireEvent.click(within(tables[0]).getByRole('button', { name: 'Duration' }))
    for (const table of tables) {
      expect(Array.from(table.querySelectorAll('tbody tr'), (r) => r.getAttribute('data-row'))).toEqual(['r2', 'r1', 'r3'])
      expect(Array.from(table.querySelectorAll('th'), (h) => h.getAttribute('aria-sort'))).toEqual(['none', 'none', null, 'ascending', null])
    }
  })

  it('panel: a Panel with a subtitle and actions over a KpiStrip of three md tiles and four sm dimension tiles', () => {
    const { container } = renderAt()
    const section = sectionOf(container, 'panel')
    const panel = section.querySelector('[data-panel]') as HTMLElement
    expect(panel).toHaveClass('card')
    expect(within(panel).getByRole('heading', { level: 3, name: 'Release readiness' })).toBeInTheDocument()
    expect(within(panel).getByText('Last 30 days · 41 runs on main')).toBeInTheDocument()
    expect(within(panel.querySelector('[data-panel-actions]') as HTMLElement).getByRole('button', { name: 'Open gate' })).toHaveClass('btn-secondary', 'btn-sm')
    const strip = panel.querySelector('[data-kpi-strip]') as HTMLElement
    expect(strip.querySelectorAll('[data-metric-card="compact"]')).toHaveLength(3)
    const dims = Array.from(panel.querySelectorAll<HTMLElement>('[data-dimension-row] [data-metric-card="stat"]'))
    expect(dims.map((d) => d.getAttribute('data-dimension'))).toEqual(['stability', 'diversity', 'time_to_fix', 'coverage'])
    for (const dim of dims) {
      expect(dim.querySelector('[data-metric-bar]')).not.toBeNull()
      expect(dim.querySelector('p')?.textContent).toMatch(/\d+%$/)
    }
    // The unmeasured one: an em dash and the empty track.
    expect(dims[3].querySelector('p.tabular-nums')?.textContent).toBe('—')
    expect(dims[3].querySelector('[data-metric-bar] i')).toBeNull()
    expect(dims[0].querySelector<HTMLElement>('[data-metric-bar] i')?.style.width).toBe('82%')
    // No white sheen and no 16 px corner anywhere in the section.
    for (const el of Array.from(section.querySelectorAll('*'))) {
      expect(el.getAttribute('class') ?? '').not.toMatch(/\bbg-white|\brounded-xl\b/)
      expect(el.getAttribute('style') ?? '').not.toMatch(/rgba\(/)
    }
  })

  it('applies a registry theme, Paper included, and ignores anything else', () => {
    const { unmount } = renderAt('?theme=paper')
    expect(document.documentElement).toHaveAttribute('data-theme', 'paper')
    unmount()
    renderAt('?theme=%3Cscript%3E')
    expect(document.documentElement).not.toHaveAttribute('data-theme')
  })
})
