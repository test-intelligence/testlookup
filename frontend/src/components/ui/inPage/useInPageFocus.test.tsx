/**
 * W27 PAN-01: what an in-page container keeps of the modal's keyboard
 * behaviour — focus in on open, Escape inside closes, focus back to the
 * opener — and what it drops: the Tab trap.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { useState, type ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useInPageFocus, type InPageFocusOptions } from './useInPageFocus'

function Box({ options, children }: { options?: InPageFocusOptions; children?: ReactNode }) {
  const ref = useInPageFocus<HTMLDivElement>(options)
  return (
    <div ref={ref} data-testid="box">
      {children ?? (
        <>
          <button type="button">First button</button>
          <input aria-label="Name" />
          <button type="button">Last button</button>
        </>
      )}
    </div>
  )
}

function Harness({ options, children }: { options?: InPageFocusOptions; children?: ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>Opener</button>
      <button type="button">Elsewhere</button>
      <button type="button" onClick={() => setOpen(false)}>Close from outside</button>
      {open && (
        <Box options={{ onEscape: () => setOpen(false), ...options }}>
          {children}
        </Box>
      )}
    </>
  )
}

function openBox(options?: InPageFocusOptions, children?: ReactNode) {
  render(<Harness options={options}>{children}</Harness>)
  const opener = screen.getByRole('button', { name: 'Opener' })
  opener.focus()
  fireEvent.click(opener)
  return { opener, box: screen.getByTestId('box') }
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useInPageFocus', () => {
  it('focuses the first enabled FIELD on mount, not the first button', () => {
    openBox()
    expect(screen.getByRole('textbox', { name: 'Name' })).toHaveFocus()
  })

  it('falls back to the first focusable control when there is no field', () => {
    openBox(undefined, (
      <>
        <button type="button" disabled>Disabled</button>
        <button type="button">Only button</button>
      </>
    ))
    expect(screen.getByRole('button', { name: 'Only button' })).toHaveFocus()
  })

  it('leaves focus alone when something inside already took it (an autoFocus field)', () => {
    openBox(undefined, (
      <>
        <input aria-label="Name" />
        <textarea aria-label="Reason" autoFocus />
      </>
    ))
    expect(screen.getByRole('textbox', { name: 'Reason' })).toHaveFocus()
  })

  it('scrolls itself into view on mount when the browser can', () => {
    const scroll = vi.fn()
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: scroll })
    try {
      openBox()
      expect(scroll).toHaveBeenCalledWith({ block: 'nearest' })
    } finally {
      delete (HTMLElement.prototype as { scrollIntoView?: unknown }).scrollIntoView
    }
  })

  it('autoFocus false moves no focus', () => {
    const { opener } = openBox({ autoFocus: false })
    expect(opener).toHaveFocus()
  })

  it('Escape inside closes it and focus returns to the opener', () => {
    const { opener } = openBox()
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Name' }), { key: 'Escape' })
    expect(screen.queryByTestId('box')).toBeNull()
    expect(opener).toHaveFocus()
  })

  it('owns the key: preventDefault + stopPropagation, so a document listener never sees it', () => {
    const onDocument = vi.fn()
    document.addEventListener('keydown', onDocument)
    try {
      openBox()
      const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
      screen.getByRole('textbox', { name: 'Name' }).dispatchEvent(event)
      expect(event.defaultPrevented).toBe(true)
      expect(onDocument).not.toHaveBeenCalled()
    } finally {
      document.removeEventListener('keydown', onDocument)
    }
  })

  it('Escape outside the container does nothing', () => {
    openBox()
    fireEvent.keyDown(screen.getByRole('button', { name: 'Elsewhere' }), { key: 'Escape' })
    expect(screen.getByTestId('box')).toBeInTheDocument()
  })

  it('canEscape false swallows Escape and nothing closes (mid-submit)', () => {
    const onDocument = vi.fn()
    document.addEventListener('keydown', onDocument)
    try {
      openBox({ canEscape: false })
      fireEvent.keyDown(screen.getByRole('textbox', { name: 'Name' }), { key: 'Escape' })
      expect(screen.getByTestId('box')).toBeInTheDocument()
      expect(onDocument).not.toHaveBeenCalled()
    } finally {
      document.removeEventListener('keydown', onDocument)
    }
  })

  it('without onEscape it does not touch Escape at all (the host keeps its own handler)', () => {
    const onDocument = vi.fn()
    document.addEventListener('keydown', onDocument)
    try {
      render(<Box options={{}} />)
      fireEvent.keyDown(screen.getByRole('textbox', { name: 'Name' }), { key: 'Escape' })
      expect(onDocument).toHaveBeenCalledTimes(1)
      expect(onDocument.mock.calls[0][0].defaultPrevented).toBe(false)
    } finally {
      document.removeEventListener('keydown', onDocument)
    }
  })

  it('does not trap Tab: the key is never prevented on the last control', () => {
    openBox()
    const last = screen.getByRole('button', { name: 'Last button' })
    last.focus()
    expect(fireEvent.keyDown(last, { key: 'Tab' })).toBe(true)
    expect(fireEvent.keyDown(screen.getByRole('button', { name: 'First button' }), { key: 'Tab', shiftKey: true })).toBe(true)
  })

  it('a user who moved focus elsewhere keeps it there when the container closes', () => {
    openBox()
    const elsewhere = screen.getByRole('button', { name: 'Elsewhere' })
    elsewhere.focus()
    fireEvent.click(screen.getByRole('button', { name: 'Close from outside' }))
    expect(screen.queryByTestId('box')).toBeNull()
    expect(elsewhere).toHaveFocus()
  })

  it('restoreFocus false leaves focus where it fell', () => {
    const { opener } = openBox({ restoreFocus: false })
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Name' }), { key: 'Escape' })
    expect(opener).not.toHaveFocus()
  })
})
