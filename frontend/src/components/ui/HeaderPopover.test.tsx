import { useRef, useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { HeaderPopover } from './HeaderPopover'

/** Mirrors the TopBar shape: a blurred header that forms a stacking context. */
function Harness({ onCloseSpy }: { onCloseSpy?: () => void } = {}) {
  const ref = useRef<HTMLButtonElement>(null)
  const [open, setOpen] = useState(false)
  return (
    <div>
      <header className="backdrop-blur" data-testid="header">
        <button ref={ref} onClick={() => setOpen(o => !o)}>Open</button>
        <HeaderPopover
          anchorRef={ref}
          open={open}
          onClose={() => { setOpen(false); onCloseSpy?.() }}
          width={200}
          ariaLabel="Test menu"
        >
          <button>Inside item</button>
        </HeaderPopover>
      </header>
      <main data-testid="page">page content</main>
    </div>
  )
}

describe('HeaderPopover', () => {
  it('escapes the header by rendering into document.body', () => {
    // Regression (UX audit issue 3): the header's `backdrop-blur` creates a
    // stacking context, so a menu nested inside it could never outrank a page
    // panel no matter how high its z-index.
    render(<Harness />)
    fireEvent.click(screen.getByText('Open'))
    const panel = screen.getByRole('menu', { name: 'Test menu' })
    expect(panel).toBeInTheDocument()
    expect(screen.getByTestId('header').contains(panel)).toBe(false)
    expect(panel.closest('body')).toBe(document.body)
  })

  it('stays open when a click lands INSIDE the portaled panel', () => {
    // The trap this component exists to avoid: once portaled, the panel is no
    // longer a DOM descendant of the trigger, so a naive
    // `rootRef.contains(e.target)` dismissal reads every in-menu click as an
    // outside click and closes before the item can fire.
    render(<Harness />)
    fireEvent.click(screen.getByText('Open'))
    fireEvent.mouseDown(screen.getByText('Inside item'))
    expect(screen.getByRole('menu', { name: 'Test menu' })).toBeInTheDocument()
  })

  it('closes on an outside click and on Escape', () => {
    render(<Harness />)
    fireEvent.click(screen.getByText('Open'))
    fireEvent.mouseDown(screen.getByTestId('page'))
    expect(screen.queryByRole('menu', { name: 'Test menu' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByText('Open'))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('menu', { name: 'Test menu' })).not.toBeInTheDocument()
  })

  it('caps its height so a long list cannot overflow a short viewport', () => {
    render(<Harness />)
    fireEvent.click(screen.getByText('Open'))
    const panel = screen.getByRole('menu', { name: 'Test menu' })
    expect(panel.className).toMatch(/max-h-\[70vh\]/)
    expect(panel.className).toMatch(/overflow-y-auto/)
  })

  it('sits above page panels but below the blocking dialog layer', () => {
    render(<Harness />)
    fireEvent.click(screen.getByText('Open'))
    // Page panels top out at z-50; TransitionReasonDialog owns z-[70].
    expect(screen.getByRole('menu', { name: 'Test menu' }).className).toMatch(/z-\[60\]/)
  })
})
