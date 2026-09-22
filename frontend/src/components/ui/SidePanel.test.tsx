/**
 * VIZ-109: SidePanel. The modal shape's full keyboard contract lives in
 * modalKeyboardContract.test.tsx with every other modal; this file covers the
 * non-modal shape and what distinguishes the two.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { useRef, useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import SidePanel from './SidePanel'

function Harness({ modal = false, onClose = () => {} }: { modal?: boolean; onClose?: () => void }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open details
      </button>
      <button type="button">Elsewhere</button>
      <SidePanel
        open={open}
        modal={modal}
        title="Run 42 details"
        onClose={() => {
          onClose()
          setOpen(false)
        }}
        footer={<button type="button">Footer action</button>}
      >
        <button type="button">Inside</button>
      </SidePanel>
    </>
  )
}

function openPanel(modal = false) {
  const onClose = vi.fn()
  render(<Harness modal={modal} onClose={onClose} />)
  const opener = screen.getByRole('button', { name: 'Open details' })
  opener.focus()
  fireEvent.click(opener)
  return { opener, onClose }
}

describe('SidePanel — non-modal (default)', () => {
  it('renders nothing while closed', () => {
    render(<SidePanel open={false} title="x" onClose={() => {}}>body</SidePanel>)
    expect(screen.queryByRole('complementary')).toBeNull()
  })

  it('is a landmark named by its heading, not a dialog, and takes focus', () => {
    openPanel()
    const panel = screen.getByRole('complementary', { name: 'Run 42 details' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(panel).not.toHaveAttribute('aria-modal')
    expect(panel.contains(document.activeElement)).toBe(true)
    expect(screen.getByRole('button', { name: 'Close panel' })).toHaveFocus()
    expect(screen.getByRole('button', { name: 'Footer action' })).toBeInTheDocument()
  })

  it('Escape inside closes it and returns focus to the opener', () => {
    const { opener, onClose } = openPanel()
    screen.getByRole('button', { name: 'Inside' }).focus()
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('complementary')).toBeNull()
    expect(opener).toHaveFocus()
  })

  it('the close button closes it and returns focus to the opener', () => {
    const { opener } = openPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Close panel' }))
    expect(screen.queryByRole('complementary')).toBeNull()
    expect(opener).toHaveFocus()
  })

  it('does not trap Tab: the page behind stays reachable', () => {
    openPanel()
    const close = screen.getByRole('button', { name: 'Close panel' })
    close.focus()
    // No keydown handler may cancel Tab on a non-modal panel.
    expect(fireEvent.keyDown(close, { key: 'Tab' })).toBe(true)
    expect(fireEvent.keyDown(close, { key: 'Tab', shiftKey: true })).toBe(true)
  })

  it('does not steal focus back if the user moved it elsewhere before it closed', () => {
    const onClose = vi.fn()
    const { rerender } = render(
      <SidePanel open title="t" onClose={onClose}>
        body
      </SidePanel>,
    )
    const outside = document.createElement('button')
    document.body.appendChild(outside)
    outside.focus()
    rerender(
      <SidePanel open={false} title="t" onClose={onClose}>
        body
      </SidePanel>,
    )
    expect(outside).toHaveFocus()
    outside.remove()
  })
})

/** jsdom has no matchMedia; stand one in that answers `min-width` queries for `width`. */
function atViewportWidth(width: number) {
  const real = window.matchMedia
  window.matchMedia = ((query: string) => {
    const min = /min-width:\s*(\d+)px/.exec(query)
    return {
      matches: min ? width >= Number(min[1]) : false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }
  }) as unknown as typeof window.matchMedia
  return () => {
    window.matchMedia = real
  }
}

describe('SidePanel — non-modal never covers the page (review fix 4)', () => {
  it('below the sm breakpoint it is a modal dialog: focus trapped, page behind blocked', () => {
    const restore = atViewportWidth(375)
    try {
      openPanel()
      const dialog = screen.getByRole('dialog', { name: 'Run 42 details' })
      expect(dialog).toHaveAttribute('aria-modal', 'true')
      expect(dialog.parentElement).toHaveAttribute('role', 'presentation')
      expect(screen.queryByRole('complementary')).toBeNull()
      expect(dialog.contains(document.activeElement)).toBe(true)
    } finally {
      restore()
    }
  })

  it('on a wide screen it reserves its width on the page instead of overlaying it, and gives it back', () => {
    const restore = atViewportWidth(1280)
    function Reserving() {
      const [open, setOpen] = useState(false)
      const pageRef = useRef<HTMLDivElement>(null)
      return (
        <div ref={pageRef} data-testid="page" style={{ paddingRight: '16px' }}>
          <button type="button" onClick={() => setOpen((o) => !o)}>
            Toggle
          </button>
          <SidePanel open={open} title="Details" width={400} reserveSpaceIn={pageRef} onClose={() => setOpen(false)}>
            body
          </SidePanel>
        </div>
      )
    }
    try {
      render(<Reserving />)
      const page = screen.getByTestId('page')
      fireEvent.click(screen.getByRole('button', { name: 'Toggle' }))
      expect(screen.getByRole('complementary', { name: 'Details' })).toBeInTheDocument()
      // Its own padding plus the panel's width (jsdom folds the calc()).
      expect(page.style.paddingRight).toMatch(/^calc\((16px \+ 400px|416px)\)$/)
      fireEvent.click(screen.getByRole('button', { name: 'Toggle' }))
      expect(page.style.paddingRight).toBe('16px')
    } finally {
      restore()
    }
  })
})

describe('SidePanel — modal locks the page scroll (review fix 10)', () => {
  it('hides the body overflow while open and restores the exact inline styles on close', () => {
    document.body.style.overflow = 'scroll'
    document.body.style.paddingRight = '3px'
    // A 15 px classic scrollbar (jsdom has no layout: stand the width in).
    const clientWidth = vi
      .spyOn(document.documentElement, 'clientWidth', 'get')
      .mockReturnValue(window.innerWidth - 15)
    try {
      const { opener } = openPanel(true)
      expect(document.body.style.overflow).toBe('hidden')
      // The hidden scrollbar's width comes back as padding: nothing shifts.
      expect(document.body.style.paddingRight).toBe('18px')
      fireEvent.click(screen.getByRole('button', { name: 'Close panel' }))
      expect(screen.queryByRole('dialog')).toBeNull()
      expect(document.body.style.overflow).toBe('scroll')
      expect(document.body.style.paddingRight).toBe('3px')
      expect(opener).toHaveFocus()
    } finally {
      clientWidth.mockRestore()
      document.body.removeAttribute('style')
    }
  })
})

describe('SidePanel — modal', () => {
  it('is an aria-modal dialog on a presentation backdrop, named by its heading', () => {
    openPanel(true)
    const dialog = screen.getByRole('dialog', { name: 'Run 42 details' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog.parentElement).toHaveAttribute('role', 'presentation')
    expect(dialog.parentElement?.className).toMatch(/\bfixed inset-0\b/)
    expect(dialog.contains(document.activeElement)).toBe(true)
  })

  it('a press on the backdrop closes it; a press inside does not', () => {
    const { onClose, opener } = openPanel(true)
    const dialog = screen.getByRole('dialog')
    fireEvent.mouseDown(dialog)
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.mouseDown(dialog.parentElement as HTMLElement)
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(opener).toHaveFocus()
  })
})
