/**
 * The release editor and Link Run, in the page (W27 PAN-02): what their users
 * kept from the modal dialogs, minus the trap. Focus moves to the form's first
 * field when it opens; Tab and Shift+Tab are NOT trapped (the page stays
 * usable around the form); Escape inside closes it; focus goes back to the
 * control that opened it; the icon-only X keeps its accessible name.
 */
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

type FormFactory = (onClose: () => void) => ReactElement

const FORMS: Array<[string, string, string, FormFactory]> = [
  ['release editor', 'New Release', 'Close release dialog', (onClose) => (
    <ReleaseModal projectId="project-1" onClose={onClose} onSaved={vi.fn()} />
  )],
  ['link run', 'Link Test Run', 'Close link test run dialog', (onClose) => (
    <LinkRunModal releaseId="release-1" phases={[]} onClose={onClose} onSaved={vi.fn()} />
  )],
]

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]):not([type="hidden"]),select:not([disabled]),[tabindex]:not([tabindex="-1"])'

function Harness({ form }: { form: FormFactory }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>Open release form</button>
      {open && form(() => setOpen(false))}
      <button type="button">After the form</button>
    </>
  )
}

function openForm(form: FormFactory, name: string) {
  render(
    <MemoryRouter>
      <Harness form={form} />
    </MemoryRouter>,
  )
  const trigger = screen.getByRole('button', { name: 'Open release form' })
  trigger.focus()
  fireEvent.click(trigger)
  const section = screen.getByRole('region', { name })
  const focusable = Array.from(section.querySelectorAll<HTMLElement>(FOCUSABLE))
  return { trigger, section, focusable }
}

describe.each(FORMS)('%s in the page', (_label, name, closeLabel, form) => {
  it('takes focus, does not trap Tab, closes on Escape, and restores its trigger', () => {
    const { trigger, section, focusable } = openForm(form, name)
    // In the page: no dialog, no modal, no backdrop.
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(document.querySelector('[aria-modal]')).toBeNull()
    // Focus is on the form's first field.
    expect(section.querySelector('input, select, textarea')).toHaveFocus()
    expect(focusable.length).toBeGreaterThan(1)

    // Tab from the last control and Shift+Tab from the first are the
    // browser's: nothing prevents them, nothing moves focus back inside.
    const last = focusable[focusable.length - 1]
    last.focus()
    expect(fireEvent.keyDown(last, { key: 'Tab' }), 'Tab is not trapped').toBe(true)
    expect(last).toHaveFocus()
    focusable[0].focus()
    expect(fireEvent.keyDown(focusable[0], { key: 'Tab', shiftKey: true }), 'Shift+Tab is not trapped').toBe(true)
    expect(focusable[0]).toHaveFocus()

    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    expect(screen.queryByRole('region', { name })).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })

  it('gives its icon-only close control an accessible name', () => {
    const { section } = openForm(form, name)
    const close = screen.getByRole('button', { name: closeLabel })
    expect(section.contains(close)).toBe(true)
    expect(close).toBeVisible()
  })
})
