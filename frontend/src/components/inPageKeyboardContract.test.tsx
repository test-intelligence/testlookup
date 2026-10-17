/**
 * W27 PAN-01: every in-page container honours the same keyboard contract —
 * what the modals gave their users, minus the trap (owner: desktop only, no
 * new keyboard work, no regression):
 *   - focus is inside the container once it opens;
 *   - Tab from the last control is NOT prevented (the page goes on around
 *     the container: no trap);
 *   - Escape (focus inside) closes it, and focus goes back to the trigger;
 *   - no dialog role, no aria-modal, no portal: it renders in its parent.
 *
 * The three primitives first. The six forms modalKeyboardContract drove
 * through the TRAP contract until W27a (CorrectClassificationModal,
 * DefectPromotionModal, CreateJiraIssueModal, DefectIntakeModal,
 * ProposeQuarantineModal, UploadReportModal) join this list, rendered exactly
 * as that file rendered them, once they open in the page (W27a phase 2).
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { useState, type ReactElement } from 'react'
import { SWRConfig } from 'swr'
import { describe, expect, it, vi } from 'vitest'

import { DetailPane } from './ui/inPage/DetailPane'
import { InlineConfirm } from './ui/inPage/InlineConfirm'
import { InlineFormSection } from './ui/inPage/InlineFormSection'

type Render = (onClose: () => void) => ReactElement

const IN_PAGE: Array<[string, Render]> = [
  ['InlineFormSection', (onClose) => (
    <InlineFormSection
      title="Edit Release"
      closeLabel="Close release dialog"
      onClose={onClose}
      footer={(
        <>
          <button type="button" onClick={onClose}>Cancel</button>
          <button type="button">Save Changes</button>
        </>
      )}
    >
      <label>Name <input aria-label="Name" /></label>
      <label>Notes <textarea aria-label="Notes" /></label>
    </InlineFormSection>
  )],
  ['DetailPane', (onClose) => (
    <DetailPane title="2026.11" closeLabel="Close release" onClose={onClose} onToggleExpand={vi.fn()}
      footer={<a href="/releases/r-1">Open full page</a>}
    >
      <button type="button">Evaluate gate</button>
    </DetailPane>
  )],
  ['InlineConfirm', (onClose) => (
    <InlineConfirm
      message={<>Delete this release and all its phases? This cannot be undone.</>}
      confirmLabel="Delete"
      tone="danger"
      onConfirm={vi.fn()}
      onCancel={onClose}
    />
  )],
]

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]):not([type="hidden"]),select:not([disabled]),[tabindex]:not([tabindex="-1"])'

function Harness({ container, onClose }: { container: Render; onClose: () => void }) {
  const [open, setOpen] = useState(false)
  return (
    <div data-testid="page">
      <button type="button" onClick={() => setOpen(true)}>Open it</button>
      {open && container(() => {
        onClose()
        setOpen(false)
      })}
      <button type="button">After the container</button>
    </div>
  )
}

function open(container: Render) {
  const onClose = vi.fn()
  render(
    <SWRConfig value={{ provider: () => new Map() }}>
      <Harness container={container} onClose={onClose} />
    </SWRConfig>,
  )
  const trigger = screen.getByRole('button', { name: 'Open it' })
  trigger.focus()
  fireEvent.click(trigger)
  const root = document.querySelector<HTMLElement>('[data-in-page], [data-inline-confirm]')
  if (!root) throw new Error('no in-page container rendered')
  const items = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE))
  return { trigger, root, items, onClose }
}

function inPageContract(container: Render) {
  it('renders in the page: no dialog role, no aria-modal, inside its parent (no portal)', () => {
    const { root } = open(container)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.querySelector('[aria-modal]')).toBeNull()
    expect(screen.getByTestId('page')).toContainElement(root)
  })

  it('takes focus when it opens', () => {
    const { root } = open(container)
    expect(root.contains(document.activeElement)).toBe(true)
  })

  it('does not trap Tab: the key on the last control (and Shift+Tab on the first) is not prevented', () => {
    const { items } = open(container)
    expect(items.length).toBeGreaterThan(1)
    const first = items[0]
    const last = items[items.length - 1]

    last.focus()
    expect(fireEvent.keyDown(last, { key: 'Tab' })).toBe(true)
    expect(last).toHaveFocus()

    first.focus()
    expect(fireEvent.keyDown(first, { key: 'Tab', shiftKey: true })).toBe(true)
    expect(first).toHaveFocus()
  })

  it('closes on Escape (focus inside) and returns focus to its trigger', () => {
    const { trigger, onClose } = open(container)
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(document.querySelector('[data-in-page], [data-inline-confirm]')).toBeNull()
    expect(trigger).toHaveFocus()
  })
}

describe.each(IN_PAGE)('%s in-page keyboard contract', (_name, container) => {
  inPageContract(container)
})
