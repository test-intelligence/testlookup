/**
 * W27 PAN-01: an item's detail docked beside its list — the /test-management
 * case panel's shape, shared. An <aside> named by its heading; Escape inside
 * closes it (a form inside closing first); a dirty form inside asks before
 * the pane goes.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { DetailPane, type DetailPaneProps } from './DetailPane'
import { InlineFormSection } from './InlineFormSection'
import { useInlineSlot } from './useInlineSlot'

function pane(props: Partial<DetailPaneProps> = {}) {
  const onClose = vi.fn()
  render(
    <DetailPane title="2026.11" closeLabel="Close release" onClose={onClose} {...props}>
      <p>Body</p>
    </DetailPane>,
  )
  return { onClose, aside: screen.getByRole('complementary', { name: '2026.11' }) }
}

describe('DetailPane', () => {
  it('is an <aside> named by its heading, the TM card shape', () => {
    const { aside } = pane({ titleId: 'tm-case-detail-title', dataAttributes: { 'data-case-panel': '', 'data-expanded': 'false' } })
    expect(aside.tagName).toBe('ASIDE')
    expect(aside).toHaveAttribute('aria-labelledby', 'tm-case-detail-title')
    expect(aside).toHaveAttribute('data-in-page', 'detail')
    expect(aside).toHaveAttribute('data-case-panel', '')
    expect(aside).toHaveAttribute('data-expanded', 'false')
    expect(aside.className).toBe('sticky top-3 flex flex-col rounded-xl overflow-hidden')
    expect(aside.style.maxHeight).toBe('calc(100vh - 96px)')
    expect(screen.getByRole('heading', { level: 2, name: '2026.11' })).toHaveAttribute('id', 'tm-case-detail-title')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.querySelector('[aria-modal]')).toBeNull()
  })

  it('Close by its label; Expand only with onToggleExpand, its label following the state', () => {
    const onToggleExpand = vi.fn()
    const { onClose } = pane({
      closeTitle: 'Close (Esc)',
      onToggleExpand,
      expandLabels: { expand: 'Expand the case to full width', collapse: 'Show the list beside the case', expandTitle: 'Expand', collapseTitle: 'Show the list' },
    })
    const close = screen.getByRole('button', { name: 'Close release' })
    expect(close).toHaveAttribute('title', 'Close (Esc)')
    fireEvent.click(close)
    expect(onClose).toHaveBeenCalledTimes(1)
    const expand = screen.getByRole('button', { name: 'Expand the case to full width' })
    expect(expand).toHaveAttribute('title', 'Expand')
    fireEvent.click(expand)
    expect(onToggleExpand).toHaveBeenCalledTimes(1)
  })

  it('no Expand button without onToggleExpand; the collapse label while expanded', () => {
    pane()
    expect(screen.queryByRole('button', { name: /expand/i })).toBeNull()
  })

  it('Prev / Next by their labels: Prev disabled at the first, the position only when total > 1', () => {
    const onNext = vi.fn()
    pane({ position: { index: 1, total: 25 }, onNext, prevLabel: 'Previous case', nextLabel: 'Next case', testIds: { position: 'tm-case-position', actions: 'tm-case-panel-actions' } })
    expect(screen.getByRole('button', { name: 'Previous case' })).toBeDisabled()
    expect(screen.getByTestId('tm-case-position')).toHaveTextContent('1 of 25')
    expect(within(screen.getByTestId('tm-case-panel-actions')).getByRole('button', { name: 'Next case' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: 'Next case' }))
    expect(onNext).toHaveBeenCalledTimes(1)
  })

  it('no position and no Prev / Next for a single item', () => {
    pane({ position: { index: 1, total: 1 }, prevLabel: 'Previous case', nextLabel: 'Next case', testIds: { position: 'tm-case-position' } })
    expect(screen.queryByTestId('tm-case-position')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Previous case' })).toBeNull()
  })

  it('subtitle, actions and footer render where they belong', () => {
    pane({
      subtitle: <span>Planned</span>,
      actions: <button type="button">Open full page</button>,
      footer: <div data-release-panel-actions="">Edit</div>,
    })
    expect(screen.getByText('Planned').parentElement?.className).toBe('flex items-center gap-2 mt-1.5 flex-wrap')
    expect(screen.getByRole('button', { name: 'Open full page' })).toBeInTheDocument()
    const footer = document.querySelector('[data-release-panel-actions]')?.parentElement as HTMLElement
    expect(footer.className).toBe('border-t border-[var(--color-border)] px-4 py-3 flex-shrink-0')
  })

  it("bodyLayout 'scroll' wraps the body; 'host' places it straight in the pane's column", () => {
    const { unmount } = render(
      <DetailPane title="A" closeLabel="Close" onClose={vi.fn()} bodyClassName="px-4 py-3"><p>Scrolled</p></DetailPane>,
    )
    expect(screen.getByText('Scrolled').parentElement?.className).toBe('min-h-0 flex-1 overflow-y-auto px-4 py-3')
    unmount()
    render(<DetailPane title="B" closeLabel="Close" onClose={vi.fn()} bodyLayout="host"><p>Hosted</p></DetailPane>)
    expect(screen.getByText('Hosted').parentElement?.className).toBe('flex flex-col min-h-0 flex-1')
  })

  it('takes focus on open by default; manageFocus false never moves it', () => {
    const { unmount } = render(<DetailPane title="A" closeLabel="Close A" onClose={vi.fn()}><p>x</p></DetailPane>)
    expect(screen.getByRole('button', { name: 'Close A' })).toHaveFocus()
    unmount()
    const outside = document.createElement('button')
    document.body.appendChild(outside)
    outside.focus()
    render(<DetailPane title="B" closeLabel="Close B" onClose={vi.fn()} manageFocus={false}><p>x</p></DetailPane>)
    expect(outside).toHaveFocus()
    outside.remove()
  })

  it("Escape inside closes it; escape 'none' leaves the key to the host", () => {
    const { onClose, aside } = pane()
    fireEvent.keyDown(within(aside).getByRole('button', { name: 'Close release' }), { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("escape 'none' does not close and does not swallow the key", () => {
    const onDocument = vi.fn()
    document.addEventListener('keydown', onDocument)
    try {
      const { onClose, aside } = pane({ escape: 'none' })
      fireEvent.keyDown(within(aside).getByRole('button', { name: 'Close release' }), { key: 'Escape' })
      expect(onClose).not.toHaveBeenCalled()
      expect(onDocument).toHaveBeenCalledTimes(1)
      expect(onDocument.mock.calls[0][0].defaultPrevented).toBe(false)
    } finally {
      document.removeEventListener('keydown', onDocument)
    }
  })
})

function PaneWithForm() {
  const slot = useInlineSlot<'edit'>()
  const [open, setOpen] = useState(true)
  if (!open) return <p>Pane closed</p>
  return (
    <DetailPane title="2026.11" closeLabel="Close release" onClose={() => setOpen(false)} slot={slot}>
      <button type="button" onClick={() => slot.open('edit')}>Edit</button>
      {slot.openKey === 'edit' && (
        <InlineFormSection title="Edit Release" titleId="release-form-title" onClose={slot.close} slot={slot}>
          <input aria-label="Description" />
        </InlineFormSection>
      )}
    </DetailPane>
  )
}

describe('DetailPane with a form inside', () => {
  it('Escape inside the nested form closes the form only', () => {
    render(<PaneWithForm />)
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Description' }), { key: 'Escape' })
    expect(screen.queryByRole('region', { name: 'Edit Release' })).toBeNull()
    expect(screen.getByRole('complementary', { name: '2026.11' })).toBeInTheDocument()

    // A second Escape, from the pane itself, closes the pane.
    fireEvent.keyDown(screen.getByRole('button', { name: 'Edit' }), { key: 'Escape' })
    expect(screen.getByText('Pane closed')).toBeInTheDocument()
  })

  it('a dirty form makes Close ask first; Discard changes then closes the pane', () => {
    render(<PaneWithForm />)
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Description' }), { target: { value: 'notes' } })

    fireEvent.click(screen.getByRole('button', { name: 'Close release' }))
    expect(screen.getByRole('complementary', { name: '2026.11' })).toBeInTheDocument()
    const question = screen.getByRole('group', { name: 'Discard your changes to "Edit Release"?' })

    fireEvent.click(within(question).getByRole('button', { name: 'Keep editing' }))
    expect(screen.getByRole('textbox', { name: 'Description' })).toHaveValue('notes')

    fireEvent.click(screen.getByRole('button', { name: 'Close release' }))
    fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }))
    expect(screen.getByText('Pane closed')).toBeInTheDocument()
  })

  it('a clean form inside does not stop the pane closing', () => {
    render(<PaneWithForm />)
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    fireEvent.click(screen.getByRole('button', { name: 'Close release' }))
    expect(screen.getByText('Pane closed')).toBeInTheDocument()
  })
})
