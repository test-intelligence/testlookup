/**
 * W27 PAN-01: a confirmation asked where its action lives. The trigger sends
 * nothing; the strip's own button is the ONLY sender.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { InlineConfirm } from './InlineConfirm'

function Host({ name = 'Checkout 2.4', busy = false, onConfirm }: { name?: string; busy?: boolean; onConfirm: () => void }) {
  const [asking, setAsking] = useState(false)
  return (
    <>
      <button type="button" onClick={() => setAsking(true)}>Delete</button>
      {asking && (
        <InlineConfirm
          message={<>Remove phase <strong>{name}</strong>?</>}
          confirmLabel="Remove"
          tone="danger"
          busy={busy}
          onConfirm={onConfirm}
          onCancel={() => setAsking(false)}
          testId="phase-confirm"
        />
      )}
    </>
  )
}

function ask(props: Partial<Parameters<typeof Host>[0]> = {}) {
  const onConfirm = vi.fn()
  render(<Host onConfirm={onConfirm} {...props} />)
  const trigger = screen.getByRole('button', { name: 'Delete' })
  trigger.focus()
  fireEvent.click(trigger)
  return { onConfirm, trigger }
}

describe('InlineConfirm', () => {
  it('is a group named by its message; the trigger alone sends nothing', () => {
    const { onConfirm } = ask()
    const strip = screen.getByRole('group', { name: 'Remove phase Checkout 2.4?' })
    expect(strip).toHaveAttribute('data-inline-confirm', '')
    expect(strip).toHaveAttribute('data-testid', 'phase-confirm')
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('calls onConfirm exactly once, from its own confirm button', () => {
    const { onConfirm } = ask()
    const confirm = screen.getByRole('button', { name: 'Remove' })
    expect(confirm).toHaveAttribute('data-inline-confirm-action', '')
    fireEvent.click(confirm)
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('Cancel closes it and never confirms; focus goes back to the trigger', () => {
    const { onConfirm, trigger } = ask()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('group')).toBeNull()
    expect(onConfirm).not.toHaveBeenCalled()
    expect(trigger).toHaveFocus()
  })

  it('takes focus on Cancel when it appears', () => {
    ask()
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus()
  })

  it('Escape inside is Cancel', () => {
    const { onConfirm, trigger } = ask()
    fireEvent.keyDown(screen.getByRole('button', { name: 'Cancel' }), { key: 'Escape' })
    expect(screen.queryByRole('group')).toBeNull()
    expect(onConfirm).not.toHaveBeenCalled()
    expect(trigger).toHaveFocus()
  })

  it('busy disables both buttons and swallows Escape', () => {
    ask({ busy: true })
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Remove' })).toBeDisabled()
    fireEvent.keyDown(screen.getByRole('group'), { key: 'Escape' })
    expect(screen.getByRole('group')).toBeInTheDocument()
  })

  it('renders a hostile name as text, never as markup', () => {
    ask({ name: '<img src=x onerror=alert(1)>' })
    const strip = screen.getByRole('group')
    expect(strip.querySelector('img')).toBeNull()
    expect(strip.querySelector('strong')?.textContent).toBe('<img src=x onerror=alert(1)>')
  })

  it('a default tone uses the primary button', () => {
    render(<InlineConfirm message="Make it active?" confirmLabel="Make active" onConfirm={vi.fn()} onCancel={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Make active' })).toHaveClass('btn-primary')
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveClass('btn-secondary')
  })

  // W29a UIX-01 / UIX-07: the one destructive button (solid red + white on the
  // light themes); the old tint pill with red text is gone.
  it('a danger tone uses .btn-danger, never the tint pill', () => {
    render(<InlineConfirm message="Delete it?" confirmLabel="Delete" tone="danger" onConfirm={vi.fn()} onCancel={vi.fn()} />)
    const confirm = screen.getByRole('button', { name: 'Delete' })
    expect(confirm).toHaveClass('btn-danger')
    expect(confirm).not.toHaveClass('btn-primary')
    expect(confirm.className).not.toMatch(/--status-failed|rounded-full/)
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveClass('btn-secondary')
  })
})
