/**
 * W27 PAN-01: a form opened where its action lives. It keeps the modal's
 * name, its first-field focus, its Escape and its focus return; it drops the
 * overlay (no dialog role, no aria-modal, no portal); and, with a slot, it
 * asks before typed input is replaced.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { InlineFormSection } from './InlineFormSection'
import { useInlineSlot } from './useInlineSlot'

function SlotHost() {
  const slot = useInlineSlot<'edit' | 'link'>()
  return (
    <div data-testid="host">
      <button type="button" onClick={() => slot.open('edit')}>Edit</button>
      <button type="button" onClick={() => slot.open('link')}>Link Run</button>
      {slot.openKey === 'edit' && (
        <InlineFormSection
          title="Edit Release"
          titleId="release-form-title"
          closeLabel="Close release dialog"
          onClose={slot.close}
          slot={slot}
          footer={<button type="button" onClick={slot.close}>Cancel</button>}
        >
          <label>Name <input aria-label="Name" /></label>
        </InlineFormSection>
      )}
      {slot.openKey === 'link' && (
        <InlineFormSection title="Link Test Run" titleId="link-test-run-title" onClose={slot.close} slot={slot}>
          <label>Run <select aria-label="Run"><option>r-1</option></select></label>
        </InlineFormSection>
      )}
    </div>
  )
}

function openEdit() {
  render(<SlotHost />)
  const trigger = screen.getByRole('button', { name: 'Edit' })
  trigger.focus()
  fireEvent.click(trigger)
  return { trigger, section: screen.getByRole('region', { name: 'Edit Release' }) }
}

describe('InlineFormSection', () => {
  it('is a region named by its heading, the old heading id kept, inside its parent (no portal)', () => {
    const { section } = openEdit()
    expect(section.tagName).toBe('SECTION')
    expect(section).toHaveAttribute('aria-labelledby', 'release-form-title')
    expect(document.getElementById('release-form-title')).toHaveTextContent('Edit Release')
    expect(screen.getByTestId('host')).toContainElement(section)
    expect(section).toHaveAttribute('data-in-page', 'form')
  })

  it('carries no dialog role and no aria-modal anywhere', () => {
    openEdit()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.querySelector('[aria-modal]')).toBeNull()
  })

  it('focuses the first field on mount', () => {
    openEdit()
    expect(screen.getByRole('textbox', { name: 'Name' })).toHaveFocus()
  })

  it('Escape inside closes it and focus goes back to the opener', () => {
    const { trigger } = openEdit()
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Name' }), { key: 'Escape' })
    expect(screen.queryByRole('region', { name: 'Edit Release' })).toBeNull()
    expect(trigger).toHaveFocus()
  })

  it('the X keeps its old label and closes it', () => {
    openEdit()
    fireEvent.click(screen.getByRole('button', { name: 'Close release dialog' }))
    expect(screen.queryByRole('region', { name: 'Edit Release' })).toBeNull()
  })

  it('canClose false: the X is disabled and Escape is swallowed (mid-submit)', () => {
    const onClose = vi.fn()
    render(
      <InlineFormSection title="Sync releases" onClose={onClose} canClose={false} closeLabel="Close sync releases dialog">
        <input aria-label="Source" />
      </InlineFormSection>,
    )
    expect(screen.getByRole('button', { name: 'Close sync releases dialog' })).toBeDisabled()
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Source' }), { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()
  })

  it('typing marks the slot dirty; another form asks first; Keep editing keeps the text', () => {
    openEdit()
    const name = screen.getByRole('textbox', { name: 'Name' })
    fireEvent.change(name, { target: { value: 'Checkout 2.5' } })

    fireEvent.click(screen.getByRole('button', { name: 'Link Run' }))
    const section = screen.getByRole('region', { name: 'Edit Release' })
    const question = within(section).getByRole('group', { name: 'Discard your changes to "Edit Release"?' })
    expect(within(question).getByRole('button', { name: 'Discard changes' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Link Test Run' })).toBeNull()

    fireEvent.click(within(question).getByRole('button', { name: 'Keep editing' }))
    expect(screen.queryByRole('group')).toBeNull()
    expect(screen.getByRole('textbox', { name: 'Name' })).toHaveValue('Checkout 2.5')
    expect(screen.queryByRole('region', { name: 'Link Test Run' })).toBeNull()
  })

  it('Discard changes opens the other form', () => {
    openEdit()
    fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'x' } })
    fireEvent.click(screen.getByRole('button', { name: 'Link Run' }))
    fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }))
    expect(screen.queryByRole('region', { name: 'Edit Release' })).toBeNull()
    expect(screen.getByRole('region', { name: 'Link Test Run' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Run' })).toHaveFocus()
  })

  it('a clean slot replaces at once', () => {
    openEdit()
    fireEvent.click(screen.getByRole('button', { name: 'Link Run' }))
    expect(screen.queryByRole('group')).toBeNull()
    expect(screen.getByRole('region', { name: 'Link Test Run' })).toBeInTheDocument()
  })

  it('Cancel never asks, even over typed input', () => {
    openEdit()
    fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'x' } })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('region', { name: 'Edit Release' })).toBeNull()
    expect(screen.queryByRole('group')).toBeNull()
    // Reopened: clean, so the other form replaces it at once.
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    fireEvent.click(screen.getByRole('button', { name: 'Link Run' }))
    expect(screen.getByRole('region', { name: 'Link Test Run' })).toBeInTheDocument()
  })

  it("as='aside' is complementary (a former non-modal side panel)", () => {
    render(
      <InlineFormSection as="aside" title="Failure signatures" closeLabel="Close panel" onClose={vi.fn()}>
        <p>clusters</p>
      </InlineFormSection>,
    )
    expect(screen.getByRole('complementary', { name: 'Failure signatures' }).tagName).toBe('ASIDE')
    expect(screen.getByRole('button', { name: 'Close panel' })).toHaveFocus()
  })

  it('ariaLabel names it when the heading text differs', () => {
    render(
      <InlineFormSection title="Decision trail" ariaLabel="AI decision trail" closeLabel="Close decision trail" onClose={vi.fn()}>
        <p>trail</p>
      </InlineFormSection>,
    )
    expect(screen.getByRole('region', { name: 'AI decision trail' })).not.toHaveAttribute('aria-labelledby')
    expect(screen.getByRole('heading', { level: 2, name: 'Decision trail' })).toBeInTheDocument()
  })

  it('onSubmit makes the body and the footer a form', () => {
    const onSubmit = vi.fn((event: { preventDefault: () => void }) => event.preventDefault())
    render(
      <InlineFormSection
        title="Correct claim"
        titleId="claim-correction-heading"
        headingLevel={3}
        onClose={vi.fn()}
        onSubmit={onSubmit}
        footer={<button type="submit">Record correction</button>}
        testId="claim-form"
        dataAttributes={{ 'data-claim-correction': 'c-1' }}
        busy
      >
        <input aria-label="Reason" />
      </InlineFormSection>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Record correction' }))
    expect(onSubmit).toHaveBeenCalledTimes(1)
    const section = screen.getByRole('region', { name: 'Correct claim' })
    expect(section).toHaveAttribute('data-testid', 'claim-form')
    expect(section).toHaveAttribute('data-claim-correction', 'c-1')
    expect(section).toHaveAttribute('aria-busy', 'true')
    expect(screen.getByRole('heading', { level: 3, name: 'Correct claim' })).toHaveAttribute('id', 'claim-correction-heading')
  })

  it('a form that goes away takes its dirty flag with it', () => {
    function Host() {
      const slot = useInlineSlot<'a'>()
      const [shown, setShown] = useState(true)
      return (
        <>
          <output data-testid="dirty">{String(slot.dirty)}</output>
          <button type="button" onClick={() => setShown(false)}>Unmount</button>
          {shown && (
            <InlineFormSection title="A" onClose={vi.fn()} slot={slot}>
              <textarea aria-label="Notes" />
            </InlineFormSection>
          )}
        </>
      )
    }
    render(<Host />)
    fireEvent.change(screen.getByRole('textbox', { name: 'Notes' }), { target: { value: 'n' } })
    expect(screen.getByTestId('dirty')).toHaveTextContent('true')
    fireEvent.click(screen.getByRole('button', { name: 'Unmount' }))
    expect(screen.getByTestId('dirty')).toHaveTextContent('false')
  })
})
