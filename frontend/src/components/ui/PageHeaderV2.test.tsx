/**
 * PageHeader v2 (UX redesign P0): the **?** help button, the **⋯** overflow
 * menu, the tabs slot and the compact size, with the v1 output unchanged when
 * none of them is used.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import PageHeader from './PageHeader'
import { useHelpStore } from '@/store/helpStore'

beforeEach(() => useHelpStore.getState().close())

const renderHeader = (ui: React.ReactNode) => render(<MemoryRouter>{ui}</MemoryRouter>)

describe('PageHeader v2', () => {
  it('without the new props renders exactly the v1 header: a 2xl title, no help, no menu', () => {
    const view = renderHeader(<PageHeader title="Runs" subtitle="All runs" />)
    const h1 = screen.getByRole('heading', { level: 1, name: 'Runs' })
    expect(h1.className).toContain('text-2xl')
    // The heading is not wrapped: the v1 DOM, so no existing page moves.
    expect(h1.parentElement?.className).toBe('max-w-3xl')
    expect(screen.queryByRole('button', { name: /Help/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'More actions' })).toBeNull()
    expect(view.container.querySelector('[data-page-header]')?.getAttribute('data-compact')).toBe('false')
  })

  it('helpTopic renders a ? that opens that topic', () => {
    renderHeader(<PageHeader title="Failures" helpTopic="failure-analysis" />)
    fireEvent.click(screen.getByRole('button', { name: 'Help: Failures' }))
    expect(useHelpStore.getState().topic).toBe('failure-analysis')
  })

  it('helpTopic "topic#anchor" opens that topic on that section', () => {
    renderHeader(<PageHeader title="Defects" helpTopic="failure-analysis#promoting-to-a-defect" />)
    const button = screen.getByRole('button', { name: 'Help: Defects' })
    expect(button).toHaveAttribute('data-help-topic', 'failure-analysis')
    expect(button).toHaveAttribute('data-help-anchor', 'promoting-to-a-defect')
    fireEvent.click(button)
    expect(useHelpStore.getState()).toMatchObject({ topic: 'failure-analysis', anchor: 'promoting-to-a-defect' })
  })

  it('overflow renders a ⋯ menu whose items act and close it', () => {
    const exportPdf = vi.fn()
    renderHeader(
      <PageHeader
        title="Summary"
        actions={<button type="button">Primary</button>}
        overflow={[
          { label: 'Export PDF', onClick: exportPdf },
          { label: 'Open settings', href: '/settings' },
        ]}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    expect(screen.getByRole('menuitem', { name: 'Open settings' })).toHaveAttribute('href', '/settings')
    fireEvent.click(screen.getByRole('menuitem', { name: 'Export PDF' }))
    expect(exportPdf).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('menuitem', { name: 'Export PDF' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Primary' })).toBeInTheDocument()
  })

  it('compact: an xl title and a one-line subtitle with its full text in title', () => {
    renderHeader(<PageHeader compact title="Trends" subtitle="A long subtitle that would wrap" tabs={<div data-testid="tabs" />} />)
    expect(screen.getByRole('heading', { level: 1, name: 'Trends' }).className).toContain('text-xl')
    const subtitle = screen.getByText('A long subtitle that would wrap')
    expect(subtitle.className).toContain('line-clamp-1')
    expect(subtitle).toHaveAttribute('title', 'A long subtitle that would wrap')
    expect(screen.getByTestId('tabs')).toBeInTheDocument()
  })
})
