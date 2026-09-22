/**
 * VIZ-109: the breadcrumb pattern — a named nav, an ordered list, real links
 * for ancestors, and the current page marked but not linked.
 */
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import Breadcrumbs from './Breadcrumbs'

const renderCrumbs = (items: Parameters<typeof Breadcrumbs>[0]['items']) =>
  render(
    <MemoryRouter>
      <Breadcrumbs items={items} />
    </MemoryRouter>,
  )

describe('Breadcrumbs', () => {
  it('is a nav named "Breadcrumb" around an ordered list', () => {
    renderCrumbs([{ label: 'Releases', to: '/releases' }, { label: 'R2' }])
    const nav = screen.getByRole('navigation', { name: 'Breadcrumb' })
    const list = within(nav).getByRole('list')
    expect(list.tagName).toBe('OL')
    expect(within(list).getAllByRole('listitem')).toHaveLength(2)
  })

  it('links ancestors and marks the current page without linking it', () => {
    renderCrumbs([
      { label: 'Releases', to: '/releases' },
      { label: 'R2', to: '/releases/r2' },
      { label: 'checkout suite', to: '/ignored' },
    ])
    expect(screen.getByRole('link', { name: 'Releases' })).toHaveAttribute('href', '/releases')
    expect(screen.getByRole('link', { name: 'R2' })).toHaveAttribute('href', '/releases/r2')
    const current = screen.getByText('checkout suite')
    expect(current).toHaveAttribute('aria-current', 'page')
    expect(current.closest('a')).toBeNull()
    expect(screen.getAllByRole('link')).toHaveLength(2)
  })

  it('truncates middle items with the full label in title; separators are hidden', () => {
    const long = 'a-very-long-intermediate-level-name-that-would-push-the-page-sideways'
    const { container } = renderCrumbs([{ label: 'Home', to: '/' }, { label: long, to: '/mid' }, { label: 'Here' }])
    const middle = screen.getByRole('link', { name: long })
    expect(middle).toHaveAttribute('title', long)
    expect(middle.className).toMatch(/\btruncate\b/)
    expect(middle.className).toMatch(/max-w-\[10rem\]/)
    expect(screen.getByRole('link', { name: 'Home' }).className).not.toMatch(/max-w-\[10rem\]/)
    const separators = container.querySelectorAll('svg')
    expect(separators).toHaveLength(2)
    separators.forEach((svg) => expect(svg).toHaveAttribute('aria-hidden', 'true'))
  })

  it('renders a label with markup as text; a level without a page is plain text', () => {
    renderCrumbs([{ label: '<b>root</b>' }, { label: 'Here' }])
    expect(screen.getByText('<b>root</b>')).toBeInTheDocument()
    expect(document.querySelector('nav b')).toBeNull()
    expect(screen.queryAllByRole('link')).toHaveLength(0)
  })

  it('renders nothing for an empty trail', () => {
    const { container } = renderCrumbs([])
    expect(container).toBeEmptyDOMElement()
  })
})
