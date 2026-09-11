/**
 * M20: the keyboard contract of a modal, asserting where focus lands.
 *
 * jsdom does not move focus on Tab by itself; a browser does. So the tests
 * pin the part the hook owns: at the dialog's edges (and from outside it) Tab
 * is intercepted and focus wraps, while in the middle Tab is left to the
 * browser (the key event is NOT cancelled).
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { useModalFocus } from './useModalFocus'

function Dialog({ onClose, canClose = true }: { onClose: () => void; canClose?: boolean }) {
  const ref = useModalFocus({ onClose, canClose })
  return (
    <div ref={ref} role="dialog" aria-modal="true" aria-label="Example">
      <button type="button">First</button>
      <input aria-label="Middle" />
      <button type="button" disabled>Disabled</button>
      <button type="button" onClick={onClose}>Last</button>
    </div>
  )
}

function Page({ canClose = true, onClose }: { canClose?: boolean; onClose?: () => void }) {
  const [open, setOpen] = useState(false)
  const close = () => {
    onClose?.()
    setOpen(false)
  }
  return (
    <>
      <button type="button">Before</button>
      <button type="button" onClick={() => setOpen(true)}>Open</button>
      {open && <Dialog onClose={close} canClose={canClose} />}
    </>
  )
}

function openDialog(props: Parameters<typeof Page>[0] = {}) {
  render(<Page {...props} />)
  const opener = screen.getByRole('button', { name: 'Open' })
  opener.focus()
  fireEvent.click(opener)
}

/** Press Tab where focus is; returns whether the hook cancelled it. */
function tab(shift = false): boolean {
  const target = document.activeElement ?? document.body
  return !fireEvent.keyDown(target, { key: 'Tab', shiftKey: shift })
}

const button = (name: string) => screen.getByRole('button', { name })

describe('useModalFocus', () => {
  it('moves focus into the dialog when it opens', () => {
    openDialog()
    expect(button('First')).toHaveFocus()
  })

  it('Tab from the last control wraps to the first', () => {
    openDialog()
    button('Last').focus()
    expect(tab()).toBe(true)
    expect(button('First')).toHaveFocus()
  })

  it('Shift+Tab from the first control wraps to the last', () => {
    openDialog()
    expect(button('First')).toHaveFocus()
    expect(tab(true)).toBe(true)
    expect(button('Last')).toHaveFocus()
  })

  it('a plain Tab from the first control is NOT pulled to the last', () => {
    openDialog()
    expect(tab()).toBe(false)
    expect(button('First')).toHaveFocus()
  })

  it('Tab in the middle is left to the browser', () => {
    openDialog()
    screen.getByRole('textbox', { name: 'Middle' }).focus()
    expect(tab()).toBe(false)
    expect(tab(true)).toBe(false)
  })

  it('focus that escaped to the page is pulled back in', () => {
    openDialog()
    button('Before').focus()
    expect(tab()).toBe(true)
    expect(button('First')).toHaveFocus()
    button('Before').focus()
    expect(tab(true)).toBe(true)
    expect(button('Last')).toHaveFocus()
  })

  it('Escape closes it and focus returns to the opener', () => {
    const onClose = vi.fn()
    openDialog({ onClose })
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(button('Open')).toHaveFocus()
  })

  it('closing by its own button also returns focus to the opener', () => {
    openDialog()
    fireEvent.click(button('Last'))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(button('Open')).toHaveFocus()
  })

  it('keeps an autoFocus field focused, and still returns focus to the opener', () => {
    // autoFocus takes focus during commit, before effects run: the opener
    // must be the element focused BEFORE the dialog rendered, not the field.
    function AutoFocusDialog({ onClose }: { onClose: () => void }) {
      const ref = useModalFocus({ onClose })
      return (
        <div ref={ref} role="dialog" aria-modal="true" aria-label="Reason">
          <button type="button">Help</button>
          <textarea aria-label="Why" autoFocus />
        </div>
      )
    }
    function Harness() {
      const [open, setOpen] = useState(false)
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>Open reason</button>
          {open && <AutoFocusDialog onClose={() => setOpen(false)} />}
        </>
      )
    }
    render(<Harness />)
    const opener = button('Open reason')
    opener.focus()
    fireEvent.click(opener)
    expect(screen.getByRole('textbox', { name: 'Why' })).toHaveFocus()

    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(opener).toHaveFocus()
  })

  it('Escape does nothing while closing is not allowed', () => {
    const onClose = vi.fn()
    openDialog({ onClose, canClose: false })
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})

// ── QA-B45-3: nested dialogs; only the topmost one handles the keyboard ─────

function Outer({
  onOuterClose,
  onInnerClose,
  innerCanClose = true,
}: {
  onOuterClose: () => void
  onInnerClose: () => void
  innerCanClose?: boolean
}) {
  const [outerOpen, setOuterOpen] = useState(true)
  const [innerOpen, setInnerOpen] = useState(true)
  const ref = useModalFocus({
    onClose: () => {
      onOuterClose()
      setOuterOpen(false)
    },
  })
  if (!outerOpen) return null
  return (
    <div ref={ref} role="dialog" aria-label="Outer">
      <button type="button">Outer first</button>
      {innerOpen && (
        <Inner
          canClose={innerCanClose}
          onClose={() => {
            onInnerClose()
            setInnerOpen(false)
          }}
        />
      )}
      <button type="button">Outer last</button>
    </div>
  )
}

function Inner({ onClose, canClose }: { onClose: () => void; canClose: boolean }) {
  const ref = useModalFocus({ onClose, canClose })
  return (
    <div ref={ref} role="dialog" aria-label="Inner">
      <button type="button">Inner first</button>
      <button type="button">Inner last</button>
    </div>
  )
}

const escape = () => fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })

describe('useModalFocus with a dialog inside a dialog', () => {
  it('Escape closes only the inner dialog; the next Escape closes the outer', () => {
    const onOuterClose = vi.fn()
    const onInnerClose = vi.fn()
    render(<Outer onOuterClose={onOuterClose} onInnerClose={onInnerClose} />)
    button('Inner first').focus()

    escape()
    expect(onInnerClose).toHaveBeenCalledTimes(1)
    expect(onOuterClose).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog', { name: 'Inner' })).toBeNull()
    expect(screen.getByRole('dialog', { name: 'Outer' })).toBeInTheDocument()

    escape()
    expect(onOuterClose).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('dialog', { name: 'Outer' })).toBeNull()
  })

  it('a busy inner dialog swallows Escape: the outer does not close over it', () => {
    const onOuterClose = vi.fn()
    const onInnerClose = vi.fn()
    render(<Outer onOuterClose={onOuterClose} onInnerClose={onInnerClose} innerCanClose={false} />)
    button('Inner first').focus()

    escape()
    expect(onInnerClose).not.toHaveBeenCalled()
    expect(onOuterClose).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog', { name: 'Inner' })).toBeInTheDocument()
  })

  it('Tab is trapped by the inner dialog only', () => {
    render(<Outer onOuterClose={vi.fn()} onInnerClose={vi.fn()} />)
    button('Inner last').focus()
    expect(tab()).toBe(true)
    expect(button('Inner first')).toHaveFocus()
  })
})
