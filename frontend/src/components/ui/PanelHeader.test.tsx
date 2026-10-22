/**
 * The ONE panel header and panel (W29b UIX-09). The shape the visual specs rely on: a `card` root that is a
 * `<div>`, a real heading, nothing between them that `cardByHeading` / `cardAround`
 * (`tests/lib/production-pages.ts`) would stop at first.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import PanelHeader, { PANEL_TITLE_CLASS, Panel } from './PanelHeader'

const classes = (el: Element | null) => (el?.getAttribute('class') ?? '').split(/\s+/).filter(Boolean)

/** `tests/lib/production-pages.ts` `cardAround`: the nearest ancestor that is a section or has class card / rounded-xl. */
function cardAround(el: Element): Element | null {
  for (let node = el.parentElement; node; node = node.parentElement) {
    if (node.tagName === 'SECTION' || node.classList.contains('card') || node.classList.contains('rounded-xl')) return node
  }
  return null
}

describe('PANEL_TITLE_CLASS', () => {
  it('is 14 px / 600 in a tight line, in the text colour', () => {
    expect(PANEL_TITLE_CLASS.split(' ')).toEqual(['m-0', 'text-base', 'font-semibold', 'leading-tight', 'text-[var(--color-text)]'])
  })
})

describe('Panel', () => {
  it('is a .card <div> with no padding of its own, data-panel, the header, then the body', () => {
    const { container } = render(
      <Panel title="Top failing tests" className="mt-4" data-testid="top-failing">
        <table aria-label="Top failing tests" />
      </Panel>,
    )
    const root = container.firstElementChild as HTMLElement
    expect(root.tagName).toBe('DIV')
    expect(root).toHaveAttribute('data-panel', '')
    expect(root).toHaveAttribute('data-testid', 'top-failing')
    expect(classes(root)).toEqual(expect.arrayContaining(['card', '!p-0', 'overflow-hidden', 'mt-4']))
    expect(root.firstElementChild).toHaveAttribute('data-panel-header', '')
    expect(root.lastElementChild?.tagName).toBe('TABLE')
    expect(root.querySelector('section, header')).toBeNull()
  })

  it('the cardByHeading lookup from the heading reaches the Panel root', () => {
    const { container } = render(
      <Panel title="Suite pass rates" actions={<a href="/trends">View all</a>}>
        <ul><li>a</li></ul>
      </Panel>,
    )
    const heading = screen.getByRole('heading', { name: 'Suite pass rates' })
    expect(cardAround(heading)).toBe(container.firstElementChild)
    expect(heading.closest('.card')).toBe(container.firstElementChild)
  })
})

describe('PanelHeader', () => {
  it('the title is an h3 by default, with PANEL_TITLE_CLASS and its hint as the title attribute', () => {
    render(<PanelHeader title="Build velocity" titleHint="Runs per day, last 30 days" id="velocity-title" />)
    const heading = screen.getByRole('heading', { level: 3, name: 'Build velocity' })
    expect(heading.className).toBe(PANEL_TITLE_CLASS)
    expect(heading).toHaveAttribute('title', 'Runs per day, last 30 days')
    expect(heading).toHaveAttribute('id', 'velocity-title')
  })

  it.each([2, 4] as const)('headingLevel %i is honoured', (level) => {
    render(<PanelHeader title="Panel" headingLevel={level} />)
    expect(screen.getByRole('heading', { level, name: 'Panel' })).toBeInTheDocument()
  })

  it('nowrapTitle keeps the title on one line', () => {
    render(<PanelHeader title="In progress" nowrapTitle />)
    expect(classes(screen.getByRole('heading'))).toContain('whitespace-nowrap')
  })

  it('subtitle is one 13 px secondary line under the title', () => {
    render(<PanelHeader title="Defects" subtitle="Open, by severity" />)
    const subtitle = screen.getByText('Open, by severity')
    expect(subtitle.tagName).toBe('P')
    expect(classes(subtitle)).toEqual(expect.arrayContaining(['text-sm', 'text-[var(--color-text-secondary)]']))
    expect(subtitle.previousElementSibling).toBe(screen.getByRole('heading'))
  })

  it('actions sit in [data-panel-actions]: 12 px muted, never wrapping their words', () => {
    const { container } = render(<PanelHeader title="Runs" actions={<span>12 runs</span>} />)
    const actions = container.querySelector('[data-panel-actions]') as HTMLElement
    expect(actions).toHaveTextContent('12 runs')
    expect(classes(actions)).toEqual(expect.arrayContaining(['whitespace-nowrap', 'text-xs', 'text-[var(--color-text-muted)]']))
    const row = container.querySelector('[data-panel-header]') as HTMLElement
    expect(classes(row)).toEqual(expect.arrayContaining(['flex', 'flex-wrap', 'justify-between', 'px-4', 'py-3']))
  })

  it('no actions, no actions slot', () => {
    const { container } = render(<PanelHeader title="Runs" />)
    expect(container.querySelector('[data-panel-actions]')).toBeNull()
  })

  it('draws the 1 px divider by default; divider={false} drops it', () => {
    const { container, rerender } = render(<PanelHeader title="Runs" />)
    const row = () => container.querySelector('[data-panel-header]') as HTMLElement
    expect(classes(row())).toEqual(expect.arrayContaining(['border-b', 'border-[var(--color-border)]']))
    rerender(<PanelHeader title="Runs" divider={false} />)
    expect(classes(row())).not.toContain('border-b')
  })
})
