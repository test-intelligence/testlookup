import { fireEvent, render, screen } from '@testing-library/react'
import { useState, type ReactElement } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { LinkRunModal, ReleaseModal } from './ReleasesPage'

vi.mock('@/hooks/useReleases', () => ({
  useReleases: vi.fn(),
  useRelease: vi.fn(),
}))

vi.mock('@/hooks/useRuns', () => ({
  useRuns: () => ({ data: { items: [] } }),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: object) => unknown) => selector({
    activeProjectId: 'project-1',
    activeProject: { id: 'project-1', name: 'Project One' },
    projects: [{ id: 'project-1', name: 'Project One' }],
  })),
}))

type ModalFactory = (onClose: () => void) => ReactElement

const MODALS: Array<[string, ModalFactory]> = [
  ['release editor', (onClose) => (
    <ReleaseModal projectId="project-1" onClose={onClose} onSaved={vi.fn()} />
  )],
  ['link run', (onClose) => (
    <LinkRunModal releaseId="release-1" phases={[]} onClose={onClose} onSaved={vi.fn()} />
  )],
]

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]):not([type="hidden"]),select:not([disabled]),[tabindex]:not([tabindex="-1"])'

function Harness({ modal }: { modal: ModalFactory }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>Open release dialog</button>
      {open && modal(() => setOpen(false))}
    </>
  )
}

function openModal(modal: ModalFactory) {
  render(
    <MemoryRouter>
      <Harness modal={modal} />
    </MemoryRouter>,
  )
  const trigger = screen.getByRole('button', { name: 'Open release dialog' })
  trigger.focus()
  fireEvent.click(trigger)
  const dialog = screen.getByRole('dialog')
  const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE))
  return { trigger, dialog, focusable }
}

describe.each(MODALS)('%s modal accessibility', (_name, modal) => {
  it('takes focus, traps Tab, closes on Escape, and restores its trigger', () => {
    const { trigger, dialog, focusable } = openModal(modal)
    expect(dialog.contains(document.activeElement)).toBe(true)
    expect(focusable.length).toBeGreaterThan(1)

    focusable[0].focus()
    fireEvent.keyDown(focusable[0], { key: 'Tab', shiftKey: true })
    expect(focusable[focusable.length - 1]).toHaveFocus()

    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Tab' })
    expect(focusable[0]).toHaveFocus()

    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })

  it('gives its icon-only close control an accessible name', () => {
    openModal(modal)
    expect(screen.getByRole('button', { name: /close/i })).toBeVisible()
  })
})
