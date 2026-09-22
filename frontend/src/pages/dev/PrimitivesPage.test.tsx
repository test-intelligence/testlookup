import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'

import PrimitivesPage from './PrimitivesPage'
import { HOSTILE_FLAG, HOSTILE_LABEL, LONG_LABEL, RELEASE_OPTION_COUNT, SUITE_OPTION_COUNT } from './primitivesFixtures'

function renderAt(search = '') {
  return render(
    <MemoryRouter initialEntries={[`/__primitives${search}`]}>
      <PrimitivesPage />
    </MemoryRouter>,
  )
}

const SECTIONS = ['multiselect', 'chips', 'skeleton', 'breadcrumbs', 'side-panel', 'formatters', 'download']

describe('PrimitivesPage', () => {
  afterEach(() => {
    document.documentElement.removeAttribute('data-theme')
  })

  it('fixtures are the sizes the spec relies on', () => {
    expect(LONG_LABEL).toHaveLength(300)
    expect(LONG_LABEL.startsWith('checkout-')).toBe(true)
    expect(LONG_LABEL.endsWith('-payments-spec')).toBe(true)
    expect(RELEASE_OPTION_COUNT).toBe(60)
    expect(SUITE_OPTION_COUNT).toBeGreaterThan(200)
  })

  it('renders every primitive section once, each named by its heading', () => {
    const { container } = renderAt()
    const ids = Array.from(container.querySelectorAll('[data-primitive]')).map((n) => n.getAttribute('data-primitive'))
    expect(ids).toEqual(SECTIONS)
    for (const id of SECTIONS) {
      const section = container.querySelector(`[data-primitive="${id}"]`) as HTMLElement
      expect(within(section).getByRole('heading', { level: 2 })).toBeInTheDocument()
    }
    expect(screen.getByRole('navigation', { name: 'Breadcrumb' })).toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Active filters' })).toBeInTheDocument()
  })

  it('renders the hostile release label as text when the list is opened', () => {
    renderAt()
    fireEvent.click(screen.getByRole('button', { name: /^Release/ }))
    fireEvent.change(screen.getByRole('combobox', { name: 'Filter Release' }), { target: { value: 'bold' } })
    const option = screen.getByRole('option')
    // Longer than 60 characters, so middle-truncated; the full text is the
    // label's title and the option's accessible name.
    expect(within(option).getByTitle(HOSTILE_LABEL)).toBeInTheDocument()
    expect(option).toHaveAccessibleName(`${HOSTILE_LABEL}, 12`)
    expect(option.textContent?.startsWith('<img src=x onerror=')).toBe(true)
    expect(option.querySelector('img, b')).toBeNull()
    expect((window as unknown as Record<string, unknown>)[HOSTILE_FLAG]).toBeUndefined()
  })

  it('applies a registry theme and ignores anything else', () => {
    const { unmount } = renderAt('?theme=lab')
    expect(document.documentElement).toHaveAttribute('data-theme', 'lab')
    expect(screen.getByTestId('primitives-gallery')).toHaveAttribute('data-gallery-theme', 'lab')
    unmount()
    expect(document.documentElement).not.toHaveAttribute('data-theme')

    renderAt('?theme=%3Cscript%3E')
    expect(document.documentElement).not.toHaveAttribute('data-theme')
    expect(screen.getByTestId('primitives-gallery')).toHaveAttribute('data-gallery-theme', 'default')
  })
})
