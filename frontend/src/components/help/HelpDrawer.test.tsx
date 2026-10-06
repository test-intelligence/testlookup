import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useHelpStore, openHelp } from '@/store/helpStore'
import HelpDrawer from './HelpDrawer'
import { parseDocHref } from './docLinks'

vi.mock('@/components/guide/MermaidDiagram', () => ({ default: () => <div data-stub="mermaid" /> }))

function Where() {
  return <div data-testid="where">{useLocation().pathname}</div>
}

function renderDrawer() {
  return render(
    <MemoryRouter initialEntries={['/flaky-coach']}>
      <Routes>
        <Route path="*" element={<><HelpDrawer /><Where /></>} />
      </Routes>
    </MemoryRouter>,
  )
}

const scrolled: string[] = []
beforeEach(() => {
  scrolled.length = 0
  Element.prototype.scrollIntoView = function (this: Element) {
    scrolled.push(this.id || this.getAttribute('data-help-topic') || this.tagName)
  }
})
afterEach(() => {
  act(() => useHelpStore.getState().close())
})

describe('HelpDrawer (UX redesign P1)', () => {
  it('renders nothing while help is closed', () => {
    renderDrawer()
    expect(screen.queryByRole('complementary')).toBeNull()
    expect(document.querySelector('[data-help-topic]')).toBeNull()
  })

  it('opens a topic beside the page: its title, its summary and its body, and a link to the full docs', () => {
    renderDrawer()
    act(() => openHelp('flaky'))
    // Once: the panel's title, not again as the topic's own H1.
    expect(screen.getAllByRole('heading', { name: 'Flaky tests' })).toHaveLength(1)
    expect(document.querySelector('[data-help-topic="flaky"]')).not.toBeNull()
    expect(screen.getByRole('heading', { name: /The score is four signals, weighted/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Open in full docs/ })).toHaveAttribute('href', '/docs/flaky')
    // Non-modal: the page behind is still the page.
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByTestId('where')).toHaveTextContent('/flaky-coach')
  })

  it('a link to another topic opens it in the drawer, without leaving the page', () => {
    renderDrawer()
    act(() => openHelp('flaky'))
    fireEvent.click(screen.getByRole('link', { name: 'Investigating failures' }))
    expect(document.querySelector('[data-help-topic="failure-analysis"]')).not.toBeNull()
    expect(screen.getByTestId('where')).toHaveTextContent('/flaky-coach')
  })

  it('opens on a section: it scrolls to that heading, and the full-docs link carries it', () => {
    renderDrawer()
    act(() => openHelp('failure-analysis', 'promoting-to-a-defect'))
    expect(scrolled).toContain('promoting-to-a-defect')
    expect(screen.getByRole('link', { name: /Open in full docs/ })).toHaveAttribute(
      'href',
      '/docs/failure-analysis#promoting-to-a-defect',
    )
  })

  it('an unknown topic falls back to the default page rather than an empty drawer', () => {
    renderDrawer()
    act(() => openHelp('no-such-topic'))
    expect(document.querySelector('[data-help-topic="introduction"]')).not.toBeNull()
  })

  it('closes from its close button', () => {
    renderDrawer()
    act(() => openHelp('flaky'))
    fireEvent.click(screen.getByRole('button', { name: 'Close help' }))
    expect(document.querySelector('[data-help-topic]')).toBeNull()
    expect(useHelpStore.getState().topic).toBeNull()
  })
})

describe('parseDocHref', () => {
  it('reads a topic and an optional section, and nothing else', () => {
    expect(parseDocHref('/docs/flaky')).toEqual({ topic: 'flaky', anchor: null })
    expect(parseDocHref('/docs/flaky#scope-and-isolation')).toEqual({ topic: 'flaky', anchor: 'scope-and-isolation' })
    expect(parseDocHref('/runs')).toBeNull()
    expect(parseDocHref('/docs')).toBeNull()
  })
})
